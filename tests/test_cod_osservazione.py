import unittest
from datetime import datetime, timedelta
from pathlib import Path

from core.classifier import Classifier, scadenza_giorni_lavorativi
from core.timezones import ROME


class DbFinto:
    def get_override(self, code):
        return None


class ContrassegnoNonDisponibileTests(unittest.TestCase):
    """GLS riprova da sola il primo giorno lavorativo: e' da osservare, non da verificare."""

    TESTO = ("Importo contrassegno non disponibile. La consegna e' prevista "
             "nel primo giorno lavorativo disponibile.")

    def setUp(self):
        self.cl = Classifier(Path("config/rules.json"), DbFinto())

    def classifica(self, quando):
        return self.cl.classify(code="", state=self.TESTO, note="",
                                event_at=quando.isoformat(), is_cod=True)

    def test_appena_arrivato_finisce_in_osservazione(self):
        esito = self.classifica(datetime.now(ROME) - timedelta(hours=2))
        self.assertEqual(esito.severity, "WATCH")
        self.assertEqual(esito.category, "COD_ISSUE")

    def test_dopo_un_giorno_lavorativo_senza_consegna_torna_da_verificare(self):
        # Mercoledi' scorso alle 11:53: il giovedi' e' passato senza consegna.
        mercoledi = datetime.now(ROME) - timedelta(days=9)
        while mercoledi.weekday() != 2:
            mercoledi -= timedelta(days=1)
        self.assertEqual(self.classifica(mercoledi).severity, "CRITICAL")

    def test_evento_del_venerdi_non_diventa_urgente_nel_weekend(self):
        venerdi = datetime(2026, 9, 11, 16, 30, tzinfo=ROME)
        sabato = scadenza_giorni_lavorativi(venerdi, 1)
        # La scadenza cade il lunedi', non il sabato.
        self.assertEqual(sabato.weekday(), 0)
        self.assertEqual(sabato.date(), datetime(2026, 9, 14).date())

    def test_seconda_scadenza_conta_due_giorni_lavorativi(self):
        venerdi = datetime(2026, 9, 11, 16, 30, tzinfo=ROME)
        self.assertEqual(scadenza_giorni_lavorativi(venerdi, 2).date(),
                         datetime(2026, 9, 15).date())
