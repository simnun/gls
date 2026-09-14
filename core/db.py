from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


WORKFLOW_ALLOWED = {"NEW", "IN_PROGRESS", "WAITING_CUSTOMER", "WAITING_GLS", "RESOLVED", "IGNORED"}


class Database:
    """Memoria del monitor su SQLite (locale) oppure Postgres/Supabase (online).

    Le query restano scritte in dialetto SQLite: quando il backend e' Postgres
    vengono tradotte al volo da core.pgcompat.
    """

    def __init__(self, path: Path | None = None, dsn: str | None = None):
        self.dsn = (dsn or "").strip() or None
        self.path = path
        if self.dsn is None:
            if path is None:
                raise ValueError("Serve un percorso SQLite oppure un DSN Postgres")
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @property
    def is_postgres(self) -> bool:
        return self.dsn is not None

    @contextmanager
    def connect(self):
        if self.dsn is not None:
            from . import pgcompat

            with pgcompat.connection(self.dsn) as conn:
                yield conn
            return

        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS shipments (
                    tracking_number TEXT PRIMARY KEY,
                    order_gid TEXT,
                    order_legacy_id TEXT,
                    order_name TEXT,
                    order_created_at TEXT,
                    fulfillment_created_at TEXT,
                    fulfillment_updated_at TEXT,
                    customer_gid TEXT,
                    customer_legacy_id TEXT,
                    customer_name TEXT,
                    customer_email TEXT,
                    customer_phone TEXT,
                    city TEXT,
                    province TEXT,
                    total_amount REAL,
                    currency TEXT,
                    payment_gateways TEXT,
                    is_cod INTEGER NOT NULL DEFAULT 0,
                    shopify_financial_status TEXT,
                    shopify_fulfillment_status TEXT,
                    gls_status TEXT,
                    gls_note TEXT,
                    gls_code TEXT,
                    gls_location TEXT,
                    gls_event_at TEXT,
                    gls_destination_depot TEXT,
                    gls_destination_city TEXT,
                    gls_destination_phone TEXT,
                    severity TEXT NOT NULL DEFAULT 'WATCH',
                    category TEXT NOT NULL DEFAULT 'UNCLASSIFIED',
                    reason TEXT,
                    recommended_action TEXT,
                    workflow_status TEXT NOT NULL DEFAULT 'NEW',
                    workflow_updated_at TEXT,
                    manual_closed_revision INTEGER,
                    tracking_revision INTEGER NOT NULL DEFAULT 0,
                    operator_note TEXT,
                    last_operator_action TEXT,
                    last_operator_action_at TEXT,
                    last_operator_name TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_changed_at TEXT NOT NULL,
                    closed INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'shopify'
                );

                CREATE INDEX IF NOT EXISTS idx_shipments_severity ON shipments(severity);
                CREATE INDEX IF NOT EXISTS idx_shipments_workflow ON shipments(workflow_status);
                CREATE INDEX IF NOT EXISTS idx_shipments_closed ON shipments(closed);
                CREATE INDEX IF NOT EXISTS idx_shipments_event_at ON shipments(gls_event_at);
                CREATE INDEX IF NOT EXISTS idx_shipments_order_created ON shipments(order_created_at);

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_number TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE,
                    event_at TEXT,
                    code TEXT,
                    state TEXT,
                    note TEXT,
                    location TEXT,
                    severity TEXT,
                    category TEXT,
                    reason TEXT,
                    raw_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(tracking_number) REFERENCES shipments(tracking_number) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_events_tracking ON events(tracking_number, event_at DESC);

                CREATE TABLE IF NOT EXISTS operator_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_number TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    action_label TEXT NOT NULL,
                    note TEXT,
                    operator_name TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(tracking_number) REFERENCES shipments(tracking_number) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_actions_tracking ON operator_actions(tracking_number, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_actions_created ON operator_actions(created_at DESC);

                CREATE TABLE IF NOT EXISTS stock_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_number TEXT NOT NULL,
                    entry_event_hash TEXT NOT NULL UNIQUE,
                    entered_at TEXT,
                    entry_code TEXT,
                    entry_state TEXT,
                    entry_note TEXT,
                    entry_location TEXT,
                    exit_event_hash TEXT,
                    exited_at TEXT,
                    outcome_category TEXT,
                    outcome_state TEXT,
                    outcome_note TEXT,
                    outcome_event_at TEXT,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(tracking_number) REFERENCES shipments(tracking_number) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_stock_tracking ON stock_cases(tracking_number, entered_at DESC);
                CREATE INDEX IF NOT EXISTS idx_stock_status ON stock_cases(status, entered_at DESC);

                CREATE TABLE IF NOT EXISTS gls_release_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_number TEXT NOT NULL,
                    stock_case_id INTEGER,
                    release_type TEXT NOT NULL,
                    release_label TEXT NOT NULL,
                    operator_name TEXT,
                    note TEXT,
                    request_json TEXT,
                    gls_success INTEGER NOT NULL DEFAULT 0,
                    gls_result TEXT,
                    gls_raw_response TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(tracking_number) REFERENCES shipments(tracking_number) ON DELETE CASCADE,
                    FOREIGN KEY(stock_case_id) REFERENCES stock_cases(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_release_tracking ON gls_release_requests(tracking_number, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_release_stock ON gls_release_requests(stock_case_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS inconsistencies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tracking_number TEXT NOT NULL,
                    issue_key TEXT NOT NULL,
                    issue_code TEXT NOT NULL,
                    title TEXT NOT NULL,
                    detail TEXT,
                    suggested_action TEXT,
                    severity TEXT NOT NULL DEFAULT 'CRITICAL',
                    detected_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    resolved_at TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    context_json TEXT,
                    UNIQUE(tracking_number, issue_key),
                    FOREIGN KEY(tracking_number) REFERENCES shipments(tracking_number) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_incons_active ON inconsistencies(active, last_seen_at DESC);
                CREATE INDEX IF NOT EXISTS idx_incons_tracking ON inconsistencies(tracking_number, active);

                CREATE TABLE IF NOT EXISTS code_overrides (
                    event_code TEXT PRIMARY KEY,
                    severity TEXT NOT NULL,
                    category TEXT NOT NULL,
                    recommended_action TEXT,
                    note TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    shopify_orders INTEGER NOT NULL DEFAULT 0,
                    discovered_tracking INTEGER NOT NULL DEFAULT 0,
                    tracking_numbers INTEGER NOT NULL DEFAULT 0,
                    skipped_closed INTEGER NOT NULL DEFAULT 0,
                    carried_open INTEGER NOT NULL DEFAULT 0,
                    gls_success INTEGER NOT NULL DEFAULT 0,
                    gls_errors INTEGER NOT NULL DEFAULT 0,
                    pending_pickup INTEGER NOT NULL DEFAULT 0,
                    no_event_attention INTEGER NOT NULL DEFAULT 0,
                    message TEXT
                );

                CREATE TABLE IF NOT EXISTS sync_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sync_id INTEGER NOT NULL,
                    tracking_number TEXT,
                    error_code TEXT NOT NULL,
                    error_title TEXT NOT NULL,
                    error_message TEXT,
                    resolution_hint TEXT,
                    attempts INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(sync_id) REFERENCES sync_runs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_sync_errors_sync ON sync_errors(sync_id, error_code);
                """
            )
            self._migrate_shipments(conn)
            self._migrate_stock_cases(conn)
            self._migrate_sync_runs(conn)

    @staticmethod
    def _migrate_shipments(conn: Any) -> None:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(shipments)").fetchall()}
        additions = {
            "last_operator_action": "TEXT",
            "last_operator_action_at": "TEXT",
            "last_operator_name": "TEXT",
            "customer_gid": "TEXT",
            "customer_legacy_id": "TEXT",
            "workflow_updated_at": "TEXT",
            "manual_closed_revision": "INTEGER",
            "tracking_revision": "INTEGER NOT NULL DEFAULT 0",
            "fulfillment_created_at": "TEXT",
            "fulfillment_updated_at": "TEXT",
        }
        for name, decl in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE shipments ADD COLUMN {name} {decl}")

        # v2.7: ogni stato logistico gia esistente vale come prima revisione.
        # Le pratiche che l'operatore aveva gia segnato come chiuse nelle build
        # precedenti vengono considerate risolte sullo stato corrente e non devono
        # continuare a comparire tra le incongruenze storiche.
        conn.execute("UPDATE shipments SET tracking_revision=1 WHERE COALESCE(tracking_revision,0)=0")
        conn.execute(
            """UPDATE shipments
               SET manual_closed_revision=tracking_revision,
                   workflow_updated_at=COALESCE(workflow_updated_at,last_operator_action_at,last_seen_at)
               WHERE workflow_status IN ('RESOLVED','IGNORED')
                 AND manual_closed_revision IS NULL"""
        )
        stamp = utcnow()
        conn.execute(
            """UPDATE inconsistencies
               SET active=0, resolved_at=COALESCE(resolved_at, ?), last_seen_at=?
               WHERE active=1 AND tracking_number IN (
                   SELECT tracking_number FROM shipments WHERE workflow_status IN ('RESOLVED','IGNORED')
               )""",
            (stamp, stamp),
        )

    @staticmethod
    def _migrate_stock_cases(conn: Any) -> None:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(stock_cases)").fetchall()}
        if cols and "outcome_event_at" not in cols:
            conn.execute("ALTER TABLE stock_cases ADD COLUMN outcome_event_at TEXT")


    @staticmethod
    def _migrate_sync_runs(conn: Any) -> None:
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(sync_runs)").fetchall()}
        additions = {
            "discovered_tracking": "INTEGER NOT NULL DEFAULT 0",
            "skipped_closed": "INTEGER NOT NULL DEFAULT 0",
            "carried_open": "INTEGER NOT NULL DEFAULT 0",
            "pending_pickup": "INTEGER NOT NULL DEFAULT 0",
            "no_event_attention": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, decl in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE sync_runs ADD COLUMN {name} {decl}")

    def start_sync(self) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO sync_runs(started_at, status) VALUES(?, 'RUNNING')",
                (utcnow(),),
            )
            return int(cur.lastrowid)

    def finish_sync(
        self,
        sync_id: int,
        status: str,
        *,
        shopify_orders: int = 0,
        discovered_tracking: int = 0,
        tracking_numbers: int = 0,
        skipped_closed: int = 0,
        carried_open: int = 0,
        gls_success: int = 0,
        gls_errors: int = 0,
        pending_pickup: int = 0,
        no_event_attention: int = 0,
        message: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE sync_runs
                SET finished_at=?, status=?, shopify_orders=?, discovered_tracking=?, tracking_numbers=?,
                    skipped_closed=?, carried_open=?, gls_success=?, gls_errors=?, pending_pickup=?,
                    no_event_attention=?, message=?
                WHERE id=?
                """,
                (
                    utcnow(), status, shopify_orders, discovered_tracking, tracking_numbers,
                    skipped_closed, carried_open, gls_success, gls_errors, pending_pickup,
                    no_event_attention, message[:4000], sync_id,
                ),
            )

    def record_sync_error(
        self, sync_id: int, tracking_number: str, *, error_code: str, error_title: str,
        error_message: str, resolution_hint: str, attempts: int = 1,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO sync_errors(
                    sync_id,tracking_number,error_code,error_title,error_message,resolution_hint,attempts,created_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (sync_id, tracking_number, error_code, error_title, error_message[:4000],
                 resolution_hint[:2000], max(1, int(attempts or 1)), utcnow()),
            )

    def latest_sync(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                return None
            result = dict(row)
            # NO_EVENTS non e' un errore tecnico: e' una condizione operativa della spedizione.
            # Versioni precedenti potevano averlo registrato in sync_errors; lo filtriamo qui
            # cosi il banner tecnico mostra solo guasti reali (rete, timeout, 429, parser, ecc.).
            groups = conn.execute(
                """SELECT error_code,error_title,resolution_hint,COUNT(*) AS count,MAX(attempts) AS attempts
                   FROM sync_errors WHERE sync_id=? AND error_code<>'NO_EVENTS'
                   GROUP BY error_code,error_title,resolution_hint
                   ORDER BY count DESC,error_code""",
                (result["id"],),
            ).fetchall()
            samples = conn.execute(
                """SELECT tracking_number,error_code,error_title,error_message,resolution_hint,attempts
                   FROM sync_errors WHERE sync_id=? AND error_code<>'NO_EVENTS'
                   ORDER BY id DESC LIMIT 25""",
                (result["id"],),
            ).fetchall()
            technical_count = conn.execute(
                "SELECT COUNT(*) FROM sync_errors WHERE sync_id=? AND error_code<>'NO_EVENTS'",
                (result["id"],),
            ).fetchone()[0]
            result["gls_errors_reported"] = int(result.get("gls_errors") or 0)
            result["gls_errors"] = int(technical_count or 0)
            result["error_summary"] = [dict(x) for x in groups]
            result["error_samples"] = [dict(x) for x in samples]
            return result

    def tracking_state_map(self) -> dict[str, dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT tracking_number,closed,category,workflow_status,last_seen_at FROM shipments"
            ).fetchall()
            return {str(r["tracking_number"]): dict(r) for r in rows}

    def list_open_shipments_for_sync(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM shipments WHERE closed=0 ORDER BY COALESCE(gls_event_at,last_seen_at) ASC"
            ).fetchall()
            return [self._shipment_row(dict(r)) for r in rows]

    def get_override(self, event_code: str | None) -> dict[str, Any] | None:
        if not event_code:
            return None
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM code_overrides WHERE event_code=?", (event_code,)).fetchone()
            return dict(row) if row else None

    def list_overrides(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM code_overrides ORDER BY event_code").fetchall()
            return [dict(r) for r in rows]

    def set_override(
        self,
        event_code: str,
        severity: str,
        category: str,
        recommended_action: str = "",
        note: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO code_overrides(event_code, severity, category, recommended_action, note, updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(event_code) DO UPDATE SET
                  severity=excluded.severity,
                  category=excluded.category,
                  recommended_action=excluded.recommended_action,
                  note=excluded.note,
                  updated_at=excluded.updated_at
                """,
                (event_code, severity, category, recommended_action, note, utcnow()),
            )

    def upsert_shipment(self, data: dict[str, Any]) -> None:
        now = utcnow()
        tracking = data["tracking_number"]
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT gls_code, gls_status, gls_note, gls_event_at, workflow_status, workflow_updated_at,
                       manual_closed_revision, tracking_revision, operator_note,
                       first_seen_at, last_changed_at, last_operator_action,
                       last_operator_action_at, last_operator_name
                FROM shipments WHERE tracking_number=?
                """,
                (tracking,),
            ).fetchone()
            first_seen = existing["first_seen_at"] if existing else now
            workflow_status = existing["workflow_status"] if existing else data.get("workflow_status", "NEW")
            operator_note = existing["operator_note"] if existing else data.get("operator_note", "")
            changed = True
            if existing:
                changed = (
                    (existing["gls_code"] or "") != (data.get("gls_code") or "")
                    or (existing["gls_status"] or "") != (data.get("gls_status") or "")
                    or (existing["gls_note"] or "") != (data.get("gls_note") or "")
                    or (existing["gls_event_at"] or "") != (data.get("gls_event_at") or "")
                )
            last_changed = now if changed else (existing["last_changed_at"] if existing else now)
            tracking_revision = (int(existing["tracking_revision"] or 0) + (1 if changed else 0)) if existing else 1

            fields = {
                "tracking_number": tracking,
                "order_gid": data.get("order_gid"),
                "order_legacy_id": data.get("order_legacy_id"),
                "order_name": data.get("order_name"),
                "order_created_at": data.get("order_created_at"),
                "fulfillment_created_at": data.get("fulfillment_created_at"),
                "fulfillment_updated_at": data.get("fulfillment_updated_at"),
                "customer_gid": data.get("customer_gid"),
                "customer_legacy_id": data.get("customer_legacy_id"),
                "customer_name": data.get("customer_name"),
                "customer_email": data.get("customer_email"),
                "customer_phone": data.get("customer_phone"),
                "city": data.get("city"),
                "province": data.get("province"),
                "total_amount": data.get("total_amount"),
                "currency": data.get("currency"),
                "payment_gateways": json.dumps(data.get("payment_gateways", []), ensure_ascii=False),
                "is_cod": 1 if data.get("is_cod") else 0,
                "shopify_financial_status": data.get("shopify_financial_status"),
                "shopify_fulfillment_status": data.get("shopify_fulfillment_status"),
                "gls_status": data.get("gls_status"),
                "gls_note": data.get("gls_note"),
                "gls_code": data.get("gls_code"),
                "gls_location": data.get("gls_location"),
                "gls_event_at": data.get("gls_event_at"),
                "gls_destination_depot": data.get("gls_destination_depot"),
                "gls_destination_city": data.get("gls_destination_city"),
                "gls_destination_phone": data.get("gls_destination_phone"),
                "severity": data.get("severity", "WATCH"),
                "category": data.get("category", "UNCLASSIFIED"),
                "reason": data.get("reason", ""),
                "recommended_action": data.get("recommended_action", ""),
                "workflow_status": workflow_status,
                "workflow_updated_at": existing["workflow_updated_at"] if existing else data.get("workflow_updated_at"),
                "manual_closed_revision": existing["manual_closed_revision"] if existing else data.get("manual_closed_revision"),
                "tracking_revision": tracking_revision,
                "operator_note": operator_note,
                "last_operator_action": existing["last_operator_action"] if existing else data.get("last_operator_action"),
                "last_operator_action_at": existing["last_operator_action_at"] if existing else data.get("last_operator_action_at"),
                "last_operator_name": existing["last_operator_name"] if existing else data.get("last_operator_name"),
                "first_seen_at": first_seen,
                "last_seen_at": now,
                "last_changed_at": last_changed,
                "closed": 1 if data.get("closed") else 0,
                "source": data.get("source", "shopify"),
            }
            cols = ",".join(fields.keys())
            placeholders = ",".join("?" for _ in fields)
            protected = {
                "tracking_number", "first_seen_at", "workflow_status", "workflow_updated_at",
                "manual_closed_revision", "operator_note",
                "last_operator_action", "last_operator_action_at", "last_operator_name",
            }
            updates = ",".join(f"{k}=excluded.{k}" for k in fields if k not in protected)
            conn.execute(
                f"INSERT INTO shipments({cols}) VALUES({placeholders}) "
                f"ON CONFLICT(tracking_number) DO UPDATE SET {updates}",
                tuple(fields.values()),
            )

    def insert_event(self, event: dict[str, Any]) -> bool:
        # DO NOTHING invece di intercettare l'errore di integrita': su Postgres
        # una violazione di vincolo aborta l'intera transazione.
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO events(
                  tracking_number,event_hash,event_at,code,state,note,location,
                  severity,category,reason,raw_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_hash) DO NOTHING
                """,
                (
                    event["tracking_number"], event["event_hash"], event.get("event_at"),
                    event.get("code"), event.get("state"), event.get("note"), event.get("location"),
                    event.get("severity"), event.get("category"), event.get("reason"),
                    json.dumps(event.get("raw", {}), ensure_ascii=False), utcnow(),
                ),
            )
            return bool(cur.rowcount)

    def update_workflow(
        self,
        tracking_number: str,
        workflow_status: str,
        operator_note: str | None = None,
        operator_name: str = "",
    ) -> None:
        if workflow_status not in WORKFLOW_ALLOWED:
            raise ValueError("workflow_status non valido")
        with self.connect() as conn:
            current = conn.execute(
                "SELECT workflow_status, operator_note, tracking_revision FROM shipments WHERE tracking_number=?",
                (tracking_number,),
            ).fetchone()
            if not current:
                raise ValueError("Spedizione non trovata")
            now = utcnow()
            is_closed_workflow = workflow_status in {"RESOLVED", "IGNORED"}
            manual_closed_revision = int(current["tracking_revision"] or 0) if is_closed_workflow else None
            if operator_note is None:
                conn.execute(
                    """UPDATE shipments
                       SET workflow_status=?, workflow_updated_at=?, manual_closed_revision=?
                       WHERE tracking_number=?""",
                    (workflow_status, now, manual_closed_revision, tracking_number),
                )
            else:
                conn.execute(
                    """UPDATE shipments
                       SET workflow_status=?, workflow_updated_at=?, manual_closed_revision=?, operator_note=?
                       WHERE tracking_number=?""",
                    (workflow_status, now, manual_closed_revision, operator_note[:4000], tracking_number),
                )
            if is_closed_workflow:
                conn.execute(
                    """UPDATE inconsistencies
                       SET active=0, resolved_at=?, last_seen_at=?
                       WHERE tracking_number=? AND active=1""",
                    (now, now, tracking_number),
                )

            status_changed = current["workflow_status"] != workflow_status
            note_changed = operator_note is not None and (current["operator_note"] or "") != operator_note
            if status_changed or note_changed:
                parts = []
                if status_changed:
                    parts.append(f"Stato pratica: {current['workflow_status']} → {workflow_status}")
                if note_changed:
                    parts.append("Nota pratica aggiornata")
                self._insert_action_conn(
                    conn,
                    tracking_number=tracking_number,
                    action_type="WORKFLOW",
                    action_label=" · ".join(parts),
                    note=(operator_note or "")[:4000] if note_changed else "",
                    operator_name=operator_name,
                )

    def add_operator_action(
        self,
        tracking_number: str,
        action_type: str,
        action_label: str,
        note: str = "",
        operator_name: str = "",
        workflow_status: str | None = None,
    ) -> dict[str, Any]:
        if workflow_status and workflow_status not in WORKFLOW_ALLOWED:
            raise ValueError("workflow_status non valido")
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM shipments WHERE tracking_number=?", (tracking_number,)
            ).fetchone()
            if not exists:
                raise ValueError("Spedizione non trovata")
            action_id = self._insert_action_conn(
                conn,
                tracking_number=tracking_number,
                action_type=action_type[:50],
                action_label=action_label[:200],
                note=note[:4000],
                operator_name=operator_name[:120],
            )
            if workflow_status:
                current = conn.execute(
                    "SELECT tracking_revision FROM shipments WHERE tracking_number=?",
                    (tracking_number,),
                ).fetchone()
                now = utcnow()
                is_closed_workflow = workflow_status in {"RESOLVED", "IGNORED"}
                manual_closed_revision = int(current["tracking_revision"] or 0) if (current and is_closed_workflow) else None
                conn.execute(
                    """UPDATE shipments
                       SET workflow_status=?, workflow_updated_at=?, manual_closed_revision=?
                       WHERE tracking_number=?""",
                    (workflow_status, now, manual_closed_revision, tracking_number),
                )
                if is_closed_workflow:
                    conn.execute(
                        """UPDATE inconsistencies
                           SET active=0, resolved_at=?, last_seen_at=?
                           WHERE tracking_number=? AND active=1""",
                        (now, now, tracking_number),
                    )
            row = conn.execute("SELECT * FROM operator_actions WHERE id=?", (action_id,)).fetchone()
            return dict(row)

    def _insert_action_conn(
        self,
        conn: Any,
        *,
        tracking_number: str,
        action_type: str,
        action_label: str,
        note: str,
        operator_name: str,
    ) -> int:
        now = utcnow()
        cur = conn.execute(
            """
            INSERT INTO operator_actions(
                tracking_number, action_type, action_label, note, operator_name, created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (tracking_number, action_type, action_label, note, operator_name, now),
        )
        conn.execute(
            """
            UPDATE shipments
            SET last_operator_action=?, last_operator_action_at=?, last_operator_name=?
            WHERE tracking_number=?
            """,
            (action_label, now, operator_name, tracking_number),
        )
        return int(cur.lastrowid)

    def dashboard(self, include_closed: bool = False) -> dict[str, Any]:
        where = "1=1" if include_closed else "s.closed=0"
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT s.*,
                       COALESCE(ic.inconsistency_count,0) AS inconsistency_count,
                       COALESCE(ic.inconsistency_titles,'') AS inconsistency_titles
                FROM shipments s
                LEFT JOIN (
                    SELECT tracking_number, COUNT(*) AS inconsistency_count,
                           GROUP_CONCAT(title, ' | ') AS inconsistency_titles
                    FROM inconsistencies WHERE active=1
                    GROUP BY tracking_number
                ) ic ON ic.tracking_number=s.tracking_number
                WHERE {where}
                ORDER BY
                    CASE WHEN COALESCE(ic.inconsistency_count,0)>0 THEN 6
                         WHEN s.severity='CRITICAL' THEN 5 WHEN s.severity='WARNING' THEN 4
                         WHEN s.severity='WATCH' THEN 3 WHEN s.severity='INFO' THEN 2 ELSE 1 END DESC,
                    CASE s.workflow_status WHEN 'NEW' THEN 0 ELSE 1 END ASC,
                    COALESCE(s.gls_event_at,s.last_seen_at) ASC
                """
            ).fetchall()
            shipments = []
            for r in rows:
                item = self._shipment_row(dict(r))
                item["inconsistency_count"] = int(item.get("inconsistency_count") or 0)
                item["has_inconsistency"] = item["inconsistency_count"] > 0
                item["effective_severity"] = "CRITICAL" if item["has_inconsistency"] else item.get("severity", "WATCH")
                shipments.append(item)
            counts = {s: 0 for s in ["CRITICAL", "WARNING", "WATCH", "INFO", "NORMAL"]}
            workflow: dict[str, int] = {}
            for item in shipments:
                sev = item.get("effective_severity") or item.get("severity")
                counts[sev] = counts.get(sev, 0) + 1
                workflow[item["workflow_status"]] = workflow.get(item["workflow_status"], 0) + 1
            unknown = sum(1 for x in shipments if x["category"] == "UNCLASSIFIED")
            pending_actions = sum(1 for x in shipments if x.get("last_operator_action") and x["workflow_status"] not in {"RESOLVED", "IGNORED"})
            return {
                "shipments": shipments,
                "counts": counts,
                "workflow_counts": workflow,
                "unknown_count": unknown,
                "pending_actions": pending_actions,
                "inconsistency_count": sum(1 for x in shipments if x.get("has_inconsistency")),
                "total_active": sum(1 for x in shipments if not x.get("closed")),
                "last_sync": self.latest_sync(),
            }

    def get_shipment(self, tracking_number: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM shipments WHERE tracking_number=?", (tracking_number,)).fetchone()
            if not row:
                return None
            item = self._shipment_row(dict(row))
            events = conn.execute(
                "SELECT * FROM events WHERE tracking_number=? ORDER BY COALESCE(event_at,created_at) DESC, id DESC",
                (tracking_number,),
            ).fetchall()
            actions = conn.execute(
                "SELECT * FROM operator_actions WHERE tracking_number=? ORDER BY created_at DESC, id DESC",
                (tracking_number,),
            ).fetchall()
            item["events"] = [dict(e) for e in events]
            item["operator_actions"] = [dict(a) for a in actions]
            stock_rows = conn.execute(
                "SELECT * FROM stock_cases WHERE tracking_number=? ORDER BY COALESCE(entered_at,created_at) DESC, id DESC",
                (tracking_number,),
            ).fetchall()
            stock_cases = []
            for sc in stock_rows:
                case = dict(sc)
                rr = conn.execute(
                    "SELECT * FROM gls_release_requests WHERE stock_case_id=? ORDER BY created_at DESC, id DESC",
                    (case["id"],),
                ).fetchall()
                case["release_requests"] = [dict(r) for r in rr]
                stock_cases.append(case)
            item["stock_cases"] = stock_cases
            issues = conn.execute(
                "SELECT * FROM inconsistencies WHERE tracking_number=? ORDER BY active DESC, detected_at DESC, id DESC",
                (tracking_number,),
            ).fetchall()
            item["inconsistencies"] = [dict(i) for i in issues]
            item["has_inconsistency"] = any(bool(i["active"]) for i in issues)
            item["effective_severity"] = "CRITICAL" if item["has_inconsistency"] else item.get("severity", "WATCH")
            return item

    def reconcile_stock_cases(self, tracking_number: str) -> None:
        """Build/refresh stock episodes from the persisted GLS event history.

        entry_event_hash makes the operation idempotent. The first movement after the
        giacenza closes the episode, while the latest event before a later giacenza is
        stored as its outcome. This lets the history answer both "quando è uscita" and
        "che fine ha fatto" (for example, eventually delivered).
        """
        exit_categories = {
            "DELIVERED", "OUT_FOR_DELIVERY", "SCHEDULED", "IN_TRANSIT", "RETURN",
            "CORRESPONDENT", "SERVICE_AREA", "LINEHAUL_DELAY", "LABEL_CREATED",
        }
        with self.connect() as conn:
            events = conn.execute(
                """
                SELECT * FROM events WHERE tracking_number=?
                ORDER BY COALESCE(event_at, created_at) ASC, id ASC
                """,
                (tracking_number,),
            ).fetchall()
            if not events:
                return

            episodes: list[dict[str, Any]] = []
            current: dict[str, Any] | None = None
            for row in events:
                e = dict(row)
                category = (e.get("category") or "").upper()
                if category == "STORAGE":
                    # A new STORAGE after a previous confirmed exit is a new episode.
                    if current is None:
                        current = {"entry": e, "first_exit": None, "final": e}
                    elif current.get("first_exit") is not None:
                        episodes.append(current)
                        current = {"entry": e, "first_exit": None, "final": e}
                    else:
                        current["final"] = e
                    continue

                if current is not None:
                    current["final"] = e
                    if current.get("first_exit") is None and category in exit_categories:
                        current["first_exit"] = e

            if current is not None:
                episodes.append(current)

            now = utcnow()
            for ep in episodes:
                entry = ep["entry"]
                first_exit = ep.get("first_exit")
                final_event = ep.get("final") if first_exit else None
                status = "CLOSED" if first_exit else "OPEN"
                conn.execute(
                    """
                    INSERT INTO stock_cases(
                        tracking_number, entry_event_hash, entered_at, entry_code, entry_state,
                        entry_note, entry_location, exit_event_hash, exited_at, outcome_category,
                        outcome_state, outcome_note, outcome_event_at, status, created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(entry_event_hash) DO UPDATE SET
                        exited_at=excluded.exited_at,
                        exit_event_hash=excluded.exit_event_hash,
                        outcome_category=excluded.outcome_category,
                        outcome_state=excluded.outcome_state,
                        outcome_note=excluded.outcome_note,
                        outcome_event_at=excluded.outcome_event_at,
                        status=excluded.status,
                        updated_at=excluded.updated_at
                    """,
                    (
                        tracking_number,
                        entry.get("event_hash"),
                        entry.get("event_at") or entry.get("created_at"),
                        entry.get("code") or "",
                        entry.get("state") or "",
                        entry.get("note") or "",
                        entry.get("location") or "",
                        first_exit.get("event_hash") if first_exit else None,
                        (first_exit.get("event_at") or first_exit.get("created_at")) if first_exit else None,
                        final_event.get("category") if final_event else None,
                        final_event.get("state") if final_event else None,
                        final_event.get("note") if final_event else None,
                        (final_event.get("event_at") or final_event.get("created_at")) if final_event else None,
                        status,
                        now,
                        now,
                    ),
                )

    def reconcile_all_stock_cases(self) -> int:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT tracking_number FROM events WHERE category='STORAGE'"
            ).fetchall()
        for row in rows:
            self.reconcile_stock_cases(row["tracking_number"])
        return len(rows)

    def _current_stock_case_id_conn(self, conn: Any, tracking_number: str) -> int | None:
        row = conn.execute(
            """
            SELECT id FROM stock_cases WHERE tracking_number=?
            ORDER BY CASE status WHEN 'OPEN' THEN 0 ELSE 1 END, COALESCE(entered_at, created_at) DESC, id DESC
            LIMIT 1
            """,
            (tracking_number,),
        ).fetchone()
        return int(row["id"]) if row else None

    def record_gls_release_request(
        self,
        *,
        tracking_number: str,
        release_type: str,
        release_label: str,
        operator_name: str,
        note: str,
        request_data: dict[str, Any],
        gls_success: bool,
        gls_result: str,
        gls_raw_response: str,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM shipments WHERE tracking_number=?", (tracking_number,)
            ).fetchone()
            if not exists:
                raise ValueError("Spedizione non trovata")
            stock_case_id = self._current_stock_case_id_conn(conn, tracking_number)
            now = utcnow()
            cur = conn.execute(
                """
                INSERT INTO gls_release_requests(
                    tracking_number, stock_case_id, release_type, release_label, operator_name,
                    note, request_json, gls_success, gls_result, gls_raw_response, created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    tracking_number, stock_case_id, str(release_type), release_label[:200],
                    operator_name[:120], note[:4000],
                    json.dumps(request_data, ensure_ascii=False), int(bool(gls_success)),
                    gls_result[:1000], gls_raw_response[:8000], now,
                ),
            )
            action_label = (
                f"Istruzione GLS inviata: {release_label}" if gls_success
                else f"Tentativo istruzione GLS fallito: {release_label}"
            )
            self._insert_action_conn(
                conn,
                tracking_number=tracking_number,
                action_type="GLS_RELEASE" if gls_success else "GLS_RELEASE_FAILED",
                action_label=action_label,
                note=note[:4000],
                operator_name=operator_name[:120],
            )
            if gls_success:
                conn.execute(
                    "UPDATE shipments SET workflow_status='IN_PROGRESS' WHERE tracking_number=?",
                    (tracking_number,),
                )
            row = conn.execute("SELECT * FROM gls_release_requests WHERE id=?", (cur.lastrowid,)).fetchone()
            return dict(row)


    @staticmethod
    def _parse_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def reconcile_inconsistencies(self, tracking_number: str) -> list[dict[str, Any]]:
        """Ricalcola le incongruenze operative per una spedizione.

        Le regole confrontano tracking GLS, istruzioni di svincolo e timeline operatore.
        Le incongruenze attive hanno sempre priorita massima e riaprono una pratica chiusa.
        """
        with self.connect() as conn:
            shipment_row = conn.execute(
                "SELECT * FROM shipments WHERE tracking_number=?", (tracking_number,)
            ).fetchone()
            if not shipment_row:
                return []
            shipment = dict(shipment_row)
            events = [dict(r) for r in conn.execute(
                "SELECT * FROM events WHERE tracking_number=? ORDER BY COALESCE(event_at,created_at) ASC, id ASC",
                (tracking_number,),
            ).fetchall()]
            releases = [dict(r) for r in conn.execute(
                "SELECT * FROM gls_release_requests WHERE tracking_number=? ORDER BY created_at ASC, id ASC",
                (tracking_number,),
            ).fetchall()]
            stock_cases = [dict(r) for r in conn.execute(
                "SELECT * FROM stock_cases WHERE tracking_number=? ORDER BY COALESCE(entered_at,created_at) ASC, id ASC",
                (tracking_number,),
            ).fetchall()]

            now = datetime.now(timezone.utc)
            issues: list[dict[str, Any]] = []

            def add(key: str, code: str, title: str, detail: str, action: str, context: dict[str, Any] | None = None):
                issues.append({
                    "issue_key": key,
                    "issue_code": code,
                    "title": title,
                    "detail": detail,
                    "suggested_action": action,
                    "severity": "CRITICAL",
                    "context": context or {},
                })

            current_category = str(shipment.get("category") or "").upper()
            current_state = shipment.get("gls_status") or shipment.get("gls_note") or "Stato GLS non disponibile"
            current_event_at = self._parse_dt(shipment.get("gls_event_at"))
            open_stock = any(str(x.get("status") or "").upper() == "OPEN" for x in stock_cases)

            # Se l'operatore ha marcato la pratica CHIUSA, la sua decisione prevale
            # sulle incongruenze gia note. La pratica puo riaprirsi solo quando GLS
            # produce un nuovo stato/revisione successivo alla chiusura manuale.
            workflow_status = str(shipment.get("workflow_status") or "NEW").upper()
            tracking_revision = int(shipment.get("tracking_revision") or 0)
            manual_closed_revision = shipment.get("manual_closed_revision")
            if workflow_status in {"RESOLVED", "IGNORED"} and manual_closed_revision is not None:
                try:
                    closed_revision = int(manual_closed_revision)
                except (TypeError, ValueError):
                    closed_revision = tracking_revision
                if tracking_revision <= closed_revision:
                    now_s = utcnow()
                    conn.execute(
                        """UPDATE inconsistencies
                           SET active=0, resolved_at=COALESCE(resolved_at, ?), last_seen_at=?
                           WHERE tracking_number=? AND active=1""",
                        (now_s, now_s, tracking_number),
                    )
                    return []

            # Il tracking dice giacenza ma il nostro episodio di giacenza non e' stato ricostruito.
            # Questo non sostituisce un eventuale elenco proprietario GLS delle giacenze gestibili,
            # ma impedisce che una giacenza sparisca dalla gestione del monitor.
            if current_category == "STORAGE" and not open_stock:
                add(
                    "STORAGE_WITHOUT_CASE",
                    "STORAGE_WITHOUT_CASE",
                    "Giacenza rilevata ma non presente nella gestione giacenze",
                    f"Il tracking GLS risulta in giacenza ({current_state}), ma il monitor non trova una giacenza aperta associata.",
                    "Verificare subito sul portale GLS e segnalare alla sede se la giacenza non risulta gestibile.",
                )

            successful = [r for r in releases if int(r.get("gls_success") or 0) == 1]
            failed = [r for r in releases if int(r.get("gls_success") or 0) == 0]

            if failed and current_category == "STORAGE":
                last_failed = failed[-1]
                msg = str(last_failed.get("gls_result") or "")
                low = msg.lower()
                if any(t in low for t in ["giacenz", "non trov", "non presente", "non gest", "svincol"]):
                    add(
                        f"STOCK_NOT_MANAGEABLE:{last_failed['id']}",
                        "STOCK_NOT_MANAGEABLE",
                        "Giacenza non gestibile via API GLS",
                        f"GLS mantiene la spedizione in giacenza ma il tentativo di gestione API non e' stato accettato: {msg or 'risposta non disponibile'}.",
                        "Esportare/segnalare la pratica a GLS per allineare la giacenza e renderla gestibile.",
                        {"release_id": last_failed["id"]},
                    )

            if successful:
                rel = successful[-1]
                rel_dt = self._parse_dt(rel.get("created_at"))
                release_type = str(rel.get("release_type") or "")
                after_events = []
                if rel_dt:
                    for e in events:
                        edt = self._parse_dt(e.get("event_at") or e.get("created_at"))
                        if edt and edt > rel_dt:
                            after_events.append(e)
                after_categories = [str(e.get("category") or "").upper() for e in after_events]

                # L'operatore ha chiesto riconsegna, GLS avvia invece il rientro.
                if release_type in {"1", "2"} and "RETURN" in after_categories:
                    return_event = next((e for e in after_events if str(e.get("category") or "").upper() == "RETURN"), after_events[-1])
                    add(
                        f"REDELIVERY_BECAME_RETURN:{rel['id']}",
                        "REDELIVERY_BECAME_RETURN",
                        "Rientro al mittente non richiesto",
                        f"Il {rel.get('created_at')} l'operatore {rel.get('operator_name') or '—'} ha richiesto “{rel.get('release_label')}”, ma successivamente GLS ha processato la spedizione come rientro al mittente: {return_event.get('state') or return_event.get('note') or 'RETURN'}.",
                        "Contattare GLS con priorita massima e chiedere blocco/ripristino della consegna.",
                        {"release_id": rel["id"], "event_id": return_event.get("id")},
                    )

                # L'operatore ha chiesto rientro, ma GLS consegna al destinatario.
                if release_type == "3" and "DELIVERED" in after_categories:
                    delivered_event = next((e for e in after_events if str(e.get("category") or "").upper() == "DELIVERED"), after_events[-1])
                    add(
                        f"RETURN_BECAME_DELIVERY:{rel['id']}",
                        "RETURN_BECAME_DELIVERY",
                        "Consegna al cliente dopo richiesta di rientro",
                        f"Era stato richiesto il ritorno al mittente, ma GLS ha poi registrato una consegna al destinatario: {delivered_event.get('state') or 'Consegnata'}.",
                        "Verificare immediatamente con GLS e customer care.",
                        {"release_id": rel["id"], "event_id": delivered_event.get("id")},
                    )

                # Svincolo accettato ma nessun nuovo movimento dopo 12 ore e tracking ancora in giacenza.
                if release_type in {"1", "2"} and rel_dt and current_category == "STORAGE" and not after_events:
                    if (now - rel_dt).total_seconds() >= 12 * 3600:
                        add(
                            f"RELEASE_NOT_ACKNOWLEDGED:{rel['id']}",
                            "RELEASE_NOT_ACKNOWLEDGED",
                            "Svincolo non recepito dal tracking GLS",
                            f"Lo svincolo “{rel.get('release_label')}” e' stato accettato dall'API da oltre 12 ore, ma non risulta alcun nuovo evento GLS e la spedizione e' ancora in giacenza.",
                            "Sollecitare la sede GLS e verificare che la disposizione sia stata presa in carico.",
                            {"release_id": rel["id"]},
                        )

                # Data riconsegna superata senza evidenza di nuova consegna/movimento utile.
                if release_type in {"1", "2"}:
                    try:
                        request = json.loads(rel.get("request_json") or "{}")
                    except Exception:
                        request = {}
                    delivery_date = str(request.get("delivery_date") or "").strip()
                    if delivery_date:
                        try:
                            due = datetime.strptime(delivery_date[:10], "%Y-%m-%d").date()
                            today = datetime.now().date()
                            forward_categories = {"OUT_FOR_DELIVERY", "DELIVERED", "SCHEDULED", "IN_TRANSIT", "CORRESPONDENT"}
                            has_forward = any(c in forward_categories for c in after_categories)
                            if today > due and not has_forward:
                                add(
                                    f"REDELIVERY_DATE_MISSED:{rel['id']}",
                                    "REDELIVERY_DATE_MISSED",
                                    "Riconsegna richiesta ma non avviata",
                                    f"La riconsegna era stata richiesta per il {due.strftime('%d/%m/%Y')}, ma non risultano movimenti GLS coerenti successivi allo svincolo.",
                                    "Sollecitare GLS e verificare la disposizione di riconsegna.",
                                    {"release_id": rel["id"], "delivery_date": delivery_date},
                                )
                        except ValueError:
                            pass

            # Anomalia bloccante che resta ferma per almeno 24 ore senza diventare giacenza/finale.
            blocked_categories = {"ADDRESS_ERROR", "ACTION_REQUIRED", "DELIVERY_FAILURE", "REFUSED", "ABSENT", "COD_ISSUE", "DAMAGE_OR_LOSS"}
            if current_category in blocked_categories and current_event_at:
                age_h = (now - current_event_at).total_seconds() / 3600
                if age_h >= 24:
                    add(
                        f"BLOCKED_NO_STOCK:{shipment.get('gls_code') or shipment.get('gls_event_at') or current_category}",
                        "BLOCKED_NO_STOCK",
                        "Anomalia bloccata senza passaggio in giacenza",
                        f"Lo stato “{current_state}” e' fermo da circa {age_h:.0f} ore e non e' seguito da una giacenza gestibile o da un esito finale.",
                        "Segnalare la spedizione a GLS: il tracking richiede intervento ma la pratica non evolve nella gestione giacenze.",
                    )

            active_keys = {i["issue_key"] for i in issues}
            existing = conn.execute(
                "SELECT * FROM inconsistencies WHERE tracking_number=?", (tracking_number,)
            ).fetchall()
            now_s = utcnow()
            for issue in issues:
                conn.execute(
                    """
                    INSERT INTO inconsistencies(
                        tracking_number, issue_key, issue_code, title, detail, suggested_action,
                        severity, detected_at, last_seen_at, active, context_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,1,?)
                    ON CONFLICT(tracking_number,issue_key) DO UPDATE SET
                        issue_code=excluded.issue_code,
                        title=excluded.title,
                        detail=excluded.detail,
                        suggested_action=excluded.suggested_action,
                        severity=excluded.severity,
                        last_seen_at=excluded.last_seen_at,
                        resolved_at=NULL,
                        active=1,
                        context_json=excluded.context_json
                    """,
                    (
                        tracking_number, issue["issue_key"], issue["issue_code"], issue["title"],
                        issue["detail"], issue["suggested_action"], issue["severity"], now_s, now_s,
                        json.dumps(issue.get("context") or {}, ensure_ascii=False),
                    ),
                )
            for row in existing:
                if row["active"] and row["issue_key"] not in active_keys:
                    conn.execute(
                        "UPDATE inconsistencies SET active=0, resolved_at=?, last_seen_at=? WHERE id=?",
                        (now_s, now_s, row["id"]),
                    )

            if issues:
                # Una NUOVA revisione GLS successiva a una chiusura manuale puo riaprire la pratica.
                # Le incongruenze gia note invece restano risolte finche il tracking non cambia.
                conn.execute(
                    """UPDATE shipments
                       SET workflow_status='NEW', workflow_updated_at=?, manual_closed_revision=NULL
                       WHERE tracking_number=? AND workflow_status IN ('RESOLVED','IGNORED')""",
                    (now_s, tracking_number),
                )

            rows = conn.execute(
                "SELECT * FROM inconsistencies WHERE tracking_number=? AND active=1 ORDER BY detected_at ASC, id ASC",
                (tracking_number,),
            ).fetchall()
            return [dict(r) for r in rows]

    def reconcile_all_inconsistencies(self) -> int:
        with self.connect() as conn:
            rows = conn.execute("SELECT tracking_number FROM shipments").fetchall()
        for row in rows:
            self.reconcile_inconsistencies(row["tracking_number"])
        return len(rows)

    def list_inconsistencies(self, active_only: bool = True) -> list[dict[str, Any]]:
        where = "i.active=1 AND s.workflow_status NOT IN ('RESOLVED','IGNORED')" if active_only else "1=1"
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT i.*, s.order_name, s.order_created_at, s.customer_name, s.customer_phone,
                       s.city, s.province, s.gls_status, s.gls_note, s.gls_event_at,
                       s.last_operator_action, s.last_operator_action_at, s.last_operator_name,
                       s.workflow_status, s.is_cod
                FROM inconsistencies i
                JOIN shipments s ON s.tracking_number=i.tracking_number
                WHERE {where}
                ORDER BY i.active DESC, i.detected_at DESC, i.id DESC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def stock_history(self) -> dict[str, Any]:
        self.reconcile_all_stock_cases()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT sc.*, s.order_name, s.order_created_at, s.customer_name, s.customer_phone,
                       s.city, s.province, s.total_amount, s.currency, s.is_cod, s.gls_status,
                       s.gls_note, s.gls_event_at, s.workflow_status, s.last_operator_action,
                       s.last_operator_action_at, s.last_operator_name, s.closed
                FROM stock_cases sc
                JOIN shipments s ON s.tracking_number=sc.tracking_number
                ORDER BY COALESCE(sc.entered_at, sc.created_at) DESC, sc.id DESC
                """
            ).fetchall()
            cases: list[dict[str, Any]] = []
            summary = {"total": 0, "open": 0, "handled": 0, "delivered": 0, "returned": 0, "other": 0}
            for row in rows:
                item = dict(row)
                releases = conn.execute(
                    "SELECT * FROM gls_release_requests WHERE stock_case_id=? ORDER BY created_at DESC, id DESC",
                    (item["id"],),
                ).fetchall()
                release_list = [dict(r) for r in releases]
                start = item.get("entered_at") or item.get("created_at")
                end = item.get("exited_at")
                if end:
                    actions = conn.execute(
                        """
                        SELECT * FROM operator_actions
                        WHERE tracking_number=? AND created_at>=? AND created_at<=?
                        ORDER BY created_at DESC, id DESC
                        """,
                        (item["tracking_number"], start, end),
                    ).fetchall()
                else:
                    actions = conn.execute(
                        """
                        SELECT * FROM operator_actions
                        WHERE tracking_number=? AND created_at>=?
                        ORDER BY created_at DESC, id DESC
                        """,
                        (item["tracking_number"], start),
                    ).fetchall()
                action_list = [dict(a) for a in actions]
                item["release_requests"] = release_list
                item["operator_actions"] = action_list
                item["handled"] = bool(release_list or action_list)
                item["latest_instruction"] = release_list[0] if release_list else None
                item["latest_action"] = action_list[0] if action_list else None
                outcome = (item.get("outcome_category") or "").upper()
                if item.get("status") == "OPEN":
                    item["outcome_label"] = "Ancora in giacenza"
                elif outcome == "DELIVERED":
                    item["outcome_label"] = "Consegnata"
                elif outcome == "RETURN":
                    item["outcome_label"] = "Rientro al mittente"
                elif outcome in {"OUT_FOR_DELIVERY", "SCHEDULED", "IN_TRANSIT", "CORRESPONDENT", "SERVICE_AREA", "LINEHAUL_DELAY"}:
                    item["outcome_label"] = "Ripartita / riconsegna"
                else:
                    item["outcome_label"] = item.get("outcome_state") or "Esito da verificare"

                summary["total"] += 1
                if item.get("status") == "OPEN": summary["open"] += 1
                if item["handled"]: summary["handled"] += 1
                if outcome == "DELIVERED": summary["delivered"] += 1
                elif outcome == "RETURN": summary["returned"] += 1
                elif item.get("status") != "OPEN": summary["other"] += 1
                cases.append(item)
            return {"summary": summary, "cases": cases}

    def stock_case_detail(self, case_id: int) -> dict[str, Any] | None:
        data = self.stock_history()
        for item in data["cases"]:
            if int(item["id"]) == int(case_id):
                tracking = item["tracking_number"]
                with self.connect() as conn:
                    events = conn.execute(
                        """
                        SELECT * FROM events WHERE tracking_number=?
                        AND COALESCE(event_at,created_at) >= ?
                        ORDER BY COALESCE(event_at,created_at) ASC, id ASC
                        """,
                        (tracking, item.get("entered_at") or item.get("created_at")),
                    ).fetchall()
                item["events_after_stock"] = [dict(e) for e in events]
                return item
        return None

    @staticmethod
    def _shipment_row(item: dict[str, Any]) -> dict[str, Any]:
        try:
            item["payment_gateways"] = json.loads(item.get("payment_gateways") or "[]")
        except Exception:
            item["payment_gateways"] = []
        item["is_cod"] = bool(item.get("is_cod"))
        item["closed"] = bool(item.get("closed"))
        return item
