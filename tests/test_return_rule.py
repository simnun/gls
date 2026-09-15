"""Riconoscimento del rientro al mittente dal codice di reso GLS.

Quando GLS pubblica "Traccia il reso con codice: R..." la spedizione sta gia'
tornando indietro: e' un esito finale, non un'anomalia da lavorare. Prima
vinceva la regola di anomalia e la pratica restava in DA VERIFICARE invece di
comparire fra i RIENTRATI.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.classifier import Classifier  # noqa: E402
from core.db import Database  # noqa: E402

FINALI = {"DELIVERED", "RETURN"}


class RegolaRientroTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.sqlite3")
        self.c = Classifier(ROOT / "config" / "rules.json", self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def classifica(self, stato, nota=""):
        return self.c.classify(code="", state=stato, note=nota,
                               event_at="2026-09-11T14:24:00+02:00")

    def test_codice_di_reso_significa_rientro(self):
        r = self.classifica(
            "Abbiamo restituito la spedizione al mittente, ti invitiamo a contattare il mittente.",
            "Traccia il reso con codice: R5260164769")
        self.assertEqual(r.category, "RETURN")

    def test_il_numero_del_reso_e_indifferente(self):
        for codice in ("R5260164769", "R1", "R99999999999"):
            r = self.classifica("Abbiamo restituito la spedizione al mittente.",
                                f"Traccia il reso con codice: {codice}")
            self.assertEqual(r.category, "RETURN", codice)

    def test_il_rientro_e_un_esito_finale(self):
        # Da qui discende la comparsa nel tab RIENTRATI: le pratiche finali
        # vengono chiuse e tolte dalle code operative.
        r = self.classifica("Abbiamo restituito la spedizione al mittente.",
                            "Traccia il reso con codice: R5260164769")
        self.assertIn(r.category, FINALI)

    def test_il_rientro_prevale_sull_anomalia(self):
        # Un rifiuto che ha gia' prodotto un reso non e' piu' da sbloccare.
        r = self.classifica(
            "La spedizione e' stata rifiutata. Ti invitiamo a contattare il mittente.",
            "Traccia il reso con codice: R5260164770")
        self.assertEqual(r.category, "RETURN")

    def test_un_rifiuto_senza_reso_resta_da_lavorare(self):
        # Senza codice di reso la spedizione e' ferma e richiede un intervento:
        # non deve sparire fra i rientrati.
        r = self.classifica(
            "La spedizione e' stata rifiutata. Ti invitiamo a contattare il mittente.")
        self.assertEqual(r.category, "REFUSED")
        self.assertNotIn(r.category, FINALI)

    def test_le_altre_formule_di_rientro_restano_valide(self):
        for stato in ("In restituzione al mittente", "Rientro al mittente",
                      "Reso al mittente", "Ritorno al mittente"):
            self.assertEqual(self.classifica(stato).category, "RETURN", stato)

    def test_gli_altri_stati_non_cambiano(self):
        atteso = {
            "Consegnata al destinatario": "DELIVERED",
            "In giacenza presso la sede GLS": "STORAGE",
            "Partita dalla sede mittente. In transito.": "IN_TRANSIT",
        }
        for stato, categoria in atteso.items():
            self.assertEqual(self.classifica(stato).category, categoria, stato)

    def test_una_consegna_prevale_sulle_anomalie(self):
        # Stessa logica del rientro: un esito finale conclude la pratica.
        r = self.classifica("Consegnata al destinatario")
        self.assertEqual(r.category, "DELIVERED")


if __name__ == "__main__":
    unittest.main()
