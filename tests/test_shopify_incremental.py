"""Ricerca Shopify incrementale.

Scandire a ogni sincronizzazione l'intera finestra di lookback consuma il grosso
del tempo disponibile: sul primo deploy reale 2500 ordini lasciavano spazio a
sole 8 interrogazioni GLS. Dalla seconda esecuzione in poi basta cio' che e'
cambiato: le spedizioni ancora aperte sono gia' nel database e vengono riprese
da li'.
"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.db import Database  # noqa: E402
from core.sync import SyncEngine  # noqa: E402


class ConfigurazioneFinta:
    shopify_lookback_days = 21
    shopify_overlap_minutes = 60
    rules_path = ROOT / "config" / "rules.json"
    # Attributi richiesti dai client, non usati in questi test.
    shopify_shop = ""
    shopify_client_id = ""
    shopify_client_secret = ""
    shopify_api_version = "2026-07"
    gls_site = ""
    gls_customer_code = ""
    gls_contract_code = ""
    gls_password = ""
    gls_track_endpoint = ""
    gls_list_endpoint = ""
    gls_release_endpoint = ""
    gls_accept_unlabeled_tracking = False
    request_timeout_seconds = 20
    gls_public_workers = 2
    gls_retry_attempts = 3
    sync_workers = 4
    mock_dir = ROOT / "mock"
    mock_mode = True


def iso(momento: datetime) -> str:
    return momento.isoformat(timespec="milliseconds")


class FinestraIncrementaleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "monitor.sqlite3")
        self.engine = SyncEngine(ConfigurazioneFinta(), self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_senza_sincronizzazioni_precedenti_si_usa_tutta_la_finestra(self):
        self.assertIsNone(self.engine._shopify_since())

    def test_una_sincronizzazione_non_conclusa_non_conta(self):
        self.db.start_sync()  # resta RUNNING
        self.assertIsNone(self.engine._shopify_since())

    def test_dopo_una_sincronizzazione_riuscita_la_finestra_si_restringe(self):
        sync_id = self.db.start_sync()
        self.db.finish_sync(sync_id, "OK")
        since = self.engine._shopify_since()
        self.assertIsNotNone(since)
        self.assertTrue(since.endswith("Z"), since)

    def test_il_margine_di_sovrapposizione_viene_applicato(self):
        sync_id = self.db.start_sync()
        self.db.finish_sync(sync_id, "OK")
        avvio = datetime.fromisoformat(self.db.last_successful_sync_at())
        since = datetime.strptime(self.engine._shopify_since(), "%Y-%m-%dT%H:%M:%SZ")
        since = since.replace(tzinfo=timezone.utc)
        differenza = (avvio - since).total_seconds() / 60
        self.assertAlmostEqual(differenza, 60, delta=1)

    def test_una_sincronizzazione_parziale_conta_comunque(self):
        sync_id = self.db.start_sync()
        self.db.finish_sync(sync_id, "PARTIAL")
        self.assertIsNotNone(self.engine._shopify_since())

    def test_dopo_una_lunga_inattivita_si_torna_alla_finestra_intera(self):
        # Se il monitor e' rimasto fermo piu' del lookback, partire dall'ultima
        # sincronizzazione lascerebbe un buco: meglio riesaminare tutto.
        sync_id = self.db.start_sync()
        self.db.finish_sync(sync_id, "OK")
        vecchia = iso(datetime.now(timezone.utc) - timedelta(days=40))
        with self.db.connect() as conn:
            conn.execute("UPDATE sync_runs SET started_at=? WHERE id=?", (vecchia, sync_id))
        self.assertIsNone(self.engine._shopify_since())

    def test_una_sincronizzazione_interrotta_non_restringe_la_finestra(self):
        # Interrotta a meta': non si puo' dare per esaminato quel periodo.
        sync_id = self.db.start_sync()
        vecchia = iso(datetime.now(timezone.utc) - timedelta(minutes=30))
        with self.db.connect() as conn:
            conn.execute("UPDATE sync_runs SET started_at=? WHERE id=?", (vecchia, sync_id))
        self.db.close_stale_syncs(older_than_minutes=1)
        self.assertIsNone(self.engine._shopify_since())


if __name__ == "__main__":
    unittest.main()
