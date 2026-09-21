import gzip
import io
import json
import unittest

from core.wsgi import make_wsgi_app

import app as applicazione


def chiama(percorso, accetta_gzip=True):
    """Fa una richiesta vera all'app e restituisce stato, intestazioni e corpo."""
    wsgi = make_wsgi_app(applicazione.AppHandler)
    ambiente = {
        "REQUEST_METHOD": "GET", "PATH_INFO": percorso, "QUERY_STRING": "",
        "SERVER_PROTOCOL": "HTTP/1.1", "wsgi.input": io.BytesIO(b""),
        "SERVER_NAME": "test", "SERVER_PORT": "80",
    }
    if accetta_gzip:
        ambiente["HTTP_ACCEPT_ENCODING"] = "gzip, deflate"
    catturato = {}

    def inizio(stato, intestazioni, exc_info=None):
        catturato["stato"] = stato
        catturato["intestazioni"] = {k.lower(): v for k, v in intestazioni}

    corpo = b"".join(wsgi(ambiente, inizio))
    return catturato["stato"], catturato["intestazioni"], corpo


class CompressioneTests(unittest.TestCase):
    """L'elenco spedizioni viaggia molte volte al giorno: senza compressione
    la banda del piano si esaurisce in pochi giorni."""

    def test_una_risposta_grande_viaggia_compressa(self):
        stato, intestazioni, corpo = chiama("/app.js")
        self.assertTrue(stato.startswith("200"))
        self.assertEqual(intestazioni.get("content-encoding"), "gzip")
        self.assertEqual(intestazioni.get("vary"), "Accept-Encoding")
        # Il contenuto deve restare intatto una volta scompattato.
        self.assertIn(b"function", gzip.decompress(corpo))

    def test_chi_non_la_accetta_riceve_il_testo_in_chiaro(self):
        stato, intestazioni, corpo = chiama("/app.js", accetta_gzip=False)
        self.assertTrue(stato.startswith("200"))
        self.assertIsNone(intestazioni.get("content-encoding"))
        self.assertIn(b"function", corpo)

    def test_la_lunghezza_dichiarata_e_quella_inviata(self):
        _, intestazioni, corpo = chiama("/app.js")
        self.assertEqual(int(intestazioni["content-length"]), len(corpo))

    def test_una_risposta_piccola_resta_in_chiaro(self):
        # Comprimere poche centinaia di byte costa piu' di quanto rende.
        _, intestazioni, corpo = chiama("/api/health")
        self.assertIsNone(intestazioni.get("content-encoding"))
        self.assertLess(len(corpo), applicazione.AppHandler.SOGLIA_COMPRESSIONE)
