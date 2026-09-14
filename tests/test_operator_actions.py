import tempfile
import unittest
from pathlib import Path

from core.db import Database


class OperatorActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / 'test.sqlite3')
        self.db.upsert_shipment({
            'tracking_number': 'NI123',
            'order_name': '#1',
            'severity': 'CRITICAL',
            'category': 'STOCK',
            'gls_status': 'Spedizione in giacenza',
            'closed': False,
        })

    def tearDown(self):
        self.tmp.cleanup()

    def test_action_is_persistent_and_updates_workflow(self):
        self.db.add_operator_action(
            'NI123',
            'RELEASE_REQUESTED',
            'Svincolo richiesto',
            note='Richiesto alla sede',
            operator_name='Simone',
            workflow_status='WAITING_GLS',
        )
        item = self.db.get_shipment('NI123')
        self.assertEqual(item['workflow_status'], 'WAITING_GLS')
        self.assertEqual(item['last_operator_action'], 'Svincolo richiesto')
        self.assertEqual(item['last_operator_name'], 'Simone')
        self.assertEqual(len(item['operator_actions']), 1)
        self.assertEqual(item['operator_actions'][0]['note'], 'Richiesto alla sede')

    def test_gls_upsert_does_not_erase_operator_memory(self):
        self.db.add_operator_action(
            'NI123', 'CUSTOMER_MESSAGE', 'Messaggio inviato al cliente',
            operator_name='Anna', workflow_status='WAITING_CUSTOMER'
        )
        self.db.upsert_shipment({
            'tracking_number': 'NI123',
            'order_name': '#1',
            'severity': 'CRITICAL',
            'category': 'STOCK',
            'gls_status': 'Spedizione in giacenza',
            'closed': False,
        })
        item = self.db.get_shipment('NI123')
        self.assertEqual(item['workflow_status'], 'WAITING_CUSTOMER')
        self.assertEqual(item['last_operator_action'], 'Messaggio inviato al cliente')
        self.assertEqual(item['last_operator_name'], 'Anna')


if __name__ == '__main__':
    unittest.main()
