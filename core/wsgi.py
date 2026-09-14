"""Ponte WSGI davanti all'handler HTTP del monitor.

Il runtime Python di Vercel si aspetta un'applicazione WSGI o ASGI, mentre il
monitor e' scritto come BaseHTTPRequestHandler per poter girare in locale senza
alcuna dipendenza. Invece di duplicare le rotte in due forme diverse, qui la
richiesta WSGI viene ricomposta nel protocollo HTTP grezzo che l'handler sa gia'
leggere, e la risposta che produce viene ritradotta in WSGI.

Cosi esiste una sola implementazione delle rotte, valida sia in locale sia online.
"""
from __future__ import annotations

import io
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable, Iterable
from urllib.parse import quote

# Header che non devono essere ripetuti a valle: la piattaforma gestisce da se'
# la lunghezza del corpo e il riuso della connessione.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


class _Server:
    """Sostituto minimo dell'oggetto server atteso dall'handler."""

    def __init__(self, address: tuple[str, int]):
        self.server_address = address
        self.server_name = address[0]
        self.server_port = address[1]


def _request_target(environ: dict[str, Any]) -> str:
    """Percorso da mettere nella riga di richiesta.

    Si preferisce l'URI grezzo quando il server lo espone: `PATH_INFO` arriva
    gia' decodificato e l'handler decodifica a sua volta, quindi ricostruirlo
    da li' perderebbe le sequenze percento originali.
    """
    raw = environ.get("RAW_URI") or environ.get("REQUEST_URI")
    if raw:
        return str(raw)
    path = environ.get("PATH_INFO", "/") or "/"
    target = quote(path, safe="/%")
    query = environ.get("QUERY_STRING", "")
    return f"{target}?{query}" if query else target


def _raw_request(environ: dict[str, Any]) -> bytes:
    method = environ.get("REQUEST_METHOD", "GET")
    lines = [f"{method} {_request_target(environ)} HTTP/1.1"]

    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        length = 0
    body = environ["wsgi.input"].read(length) if length > 0 else b""

    headers: list[tuple[str, str]] = []
    if environ.get("CONTENT_TYPE"):
        headers.append(("Content-Type", environ["CONTENT_TYPE"]))
    headers.append(("Content-Length", str(len(body))))
    for key, value in environ.items():
        if not key.startswith("HTTP_"):
            continue
        name = key[5:].replace("_", "-").title()
        if name.lower() in HOP_BY_HOP or name.lower() == "content-length":
            continue
        headers.append((name, str(value)))

    for name, value in headers:
        lines.append(f"{name}: {value}")
    head = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
    return head + body


def _parse_response(raw: bytes) -> tuple[str, list[tuple[str, str]], bytes]:
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    # "HTTP/1.0 200 OK" -> "200 OK"
    status_line = lines[0] if lines else "HTTP/1.1 500 Internal Server Error"
    parts = status_line.split(" ", 2)
    status = " ".join(parts[1:]) if len(parts) > 1 else "500 Internal Server Error"

    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if not sep:
            continue
        if name.strip().lower() in HOP_BY_HOP:
            continue
        headers.append((name.strip(), value.strip()))
    return status, headers, body


def make_wsgi_app(handler_class: type[BaseHTTPRequestHandler]) -> Callable:
    """Applicazione WSGI che serve le rotte definite da `handler_class`."""

    class _Bridge(handler_class):  # type: ignore[valid-type,misc]
        # L'handler normalmente scrive su un socket: qui legge e scrive in memoria.
        def __init__(self, raw: bytes, client_address: tuple[str, int], server: _Server):
            self.rfile = io.BytesIO(raw)
            self.wfile = io.BytesIO()
            self.connection = None
            self.client_address = client_address
            self.server = server
            self.close_connection = True
            self.handle_one_request()

        def setup(self) -> None:  # gia' fatto nel costruttore
            pass

        def finish(self) -> None:  # non c'e' nessun socket da chiudere
            pass

        def address_string(self) -> str:
            return self.client_address[0]

    def application(environ: dict[str, Any], start_response: Callable) -> Iterable[bytes]:
        client = (environ.get("REMOTE_ADDR") or "127.0.0.1", 0)
        server = _Server((environ.get("SERVER_NAME", "localhost"),
                          int(environ.get("SERVER_PORT") or 0)))
        bridge = _Bridge(_raw_request(environ), client, server)
        status, headers, body = _parse_response(bridge.wfile.getvalue())
        start_response(status, headers)
        return [body]

    return application
