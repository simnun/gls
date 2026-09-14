"""Il ponte WSGI deve servire le stesse rotte dell'handler HTTP.

E' la forma con cui l'applicazione gira online: se qui qualcosa si perde
(percorso, query string, corpo, header, codice di stato) il deploy risponde
in modo diverso dal locale.
"""
import io
import sys
import unittest
from http.server import BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.wsgi import make_wsgi_app  # noqa: E402


class EchoHandler(BaseHTTPRequestHandler):
    """Handler di prova: restituisce cio' che ha ricevuto."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silenzio durante i test
        pass

    def _reply(self, payload: bytes, status: int = 200, content_type: str = "text/plain"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Prova", "valore")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/errore":
            return self._reply(b"non trovato", 404)
        if self.path == "/binario":
            return self._reply(bytes(range(256)), 200, "application/octet-stream")
        self._reply(f"GET {self.path}".encode("utf-8"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else b""
        intestazione = self.headers.get("X-Cliente", "")
        self._reply(f"POST {self.path} | corpo={body.decode()} | header={intestazione}".encode("utf-8"))


def chiama(app, method="GET", path="/", query="", body=b"", headers=None):
    risposta = {}

    def start_response(status, response_headers):
        risposta["status"] = status
        risposta["headers"] = dict(response_headers)

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "REMOTE_ADDR": "91.80.10.5",
        "wsgi.input": io.BytesIO(body),
        "CONTENT_LENGTH": str(len(body)),
    }
    for nome, valore in (headers or {}).items():
        environ["HTTP_" + nome.upper().replace("-", "_")] = valore
    corpo = b"".join(app(environ, start_response))
    return risposta["status"], risposta["headers"], corpo


class WsgiBridgeTests(unittest.TestCase):
    def setUp(self):
        self.app = make_wsgi_app(EchoHandler)

    def test_get_semplice(self):
        status, headers, body = chiama(self.app, path="/pagina")
        self.assertTrue(status.startswith("200"))
        self.assertEqual(body, b"GET /pagina")
        self.assertEqual(headers["X-Prova"], "valore")

    def test_query_string_conservata(self):
        _, _, body = chiama(self.app, path="/api/dashboard", query="include_closed=true")
        self.assertEqual(body, b"GET /api/dashboard?include_closed=true")

    def test_corpo_e_header_della_richiesta(self):
        _, _, body = chiama(self.app, "POST", "/api/login", body=b'{"a":1}',
                            headers={"X-Cliente": "prova"})
        self.assertEqual(body, b'POST /api/login | corpo={"a":1} | header=prova')

    def test_codice_di_stato_non_200(self):
        status, _, body = chiama(self.app, path="/errore")
        self.assertTrue(status.startswith("404"))
        self.assertEqual(body, b"non trovato")

    def test_corpo_binario_intatto(self):
        _, headers, body = chiama(self.app, path="/binario")
        self.assertEqual(body, bytes(range(256)))
        self.assertEqual(headers["Content-Type"], "application/octet-stream")

    def test_header_hop_by_hop_rimossi(self):
        # Li gestisce la piattaforma: ripeterli romperebbe la risposta.
        _, headers, _ = chiama(self.app, path="/")
        for nome in headers:
            self.assertNotIn(nome.lower(), {"connection", "transfer-encoding", "keep-alive"})

    def test_percorso_con_caratteri_codificati(self):
        # L'URI grezzo, quando disponibile, ha la precedenza su PATH_INFO
        # che arriva gia' decodificato.
        risposta = {}

        def start_response(status, response_headers):
            risposta["status"] = status

        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/api/shipment/AB 12",
            "RAW_URI": "/api/shipment/AB%2012",
            "QUERY_STRING": "",
            "SERVER_NAME": "localhost", "SERVER_PORT": "80",
            "REMOTE_ADDR": "1.2.3.4",
            "wsgi.input": io.BytesIO(b""), "CONTENT_LENGTH": "0",
        }
        body = b"".join(self.app(environ, start_response))
        self.assertEqual(body, b"GET /api/shipment/AB%2012")


if __name__ == "__main__":
    unittest.main()
