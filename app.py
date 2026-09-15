#!/usr/bin/env python3
from __future__ import annotations

import base64
import hmac
import json
import mimetypes
import re
import signal
import subprocess
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from datetime import datetime, timezone

from core import auth
from core.sync import _parse_iso
from core.config import get_config
from core.wsgi import make_wsgi_app
from core.db import Database
from core.sync import SyncEngine
from core.xlsx_export import build_xlsx

CONFIG = get_config()


class _Pigro:
    """Costruzione differita alla prima richiesta.

    All'import non si deve toccare ne' la rete ne' il disco: su un hosting
    serverless un errore li' fa morire la funzione prima che possa rispondere,
    e si ottiene un 500 generico senza alcuna indicazione della causa.
    """

    def __init__(self, fabbrica):
        self._fabbrica = fabbrica
        self._istanza = None
        self._lock = threading.Lock()

    def risolvi(self):
        if self._istanza is None:
            with self._lock:
                if self._istanza is None:
                    self._istanza = self._fabbrica()
        return self._istanza

    def __getattr__(self, nome):
        return getattr(self.risolvi(), nome)


def _apri_database() -> Database:
    if CONFIG.storage_misconfigured:
        raise RuntimeError(
            "DATABASE_URL non impostata. Su un hosting serverless il disco non "
            "sopravvive alla singola richiesta: senza un database esterno ogni "
            "nota, giacenza e svincolo andrebbe perso. Configura Postgres/Supabase."
        )
    return Database(
        None if CONFIG.uses_postgres else CONFIG.db_path,
        dsn=CONFIG.database_url or None,
    )


DB = _Pigro(_apri_database)
ENGINE = _Pigro(lambda: SyncEngine(CONFIG, DB.risolvi()))


def _senza_credenziali(testo: str) -> str:
    """Toglie le credenziali dagli URL prima di mostrare un messaggio d'errore."""
    return re.sub(r"(://)[^/\s:@]+:[^/\s@]+@", r"\1***:***@", str(testo))


