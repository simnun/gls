"""Adattatore Postgres con interfaccia compatibile sqlite3.

Permette a core/db.py di restare scritto in dialetto SQLite (placeholder `?`,
`PRAGMA`, `executescript`, `lastrowid`) e di girare su Supabase/Postgres senza
riscrivere le query esistenti.

Le differenze coperte sono soltanto quelle realmente usate dal progetto:
placeholder, chiavi autoincrementali, GROUP_CONCAT e le PRAGMA di introspezione
usate dalle migrazioni.
"""
from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Sequence
from urllib.parse import quote

import psycopg
from psycopg.rows import dict_row


SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def apply_schema(dsn: str, schema: str) -> str:
    """Aggiunge al DSN lo schema in cui lavorare.

    Passa per il parametro `options` della connessione e non con `SET
    search_path`: il pooler in transaction mode puo' cambiare connessione
    server a ogni transazione, e un SET di sessione non sopravviverebbe.
    """
    schema = (schema or "").strip().lower()
    if not schema:
        return dsn
    if not SCHEMA_RE.match(schema):
        raise ValueError(
            f"Nome schema non valido: {schema!r}. "
            "Ammessi lettere minuscole, cifre e trattino basso."
        )
    if "options=" in dsn:
        return dsn
    separator = "&" if "?" in dsn else "?"
    return dsn + separator + "options=" + quote(f"-c search_path={schema}")


def schema_from_dsn(dsn: str) -> str:
    """Schema indicato nel DSN tramite `options=-c search_path=...`, se presente."""
    match = re.search(r"search_path%3D([a-z0-9_]+)|search_path=([a-z0-9_]+)", dsn, re.IGNORECASE)
    if not match:
        return ""
    return (match.group(1) or match.group(2) or "").lower()


def ensure_schema(dsn: str, schema: str) -> None:
    """Crea lo schema se manca, cosi le tabelle non finiscono in `public`."""
    schema = (schema or "").strip().lower()
    if not schema:
        return
    if not SCHEMA_RE.match(schema):
        raise ValueError(f"Nome schema non valido: {schema!r}")
    with psycopg.connect(dsn, autocommit=True, connect_timeout=15) as conn:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')


def _prepare_threshold() -> int | None:
    """Soglia dei prepared statement di psycopg.

    Il pooler Supabase in transaction mode non li supporta: ogni transazione puo'
    finire su una connessione server diversa, quindi lo statement preparato non
    esiste piu'. Per questo la soglia e' disattivata salvo indicazione contraria.
    """
    raw = os.getenv("PG_PREPARE_THRESHOLD", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None

# Tabelle con chiave surrogata `id`: solo per queste ha senso emulare lastrowid.
TABLES_WITH_SERIAL_ID = {
    "events",
    "operator_actions",
    "stock_cases",
    "gls_release_requests",
    "inconsistencies",
    "sync_runs",
    "sync_errors",
}


class Row(dict):
    """Riga accessibile per nome e per posizione, come sqlite3.Row."""

    __slots__ = ()

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return dict.__getitem__(self, key)


def split_statements(script: str) -> list[str]:
    """Divide uno script SQL sui `;` di primo livello, rispettando le stringhe."""
    statements: list[str] = []
    buffer: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(script):
        char = script[index]
        if quote:
            buffer.append(char)
            if char == quote:
                # Un apice raddoppiato e' un escape, non la fine della stringa.
                if index + 1 < len(script) and script[index + 1] == quote:
                    buffer.append(script[index + 1])
                    index += 2
                    continue
                quote = None
        elif char in {"'", '"'}:
            quote = char
            buffer.append(char)
        elif char == ";":
            chunk = "".join(buffer).strip()
            if chunk:
                statements.append(chunk)
            buffer = []
        else:
            buffer.append(char)
        index += 1
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def convert_placeholders(sql: str, escape_percent: bool) -> str:
    """Converte i `?` di SQLite in `%s`, ignorando quelli dentro le stringhe."""
    out: list[str] = []
    quote: str | None = None
    for char in sql:
        if quote:
            if char == "%" and escape_percent:
                out.append("%%")
            else:
                out.append(char)
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            out.append(char)
        elif char == "?":
            out.append("%s")
        elif char == "%" and escape_percent:
            out.append("%%")
        else:
            out.append(char)
    return "".join(out)


def translate(sql: str, escape_percent: bool = False) -> str:
    """Traduce una singola istruzione dal dialetto SQLite a Postgres."""
    sql = convert_placeholders(sql, escape_percent)
    sql = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "BIGSERIAL PRIMARY KEY",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(r"\bGROUP_CONCAT\s*\(", "string_agg(", sql, flags=re.IGNORECASE)
    return sql


class PgCursor:
    """Risultato gia' materializzato, con la superficie usata da core/db.py."""

    def __init__(self, rows: list[Row], lastrowid: int | None, rowcount: int):
        self._rows = rows
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def fetchone(self) -> Row | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Row]:
        return list(self._rows)

    def __iter__(self) -> Iterator[Row]:
        return iter(self._rows)


