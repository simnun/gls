import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.db import Database


class TimbroInterrogazioneTests(unittest.TestCase):
    """La coda delle prossime interrogazioni si ordina su gls_checked_at:
    se il timbro non avanza, ogni giro riparte dallo stesso ordine."""

    def setUp(self):
        self.db = Database(path=Path(tempfile.mkdtemp()) / "t.db")

    def test_il_timbro_avanza_a_ogni_interrogazione(self):
        vecchio = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        self.db.upsert_shipment({"tracking_number": "NI1", "gls_checked_at": vecchio})
        self.assertEqual(self.db.get_shipment("NI1")["gls_checked_at"], vecchio)

        nuovo = datetime.now(timezone.utc).isoformat()
        self.db.upsert_shipment({"tracking_number": "NI1", "gls_checked_at": nuovo})
        self.assertEqual(self.db.get_shipment("NI1")["gls_checked_at"], nuovo)

    def test_chi_non_e_mai_stato_interrogato_passa_per_primo(self):
        recente = datetime.now(timezone.utc).isoformat()
        self.db.upsert_shipment({"tracking_number": "NI_recente", "gls_checked_at": recente})
        self.db.upsert_shipment({"tracking_number": "NI_mai", "gls_checked_at": None})
        with self.db.connect() as conn:
            conn.execute("UPDATE shipments SET gls_checked_at=NULL WHERE tracking_number='NI_mai'")
        coda = [s["tracking_number"] for s in self.db.list_open_shipments_for_sync()]
        self.assertLess(coda.index("NI_mai"), coda.index("NI_recente"))
