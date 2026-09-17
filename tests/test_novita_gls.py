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


class RipristinoLavorazioniTests(unittest.TestCase):
    """Le pratiche riportate indietro dal sistema tornano in lavorazione,
    quelle mai prese in carico restano dove sono."""

    def setUp(self):
        self.db = Database(path=Path(tempfile.mkdtemp()) / "t.db")

    def pratica(self, tracking):
        self.db.upsert_shipment({"tracking_number": tracking})

    def test_recupera_la_pratica_riportata_indietro(self):
        self.pratica("NI1")
        self.db.update_workflow("NI1", "IN_PROGRESS", "in attesa di GLS", "Daniela")
        self.db.update_workflow("NI1", "NEW", None, "Sistema")
        self.assertEqual(len(self.db.ripristina_lavorazioni_annullate()), 1)
        self.assertEqual(self.db.get_shipment("NI1")["workflow_status"], "IN_PROGRESS")

    def test_non_tocca_chi_non_e_mai_stato_preso_in_carico(self):
        self.pratica("NI2")
        self.db.update_workflow("NI2", "NEW", None, "Sistema")
        self.assertEqual(self.db.ripristina_lavorazioni_annullate(), [])
        self.assertEqual(self.db.get_shipment("NI2")["workflow_status"], "NEW")

    def test_rispetta_chi_ha_riaperto_la_pratica_di_persona(self):
        self.pratica("NI3")
        self.db.update_workflow("NI3", "IN_PROGRESS", "presa in carico", "Simone")
        self.db.update_workflow("NI3", "NEW", None, "Simone")
        self.assertEqual(self.db.ripristina_lavorazioni_annullate(), [])
        self.assertEqual(self.db.get_shipment("NI3")["workflow_status"], "NEW")

    def test_conserva_lo_stato_di_attesa_scelto_dall_operatore(self):
        self.pratica("NI4")
        self.db.update_workflow("NI4", "WAITING_GLS", "svincolo inviato", "Aldo")
        self.db.update_workflow("NI4", "NEW", None, "Sistema")
        self.db.ripristina_lavorazioni_annullate()
        self.assertEqual(self.db.get_shipment("NI4")["workflow_status"], "WAITING_GLS")


class RipristinoDaAzioniTests(unittest.TestCase):
    """Chi prende in carico una pratica di solito fa un'azione concreta,
    non cambia lo stato a mano: anche quel lavoro va riconosciuto."""

    def setUp(self):
        self.db = Database(path=Path(tempfile.mkdtemp()) / "t.db")
        self.db.upsert_shipment({"tracking_number": "NI1"})

    def test_una_telefonata_al_cliente_vale_come_presa_in_carico(self):
        self.db.add_operator_action("NI1", "CUSTOMER_CALLED", "Cliente contattato",
                                    "", "Daniela", workflow_status="IN_PROGRESS")
        self.db.update_workflow("NI1", "NEW", None, "Sistema")
        self.assertEqual(len(self.db.ripristina_lavorazioni_annullate()), 1)
        self.assertEqual(self.db.get_shipment("NI1")["workflow_status"], "IN_PROGRESS")

    def test_una_pratica_chiusa_dall_operatore_non_viene_riaperta(self):
        self.db.add_operator_action("NI1", "CUSTOMER_CALLED", "Cliente contattato",
                                    "", "Daniela", workflow_status="IN_PROGRESS")
        self.db.update_workflow("NI1", "RESOLVED", None, "Daniela")
        self.db.update_workflow("NI1", "NEW", None, "Sistema")
        self.assertEqual(self.db.ripristina_lavorazioni_annullate(), [])


class UltimaAttivitaTests(unittest.TestCase):
    """In elenco la colonna dell'ultima attivita' deve dire cosa ha fatto una
    persona: i passaggi di stato del sistema non sono lavoro di nessuno."""

    def setUp(self):
        self.db = Database(path=Path(tempfile.mkdtemp()) / "t.db")
        self.db.upsert_shipment({"tracking_number": "NI1"})

    def test_il_sistema_non_diventa_l_ultima_attivita(self):
        self.db.add_operator_action("NI1", "CUSTOMER_CALLED", "Cliente chiamato",
                                    "", "Daniela", workflow_status="IN_PROGRESS")
        self.db.update_workflow("NI1", "NEW", None, "Sistema")
        riga = self.db.get_shipment("NI1")
        self.assertEqual(riga["last_operator_action"], "Cliente chiamato")
        self.assertEqual(riga["last_operator_name"], "Daniela")

    def test_il_recupero_riporta_indietro_i_casi_gia_sporcati(self):
        self.db.add_operator_action("NI1", "CUSTOMER_CALLED", "Cliente chiamato",
                                    "", "Daniela", workflow_status="IN_PROGRESS")
        with self.db.connect() as conn:
            conn.execute("UPDATE shipments SET last_operator_action=?, last_operator_name='Sistema'"
                         " WHERE tracking_number='NI1'", ("Stato pratica: NEW → IN_PROGRESS",))
        self.assertEqual(self.db.ricalcola_ultima_attivita(), 1)
        riga = self.db.get_shipment("NI1")
        self.assertEqual(riga["last_operator_action"], "Cliente chiamato")
        self.assertEqual(riga["last_operator_name"], "Daniela")

    def test_senza_interventi_umani_la_colonna_resta_vuota(self):
        self.db.update_workflow("NI1", "IN_PROGRESS", None, "Sistema")
        with self.db.connect() as conn:
            conn.execute("UPDATE shipments SET last_operator_name='Sistema' WHERE tracking_number='NI1'")
        self.db.ricalcola_ultima_attivita()
        self.assertIsNone(self.db.get_shipment("NI1")["last_operator_action"])
