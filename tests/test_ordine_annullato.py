import tempfile
import unittest
from pathlib import Path

from core.db import Database
from core.sync import motivo_spedizione_decaduta


class SpedizioneMaiPartitaTests(unittest.TestCase):
    """Un'etichetta creata e poi lasciata li' non ricevera' mai un evento da
    GLS: resterebbe in coda per sempre in attesa di un ritiro che non arriva."""

    def test_ordine_annullato_prima_del_ritiro(self):
        motivo = motivo_spedizione_decaduta({
            "gls_event_at": None, "order_cancelled_at": "2026-09-24T12:48:55Z"})
        self.assertIsNotNone(motivo)
        self.assertEqual(motivo["categoria"], "ORDER_CANCELLED")

    def test_collo_affidato_a_un_altro_corriere(self):
        motivo = motivo_spedizione_decaduta({
            "gls_event_at": None, "replaced_by_carrier": "BRT"})
        self.assertEqual(motivo["categoria"], "REPLACED_CARRIER")
        self.assertIn("BRT", motivo["stato"])

    def test_un_collo_gia_in_viaggio_non_si_tocca(self):
        # L'annullamento arriva spesso dopo, a reso concluso: quella storia
        # e' reale e va conservata com'e'.
        self.assertIsNone(motivo_spedizione_decaduta({
            "gls_event_at": "2026-09-20T10:00:00+02:00",
            "order_cancelled_at": "2026-09-24T12:48:55Z"}))
        self.assertIsNone(motivo_spedizione_decaduta({
            "gls_event_at": "2026-09-20T10:00:00+02:00",
            "replaced_by_carrier": "BRT"}))

    def test_una_spedizione_normale_resta_in_coda(self):
        self.assertIsNone(motivo_spedizione_decaduta({"gls_event_at": None}))
        self.assertIsNone(motivo_spedizione_decaduta(
            {"gls_event_at": None, "order_cancelled_at": "", "replaced_by_carrier": ""}))


class CampiConservatiTests(unittest.TestCase):
    def test_il_database_conserva_annullamento_e_corriere(self):
        db = Database(path=Path(tempfile.mkdtemp()) / "t.db")
        db.upsert_shipment({"tracking_number": "NI1",
                            "order_cancelled_at": "2026-09-24T12:48:55Z",
                            "replaced_by_carrier": "BRT"})
        riga = db.get_shipment("NI1")
        self.assertEqual(riga["order_cancelled_at"], "2026-09-24T12:48:55Z")
        self.assertEqual(riga["replaced_by_carrier"], "BRT")


class EstrazioneShopifyTests(unittest.TestCase):
    """Quello che Shopify racconta dell'ordine deve arrivare fino alla spedizione."""

    def setUp(self):
        from types import SimpleNamespace

        from core.shopify import ShopifyClient
        self.client = ShopifyClient(SimpleNamespace(
            shopify_configured=True, shopify_shop="x", shopify_client_id="a",
            shopify_client_secret="b", shopify_api_version="2025-01",
            shopify_lookback_days=21, gls_accept_unlabeled_tracking=False,
            request_timeout_seconds=5))

    def ordine(self, **extra):
        base = {
            "id": "gid://shopify/Order/1", "name": "#1", "cancelledAt": None,
            "fulfillments": [{
                "id": "f1", "status": "SUCCESS", "createdAt": "2026-09-24T12:36:52Z",
                "trackingInfo": [{"company": "GLS Italy", "number": "NI1", "url": ""}],
            }],
        }
        base.update(extra)
        return base

    def test_riporta_l_annullamento_dell_ordine(self):
        righe = self.client.extract_gls_shipments([self.ordine(cancelledAt="2026-09-24T12:48:55Z")])
        self.assertEqual(righe[0]["order_cancelled_at"], "2026-09-24T12:48:55Z")

    def test_una_spedizione_annullata_viene_ignorata(self):
        ordine = self.ordine()
        ordine["fulfillments"][0]["status"] = "CANCELLED"
        self.assertEqual(self.client.extract_gls_shipments([ordine]), [])

    def test_riconosce_il_corriere_subentrato(self):
        ordine = self.ordine()
        ordine["fulfillments"].append({
            "id": "f2", "status": "SUCCESS", "createdAt": "2026-09-25T09:00:00Z",
            "trackingInfo": [{"company": "BRT", "number": "BRT9", "url": ""}],
        })
        righe = self.client.extract_gls_shipments([ordine])
        self.assertEqual(len(righe), 1)
        self.assertEqual(righe[0]["replaced_by_carrier"], "BRT")

    def test_un_altro_corriere_precedente_non_conta(self):
        # Se la spedizione con l'altro corriere e' anteriore, quella GLS e'
        # l'ultima scelta e resta valida.
        ordine = self.ordine()
        ordine["fulfillments"].append({
            "id": "f0", "status": "SUCCESS", "createdAt": "2026-09-23T09:00:00Z",
            "trackingInfo": [{"company": "BRT", "number": "BRT9", "url": ""}],
        })
        self.assertEqual(self.client.extract_gls_shipments([ordine])[0]["replaced_by_carrier"], "")
