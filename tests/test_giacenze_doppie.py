"""Una giacenza svincolata non deve comparire due volte nello storico.

Il caso: GLS mostra la giacenza sul canale pubblico (senza codice evento),
l'operatore manda lo svincolo, poi lo stesso evento arriva dal canale
ufficiale con il codice. L'evento cambia impronta, la giacenza viene
ricostruita con l'impronta nuova e quella vecchia, che porta lo svincolo,
restava nello storico come una seconda giacenza "da verificare".
"""

import tempfile
import unittest
from pathlib import Path

from core.db import Database

GIACENZA = ("Spedizione in giacenza presso la sede GLS, ti invitiamo a contattare "
            "il mittente per programmare la riconsegna")


class GiacenzeDoppieTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "db.sqlite3")
        self.db.upsert_shipment({
            "tracking_number": "NI1", "order_name": "#1", "customer_name": "Cliente",
            "city": "Napoli", "province": "NA", "total_amount": 10.0, "currency": "EUR",
            "payment_gateways": [], "is_cod": False, "gls_status": GIACENZA, "gls_note": "",
            "severity": "CRITICAL", "category": "STORAGE", "closed": False, "workflow_status": "NEW",
        })

    def tearDown(self):
        self.tmp.cleanup()

    def evento(self, impronta, codice=""):
        self.db.insert_event({
            "tracking_number": "NI1", "event_hash": impronta, "event_at": "2026-11-03T09:30:00+01:00",
            "code": codice, "state": GIACENZA, "category": "STORAGE", "severity": "CRITICAL",
        })
        self.db.reconcile_stock_cases("NI1")

    def giacenze(self):
        with self.db.connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM stock_cases WHERE tracking_number='NI1'")]

    def svincola(self):
        return self.db.record_gls_release_request(
            tracking_number="NI1", release_type="1", release_label="Riconsegna allo stesso indirizzo",
            operator_name="Daniela", note="", request_data={}, gls_success=True,
            gls_result="OK", gls_raw_response="")

    def test_lo_stesso_evento_dal_canale_ufficiale_non_raddoppia_la_giacenza(self):
        self.evento("pubblico")                  # letto dal canale pubblico, senza codice
        self.assertEqual(len(self.giacenze()), 1)
        self.svincola()
        self.evento("ufficiale", codice="72")    # lo stesso evento, ora con il codice
        giacenze = self.giacenze()
        self.assertEqual(len(giacenze), 1, giacenze)
        # Lo svincolo resta attaccato all'unica giacenza rimasta.
        with self.db.connect() as conn:
            collegati = conn.execute("SELECT stock_case_id FROM gls_release_requests").fetchall()
        self.assertEqual([r["stock_case_id"] for r in collegati], [giacenze[0]["id"]])
        storico = self.db.stock_history()
        self.assertEqual(storico["summary"]["total"], 1)
        self.assertTrue(storico["cases"][0]["handled"])

    def test_la_manutenzione_ripulisce_i_doppioni_gia_registrati(self):
        self.evento("pubblico")
        self.svincola()
        # Il doppione com'era prima della correzione: la vecchia riga chiusa
        # con lo svincolo, accanto alla giacenza vera.
        with self.db.connect() as conn:
            conn.execute("UPDATE stock_cases SET entry_event_hash='vecchia', status='CLOSED',"
                         " outcome_state='Episodio confluito nella giacenza in corso'")
        # Lo stesso evento con il codice, e la giacenza ricostruita accanto,
        # scritti com'erano prima della correzione (senza ricostruire).
        self.db.insert_event({
            "tracking_number": "NI1", "event_hash": "ufficiale", "event_at": "2026-11-03T09:30:00+01:00",
            "code": "72", "state": GIACENZA, "category": "STORAGE", "severity": "CRITICAL",
        })
        with self.db.connect() as conn:
            conn.execute("""INSERT INTO stock_cases(tracking_number, entry_event_hash, entered_at, entry_code,
                            entry_state, status, created_at, updated_at)
                            VALUES('NI1','ufficiale','2026-11-03T09:30:00+01:00','72',?,'OPEN','x','x')""", (GIACENZA,))
        self.assertEqual(len(self.giacenze()), 2)   # il doppione com'era nello storico
        self.assertEqual(self.db.unisci_giacenze_doppie(), 1)
        giacenze = self.giacenze()
        self.assertEqual(len(giacenze), 1)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT stock_case_id FROM gls_release_requests").fetchone()[0],
                             giacenze[0]["id"])


if __name__ == "__main__":
    unittest.main()
