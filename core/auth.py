"""Utenti, password e sessioni della dashboard.

Gli operatori sono definiti nella variabile d'ambiente `DASHBOARD_USERS`, come
elenco JSON di oggetti con email, nome e hash della password. In chiaro non
viene mai salvata da nessuna parte: l'hash si genera con `genera_utenti.py`.

La sessione e' un cookie firmato, senza stato sul server: su un deploy
serverless non esiste una memoria condivisa tra le istanze dove tenere le
sessioni attive.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

# Parametri scrypt: costosi da forzare, trascurabili per un login occasionale.
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(password: str) -> str:
    """Hash scrypt con sale casuale, nel formato `scrypt$n$r$p$sale$hash`."""
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM, dklen=32,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64encode(salt)}${_b64encode(derived)}"


def verify_password(password: str, stored: str) -> bool:
    """Confronto a tempo costante contro un hash prodotto da hash_password."""
    try:
        algorithm, n, r, p, salt_b64, expected_b64 = stored.split("$")
        if algorithm != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"), salt=_b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p), maxmem=SCRYPT_MAXMEM, dklen=32,
        )
    except Exception:
        return False
    return hmac.compare_digest(derived, _b64decode(expected_b64))


@dataclass(frozen=True)
class User:
    username: str
    display_name: str
    password_hash: str

    def public(self) -> dict[str, str]:
        return {"username": self.username, "display_name": self.display_name}


def load_users(raw: str) -> dict[str, User]:
    """Legge DASHBOARD_USERS. Un contenuto non valido significa nessun utente."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        entries = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(entries, list):
        return {}

    users: dict[str, User] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        username = str(entry.get("username") or entry.get("u") or "").strip().lower()
        password_hash = str(entry.get("password_hash") or entry.get("h") or "").strip()
        if not username or not password_hash:
            continue
        display = str(entry.get("name") or entry.get("n") or "").strip() or username
        users[username] = User(username, display, password_hash)
    return users


def authenticate(users: dict[str, User], username: str, password: str) -> User | None:
    user = users.get((username or "").strip().lower())
    if user is None:
        # Si verifica comunque un hash fittizio: senza, il tempo di risposta
        # rivelerebbe quali indirizzi esistono.
        verify_password(password, hash_password("verifica-fittizia"))
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def derive_secret(users_raw: str, explicit: str = "") -> bytes:
    """Chiave per firmare i cookie.

    Deve essere identica su tutte le istanze, altrimenti un utente risulterebbe
    disconnesso ogni volta che la richiesta cade su un'istanza diversa: per
    questo non puo' essere casuale per processo. Se non e' impostata viene
    derivata dalla configurazione utenti, cosi il login funziona comunque.
    """
    if explicit.strip():
        return hashlib.sha256(explicit.strip().encode("utf-8")).digest()
    return hashlib.sha256(b"gls-monitor-session|" + users_raw.encode("utf-8")).digest()


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def create_session(username: str, secret: bytes, client_ip: str = "") -> str:
    payload = {"u": username, "t": int(time.time())}
    if client_ip:
        payload["ip"] = _fingerprint(client_ip)
    body = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _b64encode(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{signature}"


def read_session(
    token: str,
    secret: bytes,
    *,
    max_age_seconds: int,
    client_ip: str = "",
    bind_ip: bool = True,
) -> str | None:
    """Username del cookie, oppure None se non e' valido, scaduto o di altra rete."""
    if not token or "." not in token:
        return None
    body, _, signature = token.partition(".")
    expected = _b64encode(hmac.new(secret, body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload: dict[str, Any] = json.loads(_b64decode(body).decode("utf-8"))
    except Exception:
        return None

    issued = int(payload.get("t") or 0)
    if max_age_seconds > 0 and (time.time() - issued) > max_age_seconds:
        return None

    # La sessione resta valida solo dalla rete da cui e' stata aperta: un cookie
    # copiato altrove non apre la dashboard.
    if bind_ip and payload.get("ip"):
        if not client_ip or not hmac.compare_digest(str(payload["ip"]), _fingerprint(client_ip)):
            return None

    username = str(payload.get("u") or "").strip().lower()
    return username or None


def client_ip_from_headers(headers: Any, fallback: str = "") -> str:
    """IP reale del client: dietro a Vercel/Cloudflare arriva negli header."""
    for name in ("x-vercel-forwarded-for", "cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
        value = headers.get(name) if headers else None
        if value:
            # x-forwarded-for e' una catena: il primo e' il client.
            return str(value).split(",")[0].strip()
    return fallback


def parse_cookies(header: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for chunk in (header or "").split(";"):
        name, sep, value = chunk.partition("=")
        if sep:
            cookies[name.strip()] = value.strip()
    return cookies


def users_from_env() -> tuple[dict[str, User], str]:
    raw = os.getenv("DASHBOARD_USERS", "")
    return load_users(raw), raw
