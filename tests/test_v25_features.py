import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zipfile import ZipFile
from io import BytesIO

from core.classifier import Classifier
from core.db import Database
from core.xlsx_export import build_xlsx


class V25Features(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / 'test.db')
        self.rules = Path(__file__).resolve().parents[1] / 'config' / 'rules.json'
        self.classifier = Classifier(self.rules, self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _shipment(self, tracking='T1', **extra):
        base = dict(
            tracking_number=tracking, order_name='#1001', order_created_at=datetime.now(timezone.utc).isoformat(),
            customer_name='Mario Rossi', customer_phone='+39 3331234567', gls_status='In transito', gls_note='',
            gls_code='', gls_event_at=datetime.now(timezone.utc).isoformat(), severity='NORMAL', category='IN_TRANSIT',
            reason='', recommended_action='', closed=False,
        )
        base.update(extra)
        self.db.upsert_shipment(base)

    def _event(self, tracking, when, category, state):
        self.db.insert_event(dict(
            tracking_number=tracking, event_hash=f'{tracking}-{when}-{category}', event_at=when, code='', state=state,
            note='', location='Napoli', severity='CRITICAL' if category in {'RETURN','STORAGE'} else 'NORMAL',
            category=category, reason='', raw={}
        ))

    def test_return_to_sender_is_not_delivered(self):
        c = self.classifier.classify(code='', state='Spedizione riconsegnata al mittente', note='', event_at=None, is_cod=False)
        self.assertEqual(c.category, 'RETURN')

    def test_redelivery_then_return_creates_critical_inconsistency(self):
        now = datetime.now(timezone.utc)
        self._shipment('X1', category='RETURN', severity='CRITICAL', gls_status='Rientro al mittente', gls_event_at=(now + timedelta(hours=2)).isoformat(), closed=True)
        self._event('X1', (now - timedelta(hours=2)).isoformat(), 'STORAGE', 'Spedizione in giacenza')
        self.db.reconcile_stock_cases('X1')
        self.db.record_gls_release_request(
            tracking_number='X1', release_type='1', release_label='Riconsegna allo stesso indirizzo', operator_name='Simone',
            note='cliente conferma', request_data={'delivery_date': now.date().isoformat()}, gls_success=True, gls_result='Ok', gls_raw_response='<Svincolo>Ok</Svincolo>'
        )
        self._event('X1', (now + timedelta(hours=2)).isoformat(), 'RETURN', 'Rientro al mittente')
        issues = self.db.reconcile_inconsistencies('X1')
        self.assertTrue(any(x['issue_code'] == 'REDELIVERY_BECAME_RETURN' for x in issues))

    def test_blocked_state_after_24h_is_flagged(self):
        old = datetime.now(timezone.utc) - timedelta(hours=30)
        self._shipment('X2', category='ADDRESS_ERROR', severity='CRITICAL', gls_status='Indirizzo errato', gls_event_at=old.isoformat())
        self._event('X2', old.isoformat(), 'ADDRESS_ERROR', 'Indirizzo errato')
        issues = self.db.reconcile_inconsistencies('X2')
        self.assertTrue(any(x['issue_code'] == 'BLOCKED_NO_STOCK' for x in issues))

    def test_xlsx_export_is_valid_zip_package(self):
        raw = build_xlsx(['Ordine','Problema'], [['#1','Test']], 'Incongruenze')
        with ZipFile(BytesIO(raw)) as z:
            self.assertIn('xl/worksheets/sheet1.xml', z.namelist())
            self.assertIn('xl/workbook.xml', z.namelist())


if __name__ == '__main__':
    unittest.main()
