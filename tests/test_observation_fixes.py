import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from core.classifier import Classifier
from core.db import Database

ROME = ZoneInfo('Europe/Rome')


class ObservationFixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / 'test.sqlite3')
        self.classifier = Classifier(Path(__file__).parents[1] / 'config' / 'rules.json', self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def at_age(self, hours):
        return (datetime.now(ROME) - timedelta(hours=hours)).isoformat(timespec='seconds')

    def test_not_arrived_without_ancora_is_info_when_recent(self):
        c = self.classifier.classify(
            code='',
            state="La spedizione non e' arrivata presso la sede GLS destinataria.",
            note='',
            event_at=self.at_age(5),
        )
        self.assertEqual(c.severity, 'INFO')
        self.assertEqual(c.category, 'LINEHAUL_DELAY')

    def test_not_arrived_escalates_only_after_24_and_48_hours(self):
        c30 = self.classifier.classify(code='', state="La spedizione non e' arrivata presso la sede GLS destinataria.", note='', event_at=self.at_age(30))
        c55 = self.classifier.classify(code='', state="La spedizione non e' arrivata presso la sede GLS destinataria.", note='', event_at=self.at_age(55))
        self.assertEqual(c30.severity, 'WATCH')
        self.assertEqual(c55.severity, 'WARNING')

    def test_created_by_sender_has_time_based_escalation(self):
        state = "La spedizione e' stata creata dal mittente, attendiamo che ci venga affidata per l'invio a destinazione."
        c5 = self.classifier.classify(code='', state=state, note='', event_at=self.at_age(5))
        c30 = self.classifier.classify(code='', state=state, note='', event_at=self.at_age(30))
        c60 = self.classifier.classify(code='', state=state, note='', event_at=self.at_age(60))
        self.assertEqual(c5.severity, 'NORMAL')
        self.assertEqual(c30.severity, 'WATCH')
        self.assertEqual(c60.severity, 'WARNING')


if __name__ == '__main__':
    unittest.main()