class AppHandler(BaseHTTPRequestHandler):
    server_version = "GLSExceptionMonitor/1.0"

    def log_message(self, fmt: str, *args) -> None:
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    SESSION_COOKIE = "gls_session"

    def _client_ip(self) -> str:
        return auth.client_ip_from_headers(self.headers, self.client_address[0] if self.client_address else "")

    def _session_user(self) -> auth.User | None:
        """Operatore della sessione, se il cookie e' valido."""
        if not CONFIG.multi_user:
            return None
        cookies = auth.parse_cookies(self.headers.get("Cookie", ""))
        token = cookies.get(self.SESSION_COOKIE, "")
        if not token:
            return None
        username = auth.read_session(
            token,
            CONFIG.session_secret,
            max_age_seconds=CONFIG.session_max_age,
            client_ip=self._client_ip(),
            bind_ip=CONFIG.session_bind_ip,
        )
        if not username:
            return None
        return CONFIG.dashboard_users.get(username)

    def _set_session_cookie(self, token: str, max_age: int) -> None:
        secure = "; Secure" if self._is_https() else ""
        # Max-Age=0 cancella il cookie.
        self.send_header(
            "Set-Cookie",
            f"{self.SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}{secure}",
        )

    def _is_https(self) -> bool:
        proto = (self.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        return proto == "https" or CONFIG.public_deployment

    def _authorized(self) -> bool:
        if not CONFIG.dashboard_auth_enabled:
            return True
        if self._session_user() is not None:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        if CONFIG.multi_user:
            # Anche via Basic si accede con le credenziali di un operatore.
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                username, password = decoded.split(":", 1)
            except Exception:
                return False
            return auth.authenticate(CONFIG.dashboard_users, username, password) is not None
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            user, password = decoded.split(":", 1)
        except Exception:
            return False
        # Confronto a tempo costante: su internet il servizio e' esposto a tentativi ripetuti.
        return (
            hmac.compare_digest(user, CONFIG.dashboard_user)
            and hmac.compare_digest(password, CONFIG.dashboard_password)
        )

    def _cron_authorized(self) -> bool:
        """Vercel Cron si autentica con `Authorization: Bearer $CRON_SECRET`."""
        if not CONFIG.cron_secret:
            return False
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[7:].strip(), CONFIG.cron_secret)

    def _require_auth(self) -> bool:
        if CONFIG.storage_misconfigured:
            self._json(
                {"error": "DATABASE_URL non impostata: il deploy online richiede "
                          "un database esterno.",
                 "hint": "Controlla /api/status."},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return False
        # Un deploy pubblico senza credenziali esporrebbe dati cliente: non si serve nulla.
        if CONFIG.auth_required_but_missing:
            self._json(
                {
                    "error": "Monitor esposto su internet senza credenziali. "
                             "Imposta DASHBOARD_USER e DASHBOARD_PASSWORD e ridistribuisci.",
                },
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return False
        if self._authorized():
            return True
        # Con gli operatori configurati si passa dalla pagina di accesso:
        # il popup del browser non permette di uscire ne' di restare collegati.
        if CONFIG.multi_user:
            if urlparse(self.path).path.startswith("/api/"):
                self._json({"error": "Sessione scaduta o assente", "login_required": True},
                           HTTPStatus.UNAUTHORIZED)
            else:
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/login")
                self.send_header("Content-Length", "0")
                self.end_headers()
            return False
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="GLS Exception Monitor"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _json(self, payload, status=200) -> None:
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _bytes(self, payload: bytes, content_type: str, filename: str | None = None, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 64 * 1024:
            raise ValueError("Payload troppo grande")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        try:
            return self._do_GET()
        except Exception as exc:
            return self._json(
                {"error": f"{type(exc).__name__}: {_senza_credenziali(exc)}",
                 "hint": "Controlla /api/status per lo stato della configurazione."},
                500,
            )

    def _do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        # Diagnostica: deve rispondere anche quando la configurazione e' incompleta,
        # altrimenti un guasto all'avvio resta invisibile dall'esterno.
        if path == "/api/status":
            return self._handle_status()
        # Il cron esterno si autentica con CRON_SECRET, non con le credenziali dashboard.
        if path == "/api/cron/sync":
            return self._handle_cron_sync()
        # La pagina di accesso deve essere raggiungibile da chi non e' ancora entrato.
        if path == "/login":
            if CONFIG.multi_user and self._session_user() is not None:
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            return self._serve_static("/login.html")
        if not self._require_auth():
            return

        if path == "/api/me":
            user = self._session_user()
            return self._json({
                "authenticated": True,
                "multi_user": CONFIG.multi_user,
                "user": user.public() if user else None,
            })
        if path == "/api/dashboard":
            qs = parse_qs(parsed.query)
            include_closed = (qs.get("include_closed", ["false"])[0].lower() == "true")
            payload = DB.dashboard(include_closed=include_closed)
            payload["sync_running"] = ENGINE.running
            payload["config"] = self._safe_config()
            return self._json(payload)

        if path == "/api/stocks":
            return self._json(DB.stock_history())

        if path.startswith("/api/stock-case/"):
            try:
                case_id = int(path[len("/api/stock-case/"):].strip("/"))
            except ValueError:
                return self._json({"error": "ID giacenza non valido"}, 400)
            item = DB.stock_case_detail(case_id)
            if not item:
                return self._json({"error": "Giacenza non trovata"}, 404)
            return self._json(item)

        if path.startswith("/api/shipment/"):
            tracking = unquote(path[len("/api/shipment/"):])
            item = DB.get_shipment(tracking)
            if not item:
                return self._json({"error": "Spedizione non trovata"}, 404)
            item["links"] = self._links(item)
            return self._json(item)

        if path == "/api/inconsistencies":
            # Niente riconciliazione qui: la fa gia' la sincronizzazione su ogni
            # spedizione che tocca, e ogni azione operatore sulla propria. Rifarla
            # per tutte a ogni apertura di pagina significa migliaia di query verso
            # il database: in locale su SQLite non si notava, in rete la pagina non
            # arrivava mai a caricarsi.
            return self._json({"items": DB.list_inconsistencies(active_only=True)})

        if path == "/api/inconsistencies/export.xlsx":
            items = DB.list_inconsistencies(active_only=True)
            headers = [
                "Priorita", "Codice incongruenza", "Ordine", "Tracking GLS", "Cliente", "Telefono",
                "Problema", "Dettaglio", "Azione consigliata", "Stato GLS", "Ultimo evento GLS",
                "Ultima attivita operatore", "Operatore", "Data attivita", "Rilevata il"
            ]
            rows = [[
                "MASSIMA", x.get("issue_code") or "", x.get("order_name") or "", x.get("tracking_number") or "",
                x.get("customer_name") or "", x.get("customer_phone") or "", x.get("title") or "",
                x.get("detail") or "", x.get("suggested_action") or "", x.get("gls_status") or x.get("gls_note") or "",
                x.get("gls_event_at") or "", x.get("last_operator_action") or "", x.get("last_operator_name") or "",
                x.get("last_operator_action_at") or "", x.get("detected_at") or ""
            ] for x in items]
            raw = build_xlsx(headers, rows, "Incongruenze GLS")
            return self._bytes(
                raw,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "GLS_incongruenze.xlsx",
            )

        if path == "/api/rules":
            rules = json.loads(CONFIG.rules_path.read_text(encoding="utf-8"))
            return self._json({"rules": rules.get("rules", []), "overrides": DB.list_overrides()})

        if path == "/api/config":
            return self._json(self._safe_config())

        if path == "/api/health":
            return self._json({"ok": True, "sync_running": ENGINE.running, "last_sync": DB.latest_sync()})

        if path == "/api/sync/progress":
            if CONFIG.serverless:
                return self._json(self._progress_from_db())
            return self._json(ENGINE.get_progress())

        if path == "/api/diagnostics":
            result = {
                "shopify": {"configured": CONFIG.shopify_configured},
                "gls": {"tracking_configured": CONFIG.gls_tracking_configured, "list_configured": CONFIG.gls_list_configured},
            }
            if CONFIG.shopify_configured and not CONFIG.mock_mode:
                try:
                    token = ENGINE.shopify.get_token()
                    result["shopify"]["auth_ok"] = bool(token)
                except Exception as exc:
                    result["shopify"]["auth_ok"] = False
                    result["shopify"]["error"] = str(exc)
            if CONFIG.gls_list_configured and not CONFIG.mock_mode:
                try:
                    gls_diag = ENGINE.gls.diagnostics()
                    result["gls"].update(gls_diag)
                except Exception as exc:
                    result["gls"]["list_sped_ok"] = False
                    result["gls"]["error"] = str(exc)
            return self._json(result)

        return self._serve_static(path)

    def do_POST(self) -> None:
        try:
            return self._do_POST()
        except Exception as exc:
            return self._json(
                {"error": f"{type(exc).__name__}: {_senza_credenziali(exc)}",
                 "hint": "Controlla /api/status per lo stato della configurazione."},
                500,
            )

    def _do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/login":
            return self._handle_login()
        if path == "/api/logout":
            return self._handle_logout()

        if not self._require_auth():
            return
        try:
            if path == "/api/open-incognito":
                body = self._read_json()
                tracking = str(body.get("tracking") or "").strip()
                if not tracking:
                    return self._json({"error": "Tracking obbligatorio"}, 400)
                url = "https://gls-group.com/IT/it/servizi-online/ricerca-spedizioni?match=" + quote(tracking) + "&type=NAT"
                if sys.platform != "darwin":
                    return self._json({"error": "Apertura incognito automatica disponibile nella build Mac"}, 400)
                try:
                    subprocess.Popen(
                        ["open", "-na", "Google Chrome", "--args", "--incognito", url],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                except Exception as exc:
                    return self._json({"error": f"Impossibile aprire Chrome in incognito: {exc}"}, 500)
                return self._json({"ok": True, "url": url})

            if path == "/api/sync":
                if CONFIG.serverless:
                    result = ENGINE.run_sync()
                    if not result.get("ok", True):
                        return self._json({"ok": False, "message": result.get("message", "")}, 409)
                    return self._json({
                        "ok": True,
                        "done": True,
                        "message": "Sincronizzazione completata",
                        "result": result,
                    })
                started = ENGINE.run_sync_async()
                if not started:
                    return self._json({"ok": False, "message": "Sincronizzazione gia in corso"}, 409)
                return self._json({"ok": True, "message": "Sincronizzazione avviata"}, 202)

            if path.startswith("/api/shipment/") and path.endswith("/workflow"):
                tracking = unquote(path[len("/api/shipment/"):-len("/workflow")]).strip("/")
                body = self._read_json()
                DB.update_workflow(
                    tracking,
                    str(body.get("workflow_status") or "").upper(),
                    body.get("operator_note"),
                    str(body.get("operator_name") or "").strip(),
                )
                DB.reconcile_inconsistencies(tracking)
                return self._json({"ok": True})

            if path.startswith("/api/shipment/") and path.endswith("/release-stock"):
                tracking = unquote(path[len("/api/shipment/"):-len("/release-stock")]).strip("/")
                body = self._read_json()
                operator_name = str(body.get("operator_name") or "").strip()
                if not operator_name:
                    return self._json({"error": "Nome operatore obbligatorio"}, 400)
                request_data = {
                    key: value for key, value in body.items()
                    if key not in {"operator_name"}
                }
                release_type = str(body.get("release_type") or "")
                release_labels = {
                    "1": "Riconsegna allo stesso indirizzo",
                    "2": "Consegna a un indirizzo diverso",
                    "3": "Rientro al mittente",
                    "4": "Distruzione",
                    "7": "Ritiro del destinatario presso la sede GLS",
                    "8": "Consegna parziale e rientro",
                    "9": "Consegna parziale e distruzione",
                }
                try:
                    result = ENGINE.gls.release_shipment_stock(tracking, body)
                    success = bool(result["success"])
                    result_text = str(result["result"])
                    raw_response = str(result["raw_response"])
                    release_label = result["release_label"]
                except ValueError:
                    raise
                except Exception as exc:
                    success = False
                    result_text = str(exc)
                    raw_response = ""
                    release_label = release_labels.get(release_type, "Istruzione GLS")
                saved = DB.record_gls_release_request(
                    tracking_number=tracking,
                    release_type=release_type,
                    release_label=release_label,
                    operator_name=operator_name,
                    note=str(body.get("note") or ""),
                    request_data=request_data,
                    gls_success=success,
                    gls_result=result_text,
                    gls_raw_response=raw_response,
                )
                DB.reconcile_inconsistencies(tracking)
                status = 200 if success else 409
                return self._json({
                    "ok": success,
                    "message": result_text,
                    "release": saved,
                    "release_label": release_label,
                }, status)

            if path.startswith("/api/shipment/") and path.endswith("/action"):
                tracking = unquote(path[len("/api/shipment/"):-len("/action")]).strip("/")
                body = self._read_json()
                action_type = str(body.get("action_type") or "").upper().strip()
                actions = {
                    "CUSTOMER_MESSAGE": ("Messaggio inviato al cliente", "IN_PROGRESS"),
                    "CUSTOMER_CALLED": ("Cliente contattato", "IN_PROGRESS"),
                    "GLS_CONTACTED": ("Sede GLS contattata", "IN_PROGRESS"),
                    "RELEASE_REQUESTED": ("SVINCOLO Ritenta consegna", "IN_PROGRESS"),
                    "STOCK_MANUAL_HANDLED": ("Giacenza gestita esternamente", "IN_PROGRESS"),
                    "DELIVERY_RESCHEDULED": ("SVINCOLO Ritorno al mittente", "IN_PROGRESS"),
                    # Scrivere una nota e' prendere in carico la pratica, esattamente
                    # come contattare il cliente o la sede GLS: era l'unica azione a
                    # lasciare la pratica fra quelle ancora da verificare.
                    "NOTE": ("Nota operativa", "IN_PROGRESS"),
                    "RESOLVED_MANUALLY": ("Pratica risolta manualmente", "RESOLVED"),
                }
                if action_type not in actions:
                    return self._json({"error": "Azione non valida"}, 400)
                label, default_workflow = actions[action_type]
                workflow = str(body.get("workflow_status") or default_workflow or "").upper().strip() or None
                action = DB.add_operator_action(
                    tracking_number=tracking,
                    action_type=action_type,
                    action_label=label,
                    note=str(body.get("note") or ""),
                    operator_name=str(body.get("operator_name") or "").strip(),
                    workflow_status=workflow,
                )
                DB.reconcile_inconsistencies(tracking)
                return self._json({"ok": True, "action": action})

            if path == "/api/classification":
                body = self._read_json()
                code = str(body.get("event_code") or "").strip()
                severity = str(body.get("severity") or "").upper()
                category = str(body.get("category") or "").upper().strip()
                if not code:
                    return self._json({"error": "event_code obbligatorio"}, 400)
                if severity not in {"NORMAL", "INFO", "WATCH", "WARNING", "CRITICAL"}:
                    return self._json({"error": "severity non valida"}, 400)
                if not category:
                    return self._json({"error": "category obbligatoria"}, 400)
                DB.set_override(
                    code,
                    severity,
                    category,
                    str(body.get("recommended_action") or ""),
                    str(body.get("note") or ""),
                )
                return self._json({"ok": True})

            return self._json({"error": "Endpoint non trovato"}, 404)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        except Exception as exc:
            return self._json({"error": str(exc)}, 500)

    def _serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            path = "/index.html"
        relative = Path(unquote(path.lstrip("/")))
        if ".." in relative.parts:
            self.send_error(403)
            return
        target = (CONFIG.static_dir / relative).resolve()
        try:
            target.relative_to(CONFIG.static_dir.resolve())
        except ValueError:
            self.send_error(403)
            return
        if not target.exists() or not target.is_file():
            self.send_error(404)
            return
        raw = target.read_bytes()
        content_type, _ = mimetypes.guess_type(str(target))
        self.send_response(200)
        self.send_header("Content-Type", (content_type or "application/octet-stream") + ("; charset=utf-8" if content_type and content_type.startswith("text/") else ""))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle_status(self) -> None:
        """Stato dell'installazione, senza rivelare alcun segreto.

        Riporta solo se le varie configurazioni sono presenti e se il database
        risponde: serve a capire perche' il monitor non parte, senza dover
        accedere ai log della piattaforma.
        """
        from core.timezones import tzdata_available

        stato: dict = {
            "ok": True,
            "python": sys.version.split()[0],
            "tzdata": tzdata_available(),
            "serverless": CONFIG.serverless,
            "storage": "postgres" if CONFIG.uses_postgres else "sqlite",
            "configurato": {
                "database_url": bool(CONFIG.database_url),
                "operatori": len(CONFIG.dashboard_users),
                "shopify": CONFIG.shopify_configured,
                "gls_tracking": CONFIG.gls_tracking_configured,
                "gls_svincoli": CONFIG.gls_list_configured,
                "cron_secret": bool(CONFIG.cron_secret),
                "mock_mode": CONFIG.mock_mode,
            },
        }
        try:
            DB.risolvi()
            stato["database"] = "raggiungibile"
        except Exception as exc:
            stato["ok"] = False
            stato["database"] = "errore"
            stato["database_errore"] = f"{type(exc).__name__}: {_senza_credenziali(exc)}"[:400]

        if CONFIG.auth_required_but_missing:
            stato["ok"] = False
            stato["avviso"] = "Nessun operatore configurato: imposta DASHBOARD_USERS."
        return self._json(stato, 200 if stato["ok"] else 503)

    def _handle_login(self) -> None:
        if not CONFIG.multi_user:
            return self._json({"error": "Accesso per operatori non configurato"}, 400)
        try:
            body = self._read_json()
        except Exception:
            return self._json({"error": "Richiesta non valida"}, 400)

        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "")
        user = auth.authenticate(CONFIG.dashboard_users, username, password)
        if user is None:
            # Nessun dettaglio su cosa fosse sbagliato: eviterebbe di rivelare
            # quali indirizzi esistono.
            return self._json({"error": "Email o password non corretti"}, 401)

        token = auth.create_session(
            user.username,
            CONFIG.session_secret,
            self._client_ip() if CONFIG.session_bind_ip else "",
        )
        payload = json.dumps({"ok": True, "user": user.public()}, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        # Senza scadenza esplicita il cookie dura un anno: l'accesso resta
        # valido nel tempo dalla stessa rete.
        self._set_session_cookie(token, CONFIG.session_max_age or 365 * 86400)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _handle_logout(self) -> None:
        payload = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self._set_session_cookie("", 0)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _handle_cron_sync(self) -> None:
        """Sincronizzazione pianificata, chiamata da un cron esterno."""
        if not (self._cron_authorized() or (not CONFIG.auth_required_but_missing and self._authorized())):
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            result = ENGINE.run_sync()
        except Exception as exc:
            return self._json({"ok": False, "error": str(exc)}, 500)
        return self._json({"ok": bool(result.get("ok", True)), "result": result})

    def _progress_from_db(self) -> dict:
        """Stato ricostruito dall'ultima sincronizzazione registrata.

        In serverless il progresso in memoria non sopravvive alla risposta e ogni
        richiesta puo' finire su un'istanza diversa: la fonte di verita' e' il database.
        """
        last = DB.latest_sync() or {}
        running = str(last.get("status") or "").upper() == "RUNNING"
        # In serverless nessuna sincronizzazione puo' durare piu' del tempo
        # massimo della richiesta: una riga ancora RUNNING oltre quella soglia
        # appartiene a un processo gia' terminato d'autorita'. Segnalarla come
        # attiva farebbe girare all'infinito la barra di avanzamento.
        if running:
            iniziata = _parse_iso(last.get("started_at"))
            limite = max(120, (CONFIG.sync_time_budget_seconds or 60) * 2)
            if iniziata is None or (datetime.now(timezone.utc) - iniziata).total_seconds() > limite:
                running = False
        total = int(last.get("tracking_numbers") or 0)
        success = int(last.get("gls_success") or 0)
        errors = int(last.get("gls_errors") or 0)
        processed = success + errors
        return {
            "running": running,
            "phase": "RUNNING" if running else ("DONE" if last else "IDLE"),
            "label": "Sincronizzazione in corso…" if running else "Pronto",
            "processed": processed,
            "total": total,
            "percent": int(processed * 100 / total) if total else 0,
            "success": success,
            "errors": errors,
            "eta_seconds": None,
            "current_tracking": None,
            "pending_pickup": int(last.get("pending_pickup") or 0),
            "no_event_attention": int(last.get("no_event_attention") or 0),
            "started_at": last.get("started_at"),
            "updated_at": last.get("finished_at") or last.get("started_at"),
        }

    def _safe_config(self) -> dict:
        return {
            "mock_mode": CONFIG.mock_mode,
            "shopify_configured": CONFIG.shopify_configured,
            "gls_tracking_configured": CONFIG.gls_tracking_configured,
            "gls_list_configured": CONFIG.gls_list_configured,
            "gls_release_configured": CONFIG.gls_list_configured and bool(CONFIG.gls_release_endpoint),
            "shopify_shop": CONFIG.shopify_shop if CONFIG.shopify_shop else None,
            "shopify_api_version": CONFIG.shopify_api_version,
            "lookback_days": CONFIG.shopify_lookback_days,
            "sync_interval_minutes": CONFIG.sync_interval_minutes,
            "sync_workers": CONFIG.sync_workers,
            "gls_public_workers": CONFIG.gls_public_workers,
            "gls_retry_attempts": CONFIG.gls_retry_attempts,
            "host": CONFIG.app_host,
            "port": CONFIG.app_port,
            "auth_enabled": CONFIG.dashboard_auth_enabled,
            "serverless": CONFIG.serverless,
            "storage": "postgres" if CONFIG.uses_postgres else "sqlite",
            "native_incognito": sys.platform == "darwin",
            "multi_user": CONFIG.multi_user,
            "current_user": (lambda u: u.public() if u else None)(self._session_user()),
        }

    def _links(self, item: dict) -> dict:
        tracking = item.get("tracking_number") or ""
        gls_url = (
            "https://gls-group.com/IT/it/servizi-online/ricerca-spedizioni"
            f"?match={quote(str(tracking))}&type=NAT"
        )
        shopify_url = None
        customer_url = None
        legacy = item.get("order_legacy_id")
        customer_legacy = item.get("customer_legacy_id")
        if CONFIG.shopify_shop and legacy:
            shopify_url = f"https://admin.shopify.com/store/{CONFIG.shopify_shop}/orders/{legacy}"
        if CONFIG.shopify_shop and customer_legacy:
            customer_url = f"https://admin.shopify.com/store/{CONFIG.shopify_shop}/customers/{customer_legacy}"
        return {"gls": gls_url, "shopify": shopify_url, "shopify_profile": customer_url}


# Applicazione WSGI: e' la forma attesa dal runtime Python di Vercel.
# In locale resta inutilizzata, il server parte da main().
app = make_wsgi_app(AppHandler)
application = app


def main() -> None:
    # In serverless il processo viene congelato dopo ogni risposta: la
    # sincronizzazione periodica la pianifica un cron esterno su /api/cron/sync.
    if CONFIG.auto_sync and not CONFIG.serverless:
        ENGINE.start_background_scheduler()

    server = ThreadingHTTPServer((CONFIG.app_host, CONFIG.app_port), AppHandler)
    print("\nGLS Exception Monitor")
    print(f"Dashboard: http://{CONFIG.app_host}:{CONFIG.app_port}")
    print(f"Modalita: {'DEMO' if CONFIG.mock_mode else 'REALE'}")
    if not CONFIG.mock_mode:
        if not CONFIG.shopify_configured:
            print("ATTENZIONE: Shopify non configurato. Esegui setup.command o compila .env.")
        if not CONFIG.gls_tracking_configured:
            print("ATTENZIONE: GLS non configurato. Esegui setup.command o compila .env.")
    print("Premi Ctrl+C per arrestare.\n")

    def stop_server(signum, frame):
        ENGINE.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, stop_server)
    signal.signal(signal.SIGTERM, stop_server)
    try:
        server.serve_forever()
    finally:
        ENGINE.stop()
        server.server_close()


if __name__ == "__main__":
    main()
