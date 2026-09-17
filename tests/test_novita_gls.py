import tempfile
import unittest
from pathlib import Path

from core.db import Database


class NovitaGLSTests(unittest.TestCase):
    """Una pratica presa in carico non torna a DA VERIFICARE da sola:
    la novita' viene segnalata e si spegne quando l'operatore interviene."""

    def setUp(self):
        self.db = Database(path=Path(tempfile.mkdtemp()) / "t.db")
        self.db.upsert_shipment({"tracking_number": "NI1"})
        self.db.update_workflow("NI1", "IN_PROGRESS", "rimessa in consegna il 21/09", "Simone")

    def test_la_novita_viene_segnata_senza_toccare_la_lavorazione(self):
        self.db.segna_novita_gls("NI1", "2026-09-17T07:03:00+02:00")
        riga = self.db.get_shipment("NI1")
        self.assertEqual(riga["workflow_status"], "IN_PROGRESS")
        self.assertEqual(riga["unread_event_at"], "2026-09-17T07:03:00+02:00")

    def test_un_intervento_dell_operatore_spegne_l_avviso(self):
        self.db.segna_novita_gls("NI1", "2026-09-17T07:03:00+02:00")
        self.db.add_operator_action("NI1", "NOTE", "Nota operativa", "richiamato il cliente", "Daniela")
        self.assertIsNone(self.db.get_shipment("NI1")["unread_event_at"])

    def test_una_riga_di_sistema_non_vale_come_presa_visione(self):
        self.db.segna_novita_gls("NI1", "2026-09-17T07:03:00+02:00")
        self.db.update_workflow("NI1", "WAITING_GLS", None, "Sistema")
        self.assertEqual(self.db.get_shipment("NI1")["unread_event_at"], "2026-09-17T07:03:00+02:00")

    def test_la_nota_dell_operatore_spegne_l_avviso(self):
        self.db.segna_novita_gls("NI1", "2026-09-17T07:03:00+02:00")
        self.db.update_workflow("NI1", "IN_PROGRESS", "sentito GLS, riconsegna lunedi", "Simone")
        self.assertIsNone(self.db.get_shipment("NI1")["unread_event_at"])