_TABLE_INFO_RE = re.compile(r"pragma\s+table_info\s*\(\s*([a-z_][a-z0-9_]*)\s*\)", re.IGNORECASE)
_INSERT_RE = re.compile(r"insert\s+into\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)


class PgConnection:
    """Connessione Postgres che espone l'API sqlite3 usata dal progetto."""

    def __init__(self, conn: psycopg.Connection):
        self._conn = conn
        # Presente solo per compatibilita' con `conn.row_factory = sqlite3.Row`.
        self.row_factory: Any = None

    # -- API sqlite3 -----------------------------------------------------

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> PgCursor:
        text = sql.strip()
        if text.lower().startswith("pragma"):
            return self._pragma(text)

        has_params = bool(params)
        statement = translate(text, escape_percent=has_params)

        table = _INSERT_RE.match(statement)
        want_id = bool(
            table
            and table.group(1).lower() in TABLES_WITH_SERIAL_ID
            and " returning " not in statement.lower()
        )
        if want_id:
            statement = statement.rstrip().rstrip(";") + " RETURNING id"

        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(statement, tuple(params) if has_params else None)
            rows = [Row(item) for item in cur.fetchall()] if cur.description else []
            rowcount = cur.rowcount

        if want_id:
            lastrowid = int(rows[0]["id"]) if rows else None
            # La riga di RETURNING e' un dettaglio interno: non va esposta.
            return PgCursor([], lastrowid, rowcount)
        return PgCursor(rows, None, rowcount)

    def executescript(self, script: str) -> None:
        for statement in split_statements(script):
            self.execute(statement)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    # -- PRAGMA emulate --------------------------------------------------

    def _pragma(self, text: str) -> PgCursor:
        match = _TABLE_INFO_RE.search(text)
        if not match:
            # journal_mode / foreign_keys / busy_timeout non hanno equivalente:
            # su Postgres sono gia' il comportamento predefinito.
            return PgCursor([], None, 0)
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT column_name AS name
                FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = %s
                ORDER BY ordinal_position
                """,
                (match.group(1).lower(),),
            )
            rows = [Row(item) for item in cur.fetchall()]
        return PgCursor(rows, None, len(rows))


class ConnectionPool:
    """Pool minimale, pensato per il riuso tra invocazioni serverless a caldo."""

    def __init__(self, dsn: str, max_idle: int = 4):
        self._dsn = dsn
        self._max_idle = max_idle
        self._idle: list[psycopg.Connection] = []
        self._lock = threading.Lock()
        self._schema = schema_from_dsn(dsn)
        self._schema_ready = not self._schema

    def _new(self) -> psycopg.Connection:
        conn = psycopg.connect(self._dsn, autocommit=False, connect_timeout=15)
        conn.prepare_threshold = _prepare_threshold()
        if not self._schema_ready:
            # Senza lo schema, una CREATE TABLE non qualificata non troverebbe
            # dove creare la tabella. CREATE SCHEMA non dipende da search_path.
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"')
            conn.commit()
            self._schema_ready = True
        return conn

    def acquire(self) -> psycopg.Connection:
        with self._lock:
            while self._idle:
                conn = self._idle.pop()
                if not conn.closed:
                    return conn
        return self._new()

    def release(self, conn: psycopg.Connection) -> None:
        if conn.closed:
            return
        try:
            conn.rollback()
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            return
        with self._lock:
            if len(self._idle) < self._max_idle:
                self._idle.append(conn)
                return
        try:
            conn.close()
        except Exception:
            pass

    def close_all(self) -> None:
        with self._lock:
            idle, self._idle = self._idle, []
        for conn in idle:
            try:
                conn.close()
            except Exception:
                pass


_pools: dict[str, ConnectionPool] = {}
_pools_lock = threading.Lock()


def get_pool(dsn: str, max_idle: int = 4) -> ConnectionPool:
    with _pools_lock:
        pool = _pools.get(dsn)
        if pool is None:
            pool = ConnectionPool(dsn, max_idle=max_idle)
            _pools[dsn] = pool
        return pool


@contextmanager
def connection(dsn: str, max_idle: int = 4) -> Iterator[PgConnection]:
    """Connessione presa dal pool, con commit automatico e rollback in errore."""
    pool = get_pool(dsn, max_idle=max_idle)
    raw = pool.acquire()
    wrapper = PgConnection(raw)
    try:
        yield wrapper
        raw.commit()
    except BaseException:
        try:
            raw.rollback()
        except Exception:
            pass
        raise
    finally:
        pool.release(raw)
