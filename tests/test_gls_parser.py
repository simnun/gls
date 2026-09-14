import unittest
from pathlib import Path
from types import SimpleNamespace

from core.gls import GLSClient

ROOT = Path(__file__).resolve().parent.parent


class TestGLSParser(unittest.TestCase):
    def test_tracking_xml(self):
        raw = (ROOT / "mock" / "tracking_sample.xml").read_bytes()
        parsed = GLSClient.parse_tracking_xml(raw, "ABC123")
        self.assertEqual(parsed["tracking_number"], "ABC123")
        self.assertEqual(parsed["destination_depot"], "RM")
        self.assertEqual(parsed["current_event"]["code"], "999")
        self.assertIn("rifiutata", parsed["current_event"]["state"].lower())
        self.assertEqual(len(parsed["events"]), 3)
        self.assertEqual(parsed["events"][-1]["location"], "Roma")


if __name__ == "__main__":
    unittest.main()
