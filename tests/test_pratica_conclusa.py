import tempfile
import unittest
from pathlib import Path

from core.db import Database


class PraticaChiusaSenzaEventiTests(unittest.TestCase):
    """Su un tracking che GLS non ha mai mosso, la chiusura dell'operatore e'
    l'unica conclusione possibile: nessun evento arrivera' a dichiararla finita."""

    def setUp(self):
        self.db = Database(path=Path(tempfile.mkdtemp()) / "t.db")

    def spedizione(self, tracking, *, con_eventi):
        self.db.upsert_shipment({
            "tracking_number": tracking, "closed": False,
            "gls_event_at": "2026-09-22T10:00:00+02:00" if con_eventi else None,
            "gls_status": "In transito." if con_eventi else "Tracking GLS senza eventi",
        })

    def test_chiudere_la_pratica_conclude_la_spedizione_mai_partita(self):
        self.spedizione("NI1", con_eventi=False)
        self.db.update_workflow("NI1", "RESOLVED", None, "Daniela")
        self.assertTrue(self.db.get_shipment("NI1")["closed"])

    def test_un_collo_in_viaggio_resta_fra_le_attive(self):
        # La pratica e' chiusa da noi, la spedizione no: il collo si muove ancora.
        self.spedizione("NI2", con_eventi=True)
        self.db.update_workflow("NI2", "RESOLVED", None, "Daniela")
        riga = self.db.get_shipment("NI2")
        self.assertEqual(riga["workflow_status"], "RESOLVED")
        self.assertFalse(riga["closed"])

    def test_riaprire_la_pratica_rimette_la_spedizione_fra_le_attive(self):
        self.spedizione("NI3", con_eventi=False)
        self.db.update_workflow("NI3", "RESOLVED", None, "Daniela")
        self.db.update_workflow("NI3", "IN_PROGRESS", None, "Daniela")
        self.assertFalse(self.db.get_shipment("NI3")["closed"])

    def test_una_consegna_non_viene_riaperta_da_un_cambio_di_pratica(self):
        # Qui a chiudere era stato l'esito GLS, non la mano dell'operatore.
        self.db.upsert_shipment({"tracking_number": "NI4", "closed": True,
                                 "gls_event_at": "2026-09-22T10:00:00+02:00",
                                 "category": "DELIVERED"})
        self.db.update_workflow("NI4", "IN_PROGRESS", None, "Daniela")
        self.assertTrue(self.db.get_shipment("NI4")["closed"])

    def test_il_recupero_sistema_quelle_gia_chiuse(self):
        self.spedizione("NI5", con_eventi=False)
        with self.db.connect() as conn:
            conn.execute("UPDATE shipments SET workflow_status='RESOLVED' WHERE tracking_number='NI5'")
        self.assertEqual(self.db.concludi_pratiche_chiuse_senza_eventi(), 1)
        self.assertTrue(self.db.get_shipment("NI5")["closed"])
