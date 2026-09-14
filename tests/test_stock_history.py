import tempfile
import unittest
from pathlib import Path

from core.db import Database


class StockHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "db.sqlite3")
        self.db.upsert_shipment({
            "tracking_number": "NI123456789",
            "order_name": "#1001",
            "order_created_at": "2026-09-10T10:00:00+00:00",
            "customer_name": "Mario Rossi",
            "customer_phone": "3330000000",
            "city": "Napoli",
            "province": "NA",
            "total_amount": 49.9,
            "currency": "EUR",
            "payment_gateways": [],
            "is_cod": True,
            "gls_status": "Consegnata",
            "gls_note": "",
            "severity": "NORMAL",
            "category": "DELIVERED",
            "closed": True,
        })

    def tearDown(self):
        self.tmp.cleanup()

    def test_backfills_stock_case_and_outcome(self):
        self.db.insert_event({
            "tracking_number": "NI123456789", "event_hash": "stock1",
            "event_at": "2026-09-11T09:00:00+02:00", "code": "", "state": "Spedizione in giacenza",
            "note": "In attesa di istruzioni", "location": "Napoli", "severity": "CRITICAL",
            "category": "STORAGE", "reason": "", "raw": {},
        })
        self.db.insert_event({
            "tracking_number": "NI123456789", "event_hash": "move1",
            "event_at": "2026-09-12T08:00:00+02:00", "code": "", "state": "Consegna prevista nel corso della giornata",
            "note": "", "location": "Napoli", "severity": "NORMAL",
            "category": "OUT_FOR_DELIVERY", "reason": "", "raw": {},
        })
        self.db.insert_event({
            "tracking_number": "NI123456789", "event_hash": "del1",
            "event_at": "2026-09-12T12:00:00+02:00", "code": "", "state": "Consegnata",
            "note": "", "location": "Napoli", "severity": "NORMAL",
            "category": "DELIVERED", "reason": "", "raw": {},
        })
        self.db.reconcile_stock_cases("NI123456789")
        data = self.db.stock_history()
        self.assertEqual(data["summary"]["total"], 1)
        self.assertEqual(data["cases"][0]["status"], "CLOSED")
        self.assertEqual(data["cases"][0]["outcome_category"], "DELIVERED")
        self.assertEqual(data["cases"][0]["outcome_label"], "Consegnata")

    def test_release_request_is_kept_in_history(self):
        self.db.insert_event({
            "tracking_number": "NI123456789", "event_hash": "stock1",
            "event_at": "2026-09-11T09:00:00+02:00", "code": "", "state": "Spedizione in giacenza",
            "note": "In attesa di istruzioni", "location": "Napoli", "severity": "CRITICAL",
            "category": "STORAGE", "reason": "", "raw": {},
        })
        self.db.reconcile_stock_cases("NI123456789")
        self.db.record_gls_release_request(
            tracking_number="NI123456789", release_type="1", release_label="Riconsegna allo stesso indirizzo",
            operator_name="Simone", note="Cliente conferma indirizzo", request_data={"release_type":"1"},
            gls_success=True, gls_result="Ok", gls_raw_response="<Risultati />",
        )
        data = self.db.stock_history()
        case = data["cases"][0]
        self.assertTrue(case["handled"])
        self.assertEqual(case["latest_instruction"]["operator_name"], "Simone")
        self.assertEqual(case["latest_instruction"]["release_type"], "1")
        self.assertEqual(data["summary"]["handled"], 1)


if __name__ == "__main__":
    unittest.main()
