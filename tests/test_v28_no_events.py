import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from core.db import Database
from core.gls import GLSError
from core.sync import SyncEngine, no_events_pickup_state, ROME


class FakeShopify:
    def __init__(self, shipments):
        self.shipments = shipments
    def list_recent_orders(self, since_iso=None):
        return [{"id": "order"}]
    def extract_gls_shipments(self, orders):
        return list(self.shipments)
    def tracking_correnti(self, orders):
        # Il vero client dice quali numeri portano adesso gli ordini letti.
        return {}


class NoEventsGLS:
    def track(self, tracking):
        raise GLSError(
            "Tracking GLS non disponibile dopo 1 tentativo/i. "
            "XML: endpoint XML temporaneamente escluso dopo errore DNS; "
            "fallback pubblico: GLS non ha restituito eventi per questo numero spedizione"
        )


class V28NoEventsTests(unittest.TestCase):
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

    @staticmethod
    def shipment(tracking, created_at):
        return {
            "tracking_number": tracking,
            "order_name": "#100",
            "order_created_at": created_at,
            "fulfillment_created_at": created_at,
            "fulfillment_updated_at": created_at,
            "customer_name": "Cliente Test",
            "customer_phone": "3330000000",
            "is_cod": False,
        }

    def test_same_day_no_events_is_pending_pickup_not_error(self):
        fixed_now = datetime(2026, 9, 14, 16, 30, tzinfo=ROME)
        created = datetime(2026, 9, 14, 10, 0, tzinfo=ROME)
        state = no_events_pickup_state(self.shipment("NEW", created.isoformat()), now=fixed_now)
        self.assertTrue(state["pending"])

    def test_previous_business_day_after_noon_is_attention(self):
        fixed_now = datetime(2026, 9, 15, 13, 0, tzinfo=ROME)
        created = datetime(2026, 9, 14, 10, 0, tzinfo=ROME)
        state = no_events_pickup_state(self.shipment("OLD", created.isoformat()), now=fixed_now)
        self.assertFalse(state["pending"])

    def test_friday_creation_is_graceful_until_monday_noon(self):
        fixed_now = datetime(2026, 9, 14, 10, 0, tzinfo=ROME)  # Monday
        created = datetime(2026, 9, 11, 17, 0, tzinfo=ROME)    # Friday
        state = no_events_pickup_state(self.shipment("WEEKEND", created.isoformat()), now=fixed_now)
        self.assertTrue(state["pending"])
        after = no_events_pickup_state(
            self.shipment("WEEKEND", created.isoformat()),
            now=datetime(2026, 9, 14, 12, 1, tzinfo=ROME),
        )
        self.assertFalse(after["pending"])

    def test_sync_separates_pending_pickup_from_stale_no_events(self):
        now = datetime.now(timezone.utc)
        fresh = self.shipment("FRESH", now.isoformat())
        stale = self.shipment("STALE", (now - timedelta(days=5)).isoformat())
        engine = SyncEngine(self.cfg, self.db)
        engine.shopify = FakeShopify([fresh, stale])
        engine.gls = NoEventsGLS()

        result = engine.run_sync()
        self.assertTrue(result["ok"])
        self.assertEqual(result["gls_errors"], 0)
        self.assertEqual(result["pending_pickup"], 1)
        self.assertEqual(result["no_event_attention"], 1)

        fresh_row = self.db.get_shipment("FRESH")
        stale_row = self.db.get_shipment("STALE")
        self.assertEqual(fresh_row["category"], "PENDING_PICKUP")
        self.assertEqual(fresh_row["severity"], "INFO")
        self.assertEqual(stale_row["category"], "NO_GLS_EVENTS")
        self.assertEqual(stale_row["severity"], "WARNING")
        self.assertEqual(stale_row["workflow_status"], "NEW")

        last = self.db.latest_sync()
        self.assertEqual(last["gls_errors"], 0)
        self.assertEqual(last["pending_pickup"], 1)
        self.assertEqual(last["no_event_attention"], 1)
        self.assertEqual(last["error_summary"], [])


if __name__ == "__main__":
    unittest.main()
