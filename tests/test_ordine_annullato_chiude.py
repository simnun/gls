"""Un ordine annullato su Shopify chiude la pratica.

Se l'ordine non esiste piu', la spedizione non ha piu' uno scopo: il collo
torna indietro da solo e non c'e' niente da decidere. L'unica eccezione e' la
giacenza non ancora svincolata, dove non fare niente costa giorni di deposito.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.db import Database
from core.timezones import ROME

GIACENZA = ("Spedizione in giacenza presso la sede GLS, ti invitiamo a contattare "
            "il mittente per programmare la riconsegna")


class OrdineAnnullatoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "db.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def spedizione(self, numero, categoria="IN_TRANSIT", stato="In transito",
                   annullato=True, chiusa=False):
        self.db.upsert_shipment({
            "tracking_number": numero, "order_name": "#" + numero,
            "order_cancelled_at": "2026-10-10T09:00:00Z" if annullato else None,
            "customer_name": "Cliente", "city": "Cannara", "province": "PG",
            "total_amount": 29.95, "currency": "EUR", "payment_gateways": [],
            "is_cod": True, "gls_status": stato, "gls_note": "", "severity": "WATCH",
            "category": categoria, "closed": chiusa, "workflow_status": "NEW",
            "gls_event_at": datetime.now(ROME).isoformat(timespec="seconds"),
        })

    def test_chiude_la_pratica_qualunque_cosa_stia_facendo_il_collo(self):
        self.spedizione("NI1", "IN_TRANSIT", "In transito")
        self.spedizione("NI2", "RETURN", "Abbiamo restituito la spedizione al mittente")
        chiuse = self.db.chiudi_ordini_annullati()
        self.assertEqual({c["tracking_number"] for c in chiuse}, {"NI1", "NI2"})
        for numero in ("NI1", "NI2"):
            riga = self.db.get_shipment(numero)
            self.assertTrue(riga["closed"], numero)
            self.assertIn("annullato", riga["reason"])
        # L'esito di GLS resta quello vero: la pratica si chiude, non si riscrive.
        self.assertEqual(self.db.get_shipment("NI2")["category"], "RETURN")
        self.assertEqual(self.db.get_shipment("NI1")["category"], "IN_TRANSIT")

    def test_non_tocca_gli_ordini_validi(self):
        self.spedizione("NI3", annullato=False)
        self.assertEqual(self.db.chiudi_ordini_annullati(), [])
        self.assertFalse(self.db.get_shipment("NI3")["closed"])

    def test_la_giacenza_da_svincolare_resta_in_coda(self):
        # Qui chiudere costerebbe: il collo resta in sede finche' qualcuno non
        # chiede a GLS di riportarlo indietro.
        self.spedizione("NI4", "STORAGE", GIACENZA)
        self.db.insert_event({
            "tracking_number": "NI4", "event_hash": "g4",
            "event_at": (datetime.now(ROME) - timedelta(days=2)).isoformat(timespec="seconds"),
            "code": "", "state": GIACENZA, "note": "In attesa di istruzioni dal mittente",
            "location": "Foligno", "severity": "CRITICAL", "category": "STORAGE",
            "reason": "", "raw": {},
        })
        self.db.reconcile_stock_cases("NI4")
        self.assertEqual(self.db.chiudi_ordini_annullati(), [])
        self.assertFalse(self.db.get_shipment("NI4")["closed"])

    def test_la_giacenza_gia_svincolata_invece_si_chiude(self):
        self.spedizione("NI5", "STORAGE", GIACENZA)
        self.db.insert_event({
            "tracking_number": "NI5", "event_hash": "g5",
            "event_at": (datetime.now(ROME) - timedelta(days=2)).isoformat(timespec="seconds"),
            "code": "", "state": GIACENZA, "note": "In attesa di istruzioni dal mittente",
            "location": "Foligno", "severity": "CRITICAL", "category": "STORAGE",
            "reason": "", "raw": {},
        })
        self.db.reconcile_stock_cases("NI5")
        self.db.record_gls_release_request(
            tracking_number="NI5", release_type="2", release_label="Rientro al mittente",
            operator_name="Daniela", note="", request_data={"release_type": "2"},
            gls_success=True, gls_result="Accettata", gls_raw_response="",
        )
        chiuse = self.db.chiudi_ordini_annullati()
        self.assertEqual([c["tracking_number"] for c in chiuse], ["NI5"])
        self.assertTrue(self.db.get_shipment("NI5")["closed"])


if __name__ == "__main__":
    unittest.main()
