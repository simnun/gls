"""Tenuta della sincronizzazione quando la richiesta viene interrotta.

Su un hosting serverless una richiesta troppo lunga viene chiusa d'autorita' a
meta' lavoro: senza queste protezioni la pratica resta segnata "in corso" per
sempre e l'interfaccia mostra un aggiornamento che non finisce mai.
"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.db import Database  # noqa: E402


def invecchia(db: Database, sync_id: int, minuti: int) -> None:
    istante = (datetime.now(timezone.utc) - timedelta(minutes=minuti)).isoformat(
        timespec="milliseconds")
    with db.connect() as conn:
        conn.execute("UPDATE sync_runs SET started_at=? WHERE id=?", (istante, sync_id))


class SincronizzazioniAppeseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "monitor.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_una_sincronizzazione_vecchia_viene_chiusa(self):
        sync_id = self.db.start_sync()
        invecchia(self.db, sync_id, 30)
        self.assertEqual(self.db.close_stale_syncs(), 1)
        ultima = self.db.latest_sync()
        self.assertEqual(ultima["status"], "INTERROTTA")
        self.assertIsNotNone(ultima["finished_at"])
        self.assertIn("tempo massimo", ultima["message"])

    def test_una_sincronizzazione_appena_avviata_non_viene_toccata(self):
        self.db.start_sync()
        self.assertEqual(self.db.close_stale_syncs(), 0)
        self.assertEqual(self.db.latest_sync()["status"], "RUNNING")

    def test_una_sincronizzazione_conclusa_non_viene_toccata(self):
        sync_id = self.db.start_sync()
        self.db.finish_sync(sync_id, "OK", message="tutto bene")
        invecchia(self.db, sync_id, 120)
        self.assertEqual(self.db.close_stale_syncs(), 0)
        self.assertEqual(self.db.latest_sync()["status"], "OK")

    def test_la_soglia_e_configurabile(self):
        sync_id = self.db.start_sync()
        invecchia(self.db, sync_id, 5)
        self.assertEqual(self.db.close_stale_syncs(older_than_minutes=15), 0)
        self.assertEqual(self.db.close_stale_syncs(older_than_minutes=2), 1)

    def test_il_progresso_non_resta_bloccato_su_in_corso(self):
        # E' il sintomo visibile all'operatore: la barra di avanzamento
        # continuerebbe a girare all'infinito.
        sync_id = self.db.start_sync()
        invecchia(self.db, sync_id, 30)
        self.assertEqual(self.db.latest_sync()["status"], "RUNNING")
        self.db.close_stale_syncs()
        self.assertNotEqual(self.db.latest_sync()["status"], "RUNNING")


class BudgetDiTempoTests(unittest.TestCase):
    def test_il_budget_si_legge_dalla_configurazione(self):
        import os

        from core.config import get_config

        precedente = os.environ.get("SYNC_TIME_BUDGET_SECONDS")
        try:
            os.environ["SYNC_TIME_BUDGET_SECONDS"] = "40"
            self.assertEqual(get_config().sync_time_budget_seconds, 40)
            os.environ["SYNC_TIME_BUDGET_SECONDS"] = ""
            self.assertEqual(get_config().sync_time_budget_seconds, 0)
        finally:
            if precedente is None:
                os.environ.pop("SYNC_TIME_BUDGET_SECONDS", None)
            else:
                os.environ["SYNC_TIME_BUDGET_SECONDS"] = precedente


if __name__ == "__main__":
    unittest.main()
