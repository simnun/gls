import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from core.db import Database
from core.sync import SyncEngine


class V29NoEventsCaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / 'monitor.sqlite3')
        cfg = SimpleNamespace(rules_path=Path('config/rules.json'))
        # _save_no_events only needs classifier/db; avoid constructing full engine clients.
        self.engine = object.__new__(SyncEngine)
        self.engine.db = self.db

    def tearDown(self):
        self.tmp.cleanup()

    def stale(self):
        return {
            'tracking_number': 'N1860078575',
            'tracking_company': 'GLS',
            'order_gid': 'gid://shopify/Order/123',
            'order_legacy_id': '123',
            'order_name': '#276999',
            'order_created_at': (datetime.now(timezone.utc)-timedelta(days=4)).isoformat(),
            'fulfillment_created_at': (datetime.now(timezone.utc)-timedelta(days=3)).isoformat(),
            'customer_gid': 'gid://shopify/Customer/55',
            'customer_legacy_id': '55',
            'customer_name': 'Cliente Test',
            'customer_phone': '+393331234567',
            'city': 'Napoli',
            'province': 'NA',
            'is_cod': False,
        }

    def test_stale_no_events_is_order_linked_and_closeable(self):
        outcome = SyncEngine._save_no_events(self.engine, self.stale())
        self.assertEqual(outcome, 'NO_EVENT_ATTENTION')
        row = self.db.get_shipment('N1860078575')
        self.assertEqual(row['order_name'], '#276999')
        self.assertEqual(row['customer_name'], 'Cliente Test')
        self.assertEqual(row['category'], 'NO_GLS_EVENTS')
        self.assertEqual(row['workflow_status'], 'NEW')
        self.db.update_workflow('N1860078575', 'RESOLVED', 'Caso noto GLS check', 'Simone')
        row = self.db.get_shipment('N1860078575')
        self.assertEqual(row['workflow_status'], 'RESOLVED')
        SyncEngine._save_no_events(self.engine, self.stale())
        row = self.db.get_shipment('N1860078575')
        self.assertEqual(row['workflow_status'], 'RESOLVED')

    def test_legacy_no_events_not_shown_as_technical_error(self):
        sync_id = self.db.start_sync()
        self.db.record_sync_error(sync_id, 'N1', error_code='NO_EVENTS', error_title='no events', error_message='x', resolution_hint='x')
        self.db.record_sync_error(sync_id, 'N2', error_code='TIMEOUT', error_title='timeout', error_message='x', resolution_hint='x')
        self.db.finish_sync(sync_id, 'PARTIAL', gls_errors=2)
        last = self.db.latest_sync()
        self.assertEqual(last['gls_errors_reported'], 2)
        self.assertEqual(last['gls_errors'], 1)
        self.assertEqual([x['error_code'] for x in last['error_summary']], ['TIMEOUT'])


if __name__ == '__main__':
    unittest.main()
