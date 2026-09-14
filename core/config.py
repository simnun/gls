from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .auth import User, derive_secret, load_users

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def load_env(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "si", "sì"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except Exception:
        return default


@dataclass(frozen=True)
class Config:
    root: Path
    db_path: Path
    rules_path: Path
    static_dir: Path
    mock_dir: Path

    shopify_shop: str
    shopify_client_id: str
    shopify_client_secret: str
    shopify_api_version: str
    shopify_lookback_days: int

    gls_site: str
    gls_customer_code: str
    gls_contract_code: str
    gls_password: str
    gls_track_endpoint: str
    gls_list_endpoint: str
    gls_release_endpoint: str
    gls_accept_unlabeled_tracking: bool

    auto_sync: bool
    sync_interval_minutes: int
    sync_workers: int
    sync_max_tracking: int
    gls_public_workers: int
    gls_retry_attempts: int
    request_timeout_seconds: int
    app_host: str
    app_port: int
    dashboard_user: str
    dashboard_password: str
    mock_mode: bool

    # Accesso operatori
    dashboard_users: dict[str, User]
    session_secret: bytes
    session_days: int
    session_bind_ip: bool

    # Deploy online
    database_url: str
    serverless: bool
    public_deployment: bool
    cron_secret: str

    @property
    def shopify_configured(self) -> bool:
        return bool(self.shopify_shop and self.shopify_client_id and self.shopify_client_secret)

    @property
    def gls_tracking_configured(self) -> bool:
        return bool(self.gls_site and (self.gls_contract_code or self.gls_customer_code))

    @property
    def gls_list_configured(self) -> bool:
        return bool(self.gls_site and self.gls_customer_code and self.gls_password)

    @property
    def dashboard_auth_enabled(self) -> bool:
        return bool(self.dashboard_users) or bool(self.dashboard_user and self.dashboard_password)

    @property
    def multi_user(self) -> bool:
        return bool(self.dashboard_users)

    @property
    def session_max_age(self) -> int:
        return max(0, self.session_days) * 86400

    @property
    def uses_postgres(self) -> bool:
        return bool(self.database_url)

    @property
    def storage_misconfigured(self) -> bool:
        """Deploy effimero senza database esterno.

        Su serverless il disco non sopravvive alla richiesta: SQLite
        significherebbe perdere ogni nota e ogni giacenza a ogni chiamata.
        """
        return self.serverless and not self.uses_postgres

    @property
    def auth_required_but_missing(self) -> bool:
        """Deploy raggiungibile da internet senza credenziali impostate.

        In quel caso il monitor non deve servire nulla: contiene dati cliente.
        """
        return self.public_deployment and not self.dashboard_auth_enabled


def _database_url() -> str:
    """DSN Postgres: DATABASE_URL, oppure le variabili create dall'integrazione
    Supabase/Vercel. Vuoto significa SQLite locale."""
    for name in ("DATABASE_URL", "POSTGRES_URL_NON_POOLING", "POSTGRES_URL", "SUPABASE_DB_URL"):
        value = os.getenv(name, "").strip()
        if value:
            # psycopg non riconosce lo schema "postgres://" usato da alcuni provider.
            if value.startswith("postgres://"):
                value = "postgresql://" + value[len("postgres://"):]
            # Uno schema dedicato permette di riusare un progetto Supabase
            # gia' occupato da un'altra applicazione senza mischiare le tabelle.
            schema = os.getenv("DATABASE_SCHEMA", "").strip()
            if schema:
                from .pgcompat import apply_schema

                value = apply_schema(value, schema)
            return value
    return ""


def get_config() -> Config:
    load_env()
    root = ROOT
    database_url = _database_url()
    users_raw = os.getenv("DASHBOARD_USERS", "")
    dashboard_users = load_users(users_raw)
    # Su Vercel il processo e' effimero: niente scheduler in background, niente disco.
    serverless = env_bool("SERVERLESS", bool(os.getenv("VERCEL")))
    # In container (Fly/Render/Railway) la porta arriva da PORT: li' serve 0.0.0.0.
    default_host = "0.0.0.0" if os.getenv("PORT") else "127.0.0.1"
    return Config(
        root=root,
        db_path=root / "data" / "monitor.sqlite3",
        rules_path=root / "config" / "rules.json",
        static_dir=root / "static",
        mock_dir=root / "mock",
        shopify_shop=os.getenv("SHOPIFY_SHOP", "").strip().replace(".myshopify.com", ""),
        shopify_client_id=os.getenv("SHOPIFY_CLIENT_ID", "").strip(),
        shopify_client_secret=os.getenv("SHOPIFY_CLIENT_SECRET", "").strip(),
        shopify_api_version=os.getenv("SHOPIFY_API_VERSION", "2026-07").strip(),
        shopify_lookback_days=env_int("SHOPIFY_LOOKBACK_DAYS", 21),
        gls_site=os.getenv("GLS_SITE", "").strip(),
        gls_customer_code=os.getenv("GLS_CUSTOMER_CODE", "").strip(),
        gls_contract_code=os.getenv("GLS_CONTRACT_CODE", "").strip(),
        gls_password=os.getenv("GLS_PASSWORD", "").strip(),
        gls_track_endpoint=os.getenv(
            "GLS_TRACK_ENDPOINT",
            "https://wwwdr.gls-italy.com/XML/get_xml_track.php",
        ).strip(),
        gls_list_endpoint=os.getenv(
            "GLS_LIST_ENDPOINT",
            "https://labelservice.gls-italy.com/ilswebservice.asmx/ListSped",
        ).strip(),
        gls_release_endpoint=os.getenv(
            "GLS_RELEASE_ENDPOINT",
            "https://labelservice.gls-italy.com/ilswebservice.asmx/ReleaseShipmentStock",
        ).strip(),
        gls_accept_unlabeled_tracking=env_bool("GLS_ACCEPT_UNLABELED_TRACKING", False),
        auto_sync=env_bool("AUTO_SYNC", True),
        sync_interval_minutes=max(1, env_int("SYNC_INTERVAL_MINUTES", 10)),
        sync_workers=max(1, min(8, env_int("SYNC_WORKERS", 4))),
        # 0 = nessun limite. Serve dove la richiesta ha un tempo massimo (Vercel).
        sync_max_tracking=max(0, env_int("SYNC_MAX_TRACKING", 0)),
        gls_public_workers=max(1, min(4, env_int("GLS_PUBLIC_WORKERS", 2))),
        gls_retry_attempts=max(1, min(5, env_int("GLS_RETRY_ATTEMPTS", 3))),
        request_timeout_seconds=max(5, env_int("REQUEST_TIMEOUT_SECONDS", 20)),
        app_host=os.getenv("APP_HOST", default_host).strip(),
        app_port=env_int("PORT", env_int("APP_PORT", 8787)),
        dashboard_user=os.getenv("DASHBOARD_USER", "").strip(),
        dashboard_password=os.getenv("DASHBOARD_PASSWORD", "").strip(),
        mock_mode=env_bool("MOCK_MODE", False),
        dashboard_users=dashboard_users,
        session_secret=derive_secret(users_raw, os.getenv("SESSION_SECRET", "")),
        # 0 = la sessione non scade da sola.
        session_days=max(0, env_int("SESSION_DAYS", 30)),
        session_bind_ip=env_bool("SESSION_BIND_IP", True),
        database_url=database_url,
        serverless=serverless,
        public_deployment=env_bool("PUBLIC_DEPLOYMENT", serverless),
        cron_secret=os.getenv("CRON_SECRET", "").strip(),
    )
