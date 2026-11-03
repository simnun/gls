"""La nuova interfaccia e' la pagina principale; la classica resta raggiungibile."""

import io
import unittest

from core.wsgi import make_wsgi_app

import app as applicazione


def apri(percorso):
    wsgi = make_wsgi_app(applicazione.AppHandler)
    ambiente = {
        "REQUEST_METHOD": "GET", "PATH_INFO": percorso, "QUERY_STRING": "",
        "SERVER_PROTOCOL": "HTTP/1.1", "wsgi.input": io.BytesIO(b""),
        "SERVER_NAME": "test", "SERVER_PORT": "80",
    }
    stato = {}
    corpo = b"".join(wsgi(ambiente, lambda s, h, e=None: stato.update(s=s)))
    return stato["s"], corpo


class PaginaPrincipaleTests(unittest.TestCase):
    def test_la_radice_apre_la_nuova_interfaccia(self):
        stato, corpo = apri("/")
        self.assertTrue(stato.startswith("200"))
        self.assertIn(b"/nuova.js", corpo)

    def test_il_vecchio_indirizzo_nuova_funziona_ancora(self):
        stato, corpo = apri("/nuova")
        self.assertTrue(stato.startswith("200"))
        self.assertIn(b"/nuova.js", corpo)

    def test_la_pagina_classica_resta_su_classica(self):
        stato, corpo = apri("/classica")
        self.assertTrue(stato.startswith("200"))
        self.assertIn(b"/app.js", corpo)
        self.assertNotIn(b"/nuova.js", corpo)


if __name__ == "__main__":
    unittest.main()
