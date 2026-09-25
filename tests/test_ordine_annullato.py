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


class TrackingSostituitoTests(unittest.TestCase):
    """Su Shopify il numero si riscrive sulla stessa spedizione: quando il collo
    passa a un altro corriere il tracking GLS sparisce dall'ordine, e da noi
    resterebbe in coda in attesa di eventi che non arriveranno mai."""

    def setUp(self):
        from types import SimpleNamespace

        from core.shopify import ShopifyClient
        self.client = ShopifyClient(SimpleNamespace(
            shopify_configured=True, shopify_shop="x", shopify_client_id="a",
            shopify_client_secret="b", shopify_api_version="2025-01",
            shopify_lookback_days=21, gls_accept_unlabeled_tracking=False,
            request_timeout_seconds=5))
        self.db = Database(path=Path(tempfile.mkdtemp()) / "t.db")

    def ordine_con_poste(self):
        # Com'e' ridotto #278700 dopo la modifica: una sola spedizione, con
        # dentro il numero del corriere subentrato.
        return [{
            "id": "gid://shopify/Order/9", "name": "#278700",
            "fulfillments": [{
                "id": "f1", "status": "SUCCESS", "createdAt": "2026-09-23T09:21:51Z",
                "trackingInfo": [{"company": "Poste Italiane", "number": "D76937I003660",
                                  "url": "https://www.poste.it/"}],
            }],
        }]

    def test_l_ordine_non_porta_piu_il_numero_gls(self):
        corrente = self.client.tracking_correnti(self.ordine_con_poste())
        dati = corrente["gid://shopify/Order/9"]
        self.assertEqual(dati["numeri"], {"D76937I003660"})
        self.assertEqual(dati["corrieri_non_gls"], ["Poste Italiane"])
        self.assertNotIn("NI665000000", dati["numeri"])

    def test_la_spedizione_gls_viene_ritrovata_dall_ordine(self):
        self.db.upsert_shipment({"tracking_number": "NI665000000",
                                 "order_gid": "gid://shopify/Order/9", "closed": False})
        gruppi = self.db.spedizioni_per_ordine(["gid://shopify/Order/9"])
        self.assertEqual([s["tracking_number"] for s in gruppi["gid://shopify/Order/9"]],
                         ["NI665000000"])

    def test_le_spedizioni_gia_concluse_non_si_ripescano(self):
        self.db.upsert_shipment({"tracking_number": "NI1",
                                 "order_gid": "gid://shopify/Order/9", "closed": True})
        self.assertEqual(self.db.spedizioni_per_ordine(["gid://shopify/Order/9"]), {})

    def test_un_elenco_vuoto_non_interroga_il_database(self):
        self.assertEqual(self.db.spedizioni_per_ordine([]), {})


class ChiusuraDurantelaSincronizzazioneTests(unittest.TestCase):
    """La chiusura deve avvenire durante la sincronizzazione, non a mano."""

    class ShopifyFinto:
        def __init__(self, ordini):
            self.ordini = ordini

        def list_recent_orders(self, since_iso=None):
            return self.ordini

        def extract_gls_shipments(self, orders):
            return []

        def ordini_per_id(self, gids):
            # Il client vero rilegge gli ordini indicati: qui li ha gia' tutti.
            return [o for o in self.ordini if o.get("id") in set(gids)]

        def tracking_correnti(self, orders):
            from core.shopify import ShopifyClient
            return ShopifyClient.tracking_correnti(orders)

    class GlsFinto:
        def track(self, tracking):
            raise AssertionError("una spedizione sostituita non va interrogata a GLS")

    def configurazione(self):
        from types import SimpleNamespace
        return SimpleNamespace(
            rules_path=Path(__file__).resolve().parents[1] / "config" / "rules.json",
            gls_public_workers=2, gls_retry_attempts=1, mock_mode=False,
            shopify_configured=True, gls_tracking_configured=True, sync_workers=2)

    def test_la_sincronizzazione_chiude_il_tracking_sparito(self):
        from core.sync import SyncEngine

        db = Database(path=Path(tempfile.mkdtemp()) / "t.db")
        db.upsert_shipment({"tracking_number": "NI665000000", "order_name": "#278700",
                            "order_gid": "gid://shopify/Order/9", "closed": False})
        motore = SyncEngine(self.configurazione(), db)
        motore.shopify = self.ShopifyFinto([{
            "id": "gid://shopify/Order/9", "name": "#278700",
            "fulfillments": [{"id": "f1", "status": "SUCCESS", "createdAt": "2026-09-23T09:21:51Z",
                              "trackingInfo": [{"company": "Poste Italiane",
                                                "number": "D76937I003660", "url": ""}]}],
        }])
        motore.gls = self.GlsFinto()
        motore.run_sync()

        riga = db.get_shipment("NI665000000")
        self.assertTrue(riga["closed"])
        self.assertEqual(riga["category"], "REPLACED_CARRIER")
        self.assertEqual(riga["replaced_by_carrier"], "Poste Italiane")
        self.assertIn("Poste Italiane", riga["gls_status"])

    def test_un_collo_gia_partito_non_viene_chiuso(self):
        from core.sync import SyncEngine

        db = Database(path=Path(tempfile.mkdtemp()) / "t.db")
        db.upsert_shipment({"tracking_number": "NI665000001", "order_gid": "gid://shopify/Order/9",
                            "gls_event_at": "2026-09-22T10:00:00+02:00", "closed": False})
        motore = SyncEngine(self.configurazione(), db)
        motore.shopify = self.ShopifyFinto([{
            "id": "gid://shopify/Order/9",
            "fulfillments": [{"id": "f1", "status": "SUCCESS", "createdAt": "2026-09-23T09:21:51Z",
                              "trackingInfo": [{"company": "Poste Italiane",
                                                "number": "D769", "url": ""}]}],
        }])
        motore.gls = self.GlsFinto()
        chiuse = motore._chiudi_tracking_sostituiti(motore.shopify.list_recent_orders())
        self.assertEqual(chiuse, 0)
        self.assertFalse(db.get_shipment("NI665000001")["closed"])


class OrdiniFuoriFinestraTests(unittest.TestCase):
    """Il tracking puo' essere stato cambiato giorni fa: per data quell'ordine
    non tornerebbe piu' a tiro, e la spedizione resterebbe in coda per sempre."""

    def setUp(self):
        self.db = Database(path=Path(tempfile.mkdtemp()) / "t.db")

    def test_elenca_gli_ordini_delle_spedizioni_ferme(self):
        self.db.upsert_shipment({"tracking_number": "NI1", "order_gid": "gid://Order/1",
                                 "closed": False})
        self.db.upsert_shipment({"tracking_number": "NI2", "order_gid": "gid://Order/2",
                                 "gls_event_at": "2026-09-20T10:00:00+02:00", "closed": False})
        self.db.upsert_shipment({"tracking_number": "NI3", "order_gid": "gid://Order/3",
                                 "closed": True})
        # Solo la prima: la seconda ha viaggiato, la terza e' gia' conclusa.
        self.assertEqual(self.db.ordini_da_ricontrollare(), ["gid://Order/1"])

    def test_una_spedizione_senza_ordine_non_si_puo_ricontrollare(self):
        self.db.upsert_shipment({"tracking_number": "NI9", "closed": False})
        self.assertEqual(self.db.ordini_da_ricontrollare(), [])
