import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from core.classifier import Classifier
from core.db import Database
from core.timezones import ROME


class DbFinto:
    def get_override(self, code):
        return None


class TentativoFallitoTests(unittest.TestCase):
    """Un primo tentativo andato a vuoto non e' una consegna in corso:
    nomina il primo giorno lavorativo ma il collo non e' in viaggio."""

    TESTO = ("Non siamo riusciti a raggiungerti al primo tentativo di consegna, "
             "ritenteremo la consegna nel primo giorno lavorativo disponibile.")

    def setUp(self):
        self.cl = Classifier(Path("config/rules.json"), DbFinto())

    def test_non_viene_letto_come_consegna_programmata(self):
        quando = (datetime.now(ROME) - timedelta(hours=2)).isoformat()
        esito = self.cl.classify(code="1", state=self.TESTO, note="", event_at=quando, is_cod=False)
        self.assertEqual(esito.category, "DELIVERY_RETRY")
        self.assertEqual(esito.severity, "WATCH")

    def test_la_consegna_programmata_vera_resta_tale(self):
        quando = (datetime.now(ROME) - timedelta(hours=2)).isoformat()
        esito = self.cl.classify(code="", state="Consegna prevista per il giorno lavorativo successivo.",
                                 note="", event_at=quando, is_cod=False)
        self.assertEqual(esito.category, "SCHEDULED")

    def test_se_il_secondo_tentativo_non_arriva_torna_a_galla(self):
        mercoledi = datetime.now(ROME) - timedelta(days=9)
        while mercoledi.weekday() != 2:
            mercoledi -= timedelta(days=1)
        esito = self.cl.classify(code="1", state=self.TESTO, note="",
                                 event_at=mercoledi.isoformat(), is_cod=False)
        self.assertEqual(esito.severity, "CRITICAL")


class AnnullaAzioneTests(unittest.TestCase):
    """Si cancella solo l'ultima operazione registrata, e la pratica torna a
    valere per quello che dice lo storico rimasto."""

    def setUp(self):
        self.db = Database(path=Path(tempfile.mkdtemp()) / "t.db")
        self.db.upsert_shipment({"tracking_number": "NI1"})

    def azione(self, tipo, etichetta, operatore="Simone"):
        return self.db.add_operator_action("NI1", tipo, etichetta, "", operatore,
                                           workflow_status="IN_PROGRESS")

    def test_cancellare_l_unica_azione_riporta_la_pratica_allo_stato_gls(self):
        a = self.azione("CUSTOMER_CALLED", "Cliente contattato")
        esito = self.db.elimina_azione_operatore("NI1", a["id"])
        self.assertEqual(esito["workflow_status"], "NEW")
        riga = self.db.get_shipment("NI1")
        self.assertEqual(riga["workflow_status"], "NEW")
        self.assertIsNone(riga["last_operator_action"])

    def test_resta_in_lavorazione_se_prima_c_era_un_altro_intervento(self):
        self.azione("CUSTOMER_MESSAGE", "Messaggio inviato al cliente", "Daniela")
        seconda = self.azione("CUSTOMER_CALLED", "Cliente contattato")
        self.db.elimina_azione_operatore("NI1", seconda["id"])
        riga = self.db.get_shipment("NI1")
        self.assertEqual(riga["workflow_status"], "IN_PROGRESS")
        self.assertEqual(riga["last_operator_action"], "Messaggio inviato al cliente")
        self.assertEqual(riga["last_operator_name"], "Daniela")

    def test_non_si_puo_cancellare_un_operazione_in_mezzo(self):
        prima = self.azione("CUSTOMER_MESSAGE", "Messaggio inviato al cliente")
        self.azione("CUSTOMER_CALLED", "Cliente contattato")
        with self.assertRaises(ValueError):
            self.db.elimina_azione_operatore("NI1", prima["id"])

    def test_non_si_puo_cancellare_l_azione_di_un_altra_spedizione(self):
        self.db.upsert_shipment({"tracking_number": "NI2"})
        a = self.azione("CUSTOMER_CALLED", "Cliente contattato")
        with self.assertRaises(ValueError):
            self.db.elimina_azione_operatore("NI2", a["id"])


class PraticaChiusaDalMovimentoTests(unittest.TestCase):
    """Quando GLS rimette il collo in viaggio il lavoro dell'operatore ha
    prodotto il suo effetto: la pratica non ha piu' bisogno di una persona."""

    def setUp(self):
        self.db = Database(path=Path(tempfile.mkdtemp()) / "t.db")
        self.db.upsert_shipment({"tracking_number": "NI1"})

    def test_la_chiusura_spegne_anche_l_avviso_di_novita(self):
        self.db.update_workflow("NI1", "IN_PROGRESS", "svincolo inviato", "Daniela")
        self.db.segna_novita_gls("NI1", "2026-09-17T07:03:00+02:00")
        self.db.update_workflow("NI1", "RESOLVED", None, "Sistema")
        riga = self.db.get_shipment("NI1")
        self.assertEqual(riga["workflow_status"], "RESOLVED")
        self.assertIsNone(riga["unread_event_at"])

    def test_un_nuovo_guaio_puo_riaprire_la_pratica(self):
        self.db.update_workflow("NI1", "RESOLVED", None, "Sistema")
        self.db.update_workflow("NI1", "NEW", None, "Sistema")
        self.assertEqual(self.db.get_shipment("NI1")["workflow_status"], "NEW")


class AnnullaSaltandoIlSistemaTests(unittest.TestCase):
    """Il sistema scrive i suoi passaggi di stato ma non compare nello storico:
    la croce resta sull'ultima operazione fatta da una persona."""

    def setUp(self):
        self.db = Database(path=Path(tempfile.mkdtemp()) / "t.db")
        self.db.upsert_shipment({"tracking_number": "NI1"})

    def test_si_cancella_anche_se_il_sistema_ha_scritto_dopo(self):
        azione = self.db.add_operator_action("NI1", "CUSTOMER_CALLED", "Cliente contattato",
                                             "", "Daniela", workflow_status="IN_PROGRESS")
        self.db.update_workflow("NI1", "RESOLVED", None, "Sistema")
        esito = self.db.elimina_azione_operatore("NI1", azione["id"])
        self.assertEqual(esito["workflow_status"], "NEW")

    def test_le_righe_del_sistema_non_si_possono_cancellare(self):
        self.db.update_workflow("NI1", "IN_PROGRESS", None, "Sistema")
        with self.db.connect() as conn:
            riga = conn.execute("SELECT id FROM operator_actions ORDER BY id DESC LIMIT 1").fetchone()
        with self.assertRaises(ValueError):
            self.db.elimina_azione_operatore("NI1", riga["id"])
