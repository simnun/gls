import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.db import Database


class V27WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.tmp.cleanup()

    def upsert(self, tracking, *, category, status, event_at, closed=False, severity="CRITICAL"):
        self.db.upsert_shipment({
            "tracking_number": tracking,
            "order_name": "#276157",
            "order_created_at": event_at,
            "customer_name": "Desiree Gambino",
            "customer_phone": "3331234567",
            "gls_status": status,
            "gls_note": "",
            "gls_code": "",
            "gls_event_at": event_at,
            "severity": severity,
            "category": category,
            "closed": closed,
        })

    def event(self, tracking, when, category, state):
        self.db.insert_event({
            "tracking_number": tracking,
            "event_hash": f"{tracking}-{when}-{category}-{state}",
            "event_at": when,
            "code": "",
            "state": state,
            "note": "",
            "location": "Napoli",
            "severity": "CRITICAL" if category in {"RETURN", "STORAGE", "ADDRESS_ERROR"} else "NORMAL",
            "category": category,
            "reason": "",
            "raw": {},
        })

    def test_manual_resolution_removes_final_return_from_inconsistencies(self):
        now = datetime.now(timezone.utc)
        t0 = (now - timedelta(hours=4)).isoformat()
        t1 = (now + timedelta(hours=1)).isoformat()
        t2 = (now + timedelta(hours=2)).isoformat()
        self.upsert("RET1", category="RETURN", status="Rientro al mittente", event_at=t2, closed=True)
        self.event("RET1", t0, "STORAGE", "Spedizione in giacenza")
        self.db.reconcile_stock_cases("RET1")
        self.db.record_gls_release_request(
            tracking_number="RET1",
            release_type="1",
            release_label="Riconsegna allo stesso indirizzo",
            operator_name="Simone",
            note="cliente conferma",
            request_data={},
            gls_success=True,
            gls_result="OK",
            gls_raw_response="<ok/>",
        )
        self.event("RET1", t1, "RETURN", "Rientro al mittente")
        issues = self.db.reconcile_inconsistencies("RET1")
        self.assertTrue(any(x["issue_code"] == "REDELIVERY_BECAME_RETURN" for x in issues))

        self.db.update_workflow("RET1", "RESOLVED", "Verificato: pratica conclusa", "Simone")
        issues_after = self.db.reconcile_inconsistencies("RET1")
        self.assertEqual(issues_after, [])
        self.assertEqual(self.db.list_inconsistencies(active_only=True), [])

        item = self.db.get_shipment("RET1")
        self.assertEqual(item["workflow_status"], "RESOLVED")
        self.assertEqual(item["category"], "RETURN")
        self.assertTrue(item["closed"])

        dashboard = self.db.dashboard(include_closed=True)
        row = next(x for x in dashboard["shipments"] if x["tracking_number"] == "RET1")
        self.assertFalse(row["has_inconsistency"])
        self.assertEqual(row["category"], "RETURN")

    def test_same_tracking_state_does_not_reopen_manually_closed_case(self):
        old = datetime.now(timezone.utc) - timedelta(hours=30)
        self.upsert("OPEN1", category="ADDRESS_ERROR", status="Indirizzo errato", event_at=old.isoformat(), closed=False)
        self.event("OPEN1", old.isoformat(), "ADDRESS_ERROR", "Indirizzo errato")
        self.assertTrue(self.db.reconcile_inconsistencies("OPEN1"))
        self.db.update_workflow("OPEN1", "RESOLVED", "Gestita", "Simone")

        # Stesso stato GLS / stesso evento: la chiusura dell'operatore resta valida.
        self.upsert("OPEN1", category="ADDRESS_ERROR", status="Indirizzo errato", event_at=old.isoformat(), closed=False)
        self.assertEqual(self.db.reconcile_inconsistencies("OPEN1"), [])
        self.assertEqual(self.db.get_shipment("OPEN1")["workflow_status"], "RESOLVED")

    def test_new_problematic_tracking_revision_can_reopen_case(self):
        old = datetime.now(timezone.utc) - timedelta(hours=48)
        self.upsert("OPEN2", category="ADDRESS_ERROR", status="Indirizzo errato", event_at=old.isoformat(), closed=False)
        self.event("OPEN2", old.isoformat(), "ADDRESS_ERROR", "Indirizzo errato")
        self.assertTrue(self.db.reconcile_inconsistencies("OPEN2"))
        self.db.update_workflow("OPEN2", "RESOLVED", "Gestita", "Simone")

        # Nuovo evento GLS successivo: deve poter riaprire la pratica se resta anomalo.
        newer = datetime.now(timezone.utc) - timedelta(hours=30)
        self.upsert("OPEN2", category="ADDRESS_ERROR", status="Indirizzo errato - contattare mittente", event_at=newer.isoformat(), closed=False)
        self.event("OPEN2", newer.isoformat(), "ADDRESS_ERROR", "Indirizzo errato - contattare mittente")
        issues = self.db.reconcile_inconsistencies("OPEN2")
        self.assertTrue(issues)
        self.assertEqual(self.db.get_shipment("OPEN2")["workflow_status"], "NEW")


if __name__ == "__main__":
    unittest.main()
