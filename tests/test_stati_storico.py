import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from core.classifier import Classifier
from core.sync import peso_conclusivo
from core.timezones import ROME


class DbFinto:
    def get_override(self, code):
        return None


class CoperturaStatiTests(unittest.TestCase):
    """Ogni testo che GLS ha davvero mandato deve trovare una regola: quelli
    scoperti finiscono in osservazione senza che nessuno se ne accorga."""

    # Campionatura dei testi realmente osservati in produzione.
    OSSERVATI = {
        "Consegnata.": ("NORMAL", "DELIVERED"),
        "Consegna prevista nel corso della giornata odierna.": ("NORMAL", "OUT_FOR_DELIVERY"),
        "Consegna concordata con il destinatario.": ("NORMAL", "SCHEDULED"),
        "Oggi non siamo riusciti a consegnare. La consegna e' prevista nel primo giorno lavorativo disponibile.":
            ("WATCH", "DELIVERY_RETRY"),
        "Non e' stato possibile consegnare a causa di un ritardo nel network. La consegna e' prevista nel primo giorno lavorativo disponibile.":
            ("WATCH", "DELIVERY_RETRY"),
        "Destinatario chiuso per turno. Consegna prevista per il giorno lavorativo successivo.":
            ("WATCH", "DELIVERY_RETRY"),
        "Destinatario chiuso per ferie. Consegna prevista alla data di riapertura.":
            ("WATCH", "RECIPIENT_CLOSED"),
        "Disponibile per il ritiro in Sede GLS.": ("WATCH", "PICKUP_AT_DEPOT"),
        "Richiesto il ritiro della spedizione presso la sede GLS destinataria.": ("WATCH", "PICKUP_AT_DEPOT"),
        "Si e' verificato un errore nel trasferimento dei dati della spedizione. Effettueremo un tentativo di consegna non appena possibile.":
            ("WATCH", "CARRIER_DELAY"),
        "Destinatario chiuso per cessata attivita', ti invitiamo a contattare il mittente.":
            ("CRITICAL", "ACTION_REQUIRED"),
        "Spedizione in giacenza presso la sede GLS, siamo in attesa di istruzioni.":
            ("CRITICAL", "STORAGE"),
    }

    def setUp(self):
        self.cl = Classifier(Path("config/rules.json"), DbFinto())
        self.recente = (datetime.now(ROME) - timedelta(hours=2)).isoformat()

    def test_nessuno_stato_osservato_resta_senza_regola(self):
        scoperti = [t for t in self.OSSERVATI
                    if self.cl.classify(code="", state=t, note="", event_at=self.recente).category == "UNCLASSIFIED"]
        self.assertEqual(scoperti, [])

    def test_ogni_stato_finisce_dove_ci_aspettiamo(self):
        for testo, (gravita, categoria) in self.OSSERVATI.items():
            with self.subTest(testo[:40]):
                esito = self.cl.classify(code="", state=testo, note="", event_at=self.recente)
                self.assertEqual((esito.severity, esito.category), (gravita, categoria))

    def test_un_tentativo_fallito_non_e_una_consegna_programmata(self):
        # Tutti nominano il giorno lavorativo successivo: e' la trappola in cui
        # finivano prima, comparendo verdi nel tab "In consegna".
        for testo in ("Oggi non siamo riusciti a consegnare. La consegna e' prevista nel primo giorno lavorativo disponibile.",
                      "Non e' stato possibile consegnare a causa di un evento imprevisto/ritardo sul mezzo. La consegna e' prevista nel primo giorno lavorativo disponibile.",
                      "Destinatario chiuso per turno. Consegna prevista per il giorno lavorativo successivo."):
            with self.subTest(testo[:40]):
                self.assertEqual(self.cl.classify(code="", state=testo, note="", event_at=self.recente).category,
                                 "DELIVERY_RETRY")

    def test_le_regole_del_file_sono_tutte_valide(self):
        regole = json.loads(Path("config/rules.json").read_text(encoding="utf-8"))["rules"]
        identificativi = [r["id"] for r in regole]
        self.assertEqual(len(identificativi), len(set(identificativi)), "identificativi duplicati")
        for r in regole:
            self.assertTrue(r.get("patterns"), f"{r['id']} senza testi da cercare")
            self.assertIn(r["severity"], {"NORMAL", "INFO", "WATCH", "WARNING", "CRITICAL"})


class OrdineStessoMinutoTests(unittest.TestCase):
    """Nel dato gia' registrato l'ordine di lettura originale e' perduto: a
    parita' di minuto vale la sostanza dello stato."""

    def test_un_esito_pesa_piu_di_un_annuncio(self):
        self.assertGreater(peso_conclusivo("DELIVERED"), peso_conclusivo("OUT_FOR_DELIVERY"))
        self.assertGreater(peso_conclusivo("RETURN"), peso_conclusivo("IN_TRANSIT"))
        self.assertGreater(peso_conclusivo("STORAGE"), peso_conclusivo("SCHEDULED"))

    def test_una_categoria_sconosciuta_non_scavalca_nulla(self):
        self.assertEqual(peso_conclusivo("BOH"), 1)
        self.assertEqual(peso_conclusivo(None), 1)
