import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.db import Database
from core.sync import SyncEngine, technical_error_info
from core.gls import GLSError


class FakeShopify:
    def __init__(self, shipments):
        self.shipments = shipments
    def list_recent_orders(self, since_iso=None):
        return [{"id": "order"}]
    def extract_gls_shipments(self, orders):
        return list(self.shipments)


class FakeGLS:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or set()
    def track(self, tracking):
        self.calls.append(tracking)
        if tracking in self.fail:
            raise GLSError("Tracking GLS non disponibile dopo 3 tentativo/i. XML: Errore rete GLS: [Errno 8] nodename nor servname provided; fallback pubblico: GLS HTTP 429: Too Many Requests")
        event = {
            "event_at": datetime.now(timezone.utc).isoformat(),
            "code": "",
            "state": "In transito",
            "note": "",
            "location": "Napoli",
        }
        return {"status": "In transito", "events": [event], "current_event": event}
    @staticmethod
    def event_hash(tracking, event):
        return f"{tracking}-{event.get('event_at')}-{event.get('state')}"


class V26SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(__file__).resolve().parents[1]
        self.db = Database(Path(self.tmp.name) / "test.db")
        self.cfg = SimpleNamespace(
            rules_path=self.root / "config" / "rules.json",
            gls_public_workers=2,
            gls_retry_attempts=3,
            mock_mode=False,
            shopify_configured=True,
            gls_tracking_configured=True,
            sync_workers=2,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, tracking, *, closed, category="IN_TRANSIT"):
        self.db.upsert_shipment({
            "tracking_number": tracking,
            "order_name": "#1",
            "order_created_at": datetime.now(timezone.utc).isoformat(),
            "customer_name": "Test",
            "customer_phone": "3331234567",
            "gls_status": "Consegnata" if closed else "In transito",
            "gls_note": "",
            "gls_event_at": datetime.now(timezone.utc).isoformat(),
            "severity": "NORMAL",
            "category": category,
            "closed": closed,
        })

    def shipment(self, tracking):
        return {
            "tracking_number": tracking,
            "order_name": "#2",
            "order_created_at": datetime.now(timezone.utc).isoformat(),
            "customer_name": "Nuovo",
            "customer_phone": "3330000000",
            "is_cod": False,
        }

    def test_closed_shipments_are_not_polled_again_and_old_open_are_kept(self):
        self.add("CLOSED", closed=True, category="DELIVERED")
        self.add("OLDOPEN", closed=False)
        engine = SyncEngine(self.cfg, self.db)
        engine.shopify = FakeShopify([self.shipment("CLOSED"), self.shipment("NEW")])
        fake_gls = FakeGLS()
        engine.gls = fake_gls

        result = engine.run_sync()
        self.assertTrue(result["ok"])
        self.assertEqual(result["skipped_closed"], 1)
        self.assertEqual(result["carried_open"], 1)
        self.assertEqual(set(fake_gls.calls), {"NEW", "OLDOPEN"})
        self.assertNotIn("CLOSED", fake_gls.calls)

    def test_sync_errors_are_explained_and_persisted(self):
        engine = SyncEngine(self.cfg, self.db)
        engine.shopify = FakeShopify([self.shipment("FAIL")])
        fake_gls = FakeGLS(fail={"FAIL"})
        engine.gls = fake_gls
        result = engine.run_sync()
        self.assertEqual(result["gls_errors"], 1)
        last = self.db.latest_sync()
        self.assertEqual(last["error_summary"][0]["error_code"], "RATE_LIMIT")
        self.assertIn("limitando", last["error_summary"][0]["error_title"].lower())
        self.assertEqual(last["error_samples"][0]["tracking_number"], "FAIL")

    def test_error_classifier_prioritizes_public_fallback_reason(self):
        info = technical_error_info("XML: [Errno 8] nodename nor servname; fallback pubblico: GLS non ha restituito eventi per questo numero spedizione")
        self.assertEqual(info["code"], "NO_EVENTS")


if __name__ == "__main__":
    unittest.main()
