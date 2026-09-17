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
            'GLS_CONTACTED',
            'GLS contattato',
            note='Richiesto alla sede',
            operator_name='Simone',
            workflow_status='WAITING_GLS',
        )
        item = self.db.get_shipment('NI123')
        self.assertEqual(item['workflow_status'], 'WAITING_GLS')
        self.assertEqual(item['last_operator_action'], 'GLS contattato')
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


class NotaPrendeInCaricoTests(unittest.TestCase):
    """Scrivere una nota significa aver preso in mano la pratica.

    Era l'unica azione che lasciava lo stato su NEW: la pratica restava fra
    quelle da verificare anche dopo che un operatore l'aveva annotata.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / 't.sqlite3')
        self.db.upsert_shipment({
            'tracking_number': 'NOTA1', 'gls_status': 'Indirizzo errato',
            'category': 'ADDRESS_ERROR', 'severity': 'CRITICAL',
        })

    def tearDown(self):
        self.tmp.cleanup()

    def annota(self):
        self.db.add_operator_action(
            tracking_number='NOTA1', action_type='NOTE', action_label='Nota operativa',
            note='Quando va in giacenza inserire il civico 31',
            operator_name='Simone', workflow_status='IN_PROGRESS',
        )

    def test_la_nota_porta_in_lavorazione(self):
        self.annota()
        self.assertEqual(self.db.get_shipment('NOTA1')['workflow_status'], 'IN_PROGRESS')

    def test_la_nota_resta_leggibile_nello_storico(self):
        self.annota()
        note = [a['note'] for a in self.db.get_shipment('NOTA1')['operator_actions']]
        self.assertIn('Quando va in giacenza inserire il civico 31', note)


if __name__ == '__main__':
    unittest.main()
