#!/usr/bin/env python3
"""Importa il database locale SQLite dentro Postgres/Supabase.

Uso:
    DATABASE_URL="postgresql://..." python3 migrate_to_postgres.py [percorso.sqlite3]

Lo script e' ripetibile: le righe gia' presenti vengono lasciate intatte
(ON CONFLICT DO NOTHING), quindi si puo' rilanciare senza duplicare nulla.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.config import get_config  # noqa: E402
from core.db import Database  # noqa: E402
from core import pgcompat  # noqa: E402

# L'ordine rispetta le chiavi esterne: prima i padri, poi i figli.
TABLES: list[tuple[str, str | None]] = [
    ("shipments", None),
    ("events", "id"),
    ("operator_actions", "id"),
    ("stock_cases", "id"),
    ("gls_release_requests", "id"),
    ("inconsistencies", "id"),
    ("code_overrides", None),
    ("sync_runs", "id"),
    ("sync_errors", "id"),
]

# Chiavi su cui riconoscere una riga gia' importata.
CONFLICT_KEYS = {
    "shipments": "tracking_number",
    "events": "id",
    "operator_actions": "id",
    "stock_cases": "id",
    "gls_release_requests": "id",
    "inconsistencies": "id",
    "code_overrides": "event_code",
    "sync_runs": "id",
    "sync_errors": "id",
}

BATCH = 500


def source_path() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).expanduser().resolve()
    return get_config().db_path


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return bool(row)


def main() -> int:
    dsn = get_config().database_url
    if not dsn:
        print("DATABASE_URL non impostata: indica il Postgres di destinazione.")
        return 2

    source = source_path()
    if not source.exists():
        print(f"Database SQLite non trovato: {source}")
        return 2

    print(f"Origine   : {source}")
    print(f"Destinazione: Postgres\n")

    # Crea lo schema sulla destinazione se non esiste ancora.
    target = Database(None, dsn=dsn)

    src = sqlite3.connect(source)
    src.row_factory = sqlite3.Row

    totals: dict[str, int] = {}
    for table, _pk in TABLES:
        if not table_exists(src, table):
            print(f"  {table}: assente nell'origine, salto")
            continue

        rows = src.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"  {table}: 0 righe")
            totals[table] = 0
            continue

        # Solo le colonne presenti su entrambi i lati: le build si sono evolute.
        with target.connect() as conn:
            dest_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        columns = [c for c in rows[0].keys() if c in dest_cols]
        placeholders = ",".join("?" for _ in columns)
        collist = ",".join(columns)
        conflict = CONFLICT_KEYS[table]
        sql = (
            f"INSERT INTO {table}({collist}) VALUES({placeholders}) "
            f"ON CONFLICT({conflict}) DO NOTHING"
        )

        written = 0
        for start in range(0, len(rows), BATCH):
            chunk = rows[start:start + BATCH]
            with target.connect() as conn:
                for row in chunk:
                    conn.execute(sql, tuple(row[c] for c in columns))
            written += len(chunk)
            print(f"  {table}: {written}/{len(rows)}", end="\r", flush=True)
        totals[table] = written
        print(f"  {table}: {written} righe importate     ")

    src.close()

    # Riallinea le sequenze, altrimenti il primo INSERT collide con gli id importati.
    print("\nRiallineo le sequenze...")
    with pgcompat.connection(dsn) as conn:
        for table, pk in TABLES:
            if not pk:
                continue
            conn.execute(
                f"SELECT setval(pg_get_serial_sequence('{table}', '{pk}'), "
                f"COALESCE((SELECT MAX({pk}) FROM {table}), 0) + 1, false)"
            )
    print("Sequenze allineate.")

    print("\nRiepilogo:")
    for table, count in totals.items():
        print(f"  {table:24s} {count}")
    print("\nImportazione completata.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
