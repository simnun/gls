#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "data" / "monitor.sqlite3"


def has_real_data(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("SELECT COUNT(*) FROM shipments").fetchone()
            return bool(row and int(row[0]) > 0)
        finally:
            conn.close()
    except Exception:
        return False


def resolve_source(value: str) -> Path | None:
    raw = value.strip().strip('"').strip("'")
    if not raw:
        return None
    p = Path(raw).expanduser()
    if p.is_dir():
        candidate = p / "data" / "monitor.sqlite3"
        if has_real_data(candidate):
            return candidate
    if has_real_data(p):
        return p
    return None


def find_previous() -> Path | None:
    candidates: list[Path] = []
    for folder in ROOT.parent.glob("GLS_Exception_Monitor*"):
        if not folder.is_dir() or folder.resolve() == ROOT.resolve():
            continue
        db = folder / "data" / "monitor.sqlite3"
        if has_real_data(db):
            candidates.append(db)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def migrate(source: Path | None = None, force: bool = False) -> bool:
    if has_real_data(TARGET) and not force:
        return False
    source = source or find_previous()
    if not source:
        return False
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(source)
    dst = sqlite3.connect(TARGET)
    try:
        if force:
            for table in ["sync_errors", "inconsistencies", "gls_release_requests", "stock_cases", "operator_actions", "events", "shipments", "code_overrides", "sync_runs"]:
                try:
                    dst.execute(f"DROP TABLE IF EXISTS {table}")
                except Exception:
                    pass
            dst.commit()
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    print(f"Dati operativi importati da: {source.parent.parent.name}")
    return True


if __name__ == "__main__":
    explicit = resolve_source(sys.argv[1]) if len(sys.argv) > 1 else None
    if len(sys.argv) > 1 and not explicit:
        print("Non trovo un database valido nel percorso indicato.")
        raise SystemExit(2)
    migrated = migrate(explicit, force=bool(explicit))
    if explicit and not migrated:
        print("Nessun dato importato.")
