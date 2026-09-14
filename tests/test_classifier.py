import tempfile
import unittest
from pathlib import Path

from core.classifier import Classifier
from core.db import Database

ROOT = Path(__file__).resolve().parent.parent


class TestClassifier(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.sqlite3")
        self.c = Classifier(ROOT / "config" / "rules.json", self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_refused_is_critical(self):
        result = self.c.classify(
            code="X1",
            state="La spedizione e' stata rifiutata.",
            note="Contattare il mittente",
            event_at=None,
            is_cod=False,
        )
        self.assertEqual(result.severity, "CRITICAL")
        self.assertEqual(result.category, "REFUSED")

    def test_delivered_is_normal(self):
        result = self.c.classify(code="D", state="Consegnata.", note="", event_at=None)
        self.assertEqual(result.severity, "NORMAL")
        self.assertEqual(result.category, "DELIVERED")

    def test_unknown_is_watch(self):
        result = self.c.classify(code="NEW999", state="Evento misterioso", note="", event_at=None)
        self.assertEqual(result.severity, "WATCH")
        self.assertEqual(result.category, "UNCLASSIFIED")

    def test_manual_override_wins(self):
        self.db.set_override("ABC", "NORMAL", "CUSTOM_OK", "Nessuna azione")
        result = self.c.classify(code="ABC", state="Indirizzo errato", note="", event_at=None)
        self.assertEqual(result.severity, "NORMAL")
        self.assertEqual(result.category, "CUSTOM_OK")


if __name__ == "__main__":
    unittest.main()
