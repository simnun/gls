"""Verifica che il backend Postgres si comporti come SQLite.

Il test gira solo se e' disponibile un Postgres di prova:

    TEST_DATABASE_URL="postgresql://utente:password@host:5432/db" \\
        python3 tests/test_postgres_backend.py

Senza quella variabile viene saltato, cosi l'uso locale su SQLite non richiede
ne' psycopg ne' un database aggiuntivo.
"""
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.db import Database  # noqa: E402

BASE_DSN = os.getenv("TEST_DATABASE_URL", "").strip()


def make_postgres_db() -> Database:
    """Database Postgres isolato in uno schema dedicato, per non sporcare gli altri test."""
    import psycopg

    schema = "t" + uuid.uuid4().hex[:12]
    with psycopg.connect(BASE_DSN, autocommit=True) as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
    separator = "&" if "?" in BASE_DSN else "?"
    dsn = BASE_DSN + separator + "options=" + quote(f"-c search_path={schema}")
    return Database(None, dsn=dsn)


def populate(db: Database) -> dict:
    """Stessa sequenza di operazioni su entrambi i backend."""
    tracking = "TRK-PARITA-1"
    db.upsert_shipment({
        "tracking_number": tracking,
        "order_name": "#1001",
        "order_legacy_id": "555",
        "customer_name": "Mario Rossi",
        "customer_phone": "+393331234567",
        "order_created_at": "2026-01-10T09:00:00.000+00:00",
        "gls_status": "In giacenza",
        "gls_code": "M0",
        "gls_event_at": "2026-01-12T10:00:00.000+00:00",
        "severity": "CRITICAL",
        "category": "STOCK",
        "total_amount": 49.9,
        "currency": "EUR",
        "is_cod": 1,
    })
    inserted = db.insert_event({
        "tracking_number": tracking,
        "event_hash": "hash-1",
        "event_at": "2026-01-12T10:00:00.000+00:00",
        "code": "M0",
        "state": "In giacenza",
        "note": "Giacenza aperta",
        "severity": "CRITICAL",
        "category": "STOCK",
        "raw": {"k": "v"},
    })
    duplicate = db.insert_event({
        "tracking_number": tracking,
        "event_hash": "hash-1",
        "event_at": "2026-01-12T10:00:00.000+00:00",
        "code": "M0",
        "state": "In giacenza",
        "note": "Giacenza aperta",
        "severity": "CRITICAL",
        "category": "STOCK",
        "raw": {"k": "v"},
    })
    action = db.add_operator_action(
        tracking_number=tracking,
        action_type="NOTE",
        action_label="Nota operativa",
        note="Cliente avvisato",
        operator_name="Simone",
        workflow_status="IN_PROGRESS",
    )
    db.set_override("M0", "CRITICAL", "STOCK", "Gestire la giacenza", "override di prova")

    sync_id = db.start_sync()
    db.record_sync_error(
        sync_id, tracking, error_code="TIMEOUT", error_title="Timeout GLS",
        error_message="tempo scaduto", resolution_hint="riprovare", attempts=2,
    )
    db.record_sync_error(
        sync_id, tracking, error_code="NO_EVENTS", error_title="Nessun evento",
        error_message="", resolution_hint="",
    )
    db.finish_sync(sync_id, "OK", shopify_orders=1, tracking_numbers=1, gls_success=0, gls_errors=1)

    last = db.latest_sync()
    detail = db.get_shipment(tracking)
    dashboard = db.dashboard()

    return {
        "evento_inserito": inserted,
        "duplicato_ignorato": duplicate,
        "azione_ha_id": bool(action.get("id")),
        "note_azioni": [a["note"] for a in detail["operator_actions"]],
        "spedizioni": len(dashboard["shipments"]),
        "tracking": dashboard["shipments"][0]["tracking_number"],
        "severity": dashboard["shipments"][0]["severity"],
        "eventi": len(detail["events"]),
        "override": {o["event_code"]: o["severity"] for o in db.list_overrides()},
        "sync_status": last["status"],
        "errori_tecnici": last["gls_errors"],
        "riepilogo_errori": [dict(g) for g in last["error_summary"]],
    }


@unittest.skipUnless(BASE_DSN, "TEST_DATABASE_URL non impostata")
class PostgresParityTests(unittest.TestCase):
    def test_stesso_risultato_su_sqlite_e_postgres(self):
        with tempfile.TemporaryDirectory() as tmp:
            atteso = populate(Database(Path(tmp) / "parita.sqlite3"))
        ottenuto = populate(make_postgres_db())
        self.assertEqual(atteso, ottenuto)

    def test_lastrowid_e_sequenze(self):
        db = make_postgres_db()
        primo = db.start_sync()
        secondo = db.start_sync()
        self.assertIsInstance(primo, int)
        self.assertEqual(secondo, primo + 1)

    def test_pragma_table_info_emulata(self):
        db = make_postgres_db()
        with db.connect() as conn:
            colonne = {r["name"] for r in conn.execute("PRAGMA table_info(shipments)").fetchall()}
        self.assertIn("tracking_number", colonne)
        self.assertIn("workflow_status", colonne)


if __name__ == "__main__":
    unittest.main()
