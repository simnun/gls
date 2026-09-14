"""Diagnostica dell'avvio: l'applicazione deve partire anche mal configurata.

Su un hosting serverless un errore durante l'import fa morire la funzione prima
che possa rispondere, e l'unico segnale e' un 500 senza spiegazione. Questi test
verificano che ogni configurazione sbagliata produca invece un messaggio utile.
"""
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RICHIESTA_STATUS = '''
import io, sys, os
sys.path.insert(0, %r); os.chdir(%r)
from app import app
risposta = {}
def start_response(status, headers):
    risposta["status"] = status
environ = {"REQUEST_METHOD":"GET","PATH_INFO":"/api/status","QUERY_STRING":"",
           "SERVER_NAME":"x","SERVER_PORT":"80","REMOTE_ADDR":"1.2.3.4",
           "wsgi.input": io.BytesIO(b""), "CONTENT_LENGTH":"0"}
corpo = b"".join(app(environ, start_response))
sys.stdout.write("#S#" + risposta["status"] + "#C#" + corpo.decode() + "#F#")
''' % (str(ROOT), str(ROOT))

UTENTE = json.dumps([{"username": "x@zuiki.it", "name": "X",
                      "password_hash": "scrypt$16384$8$1$AAAA$BBBB"}])


def chiedi_status(**variabili) -> tuple[str, dict]:
    """Avvia un processo separato: l'import va verificato da zero ogni volta."""
    ambiente = {k: v for k, v in os.environ.items()
                if k not in {"DATABASE_URL", "DASHBOARD_USERS", "SERVERLESS",
                             "PUBLIC_DEPLOYMENT", "MOCK_MODE", "DATABASE_SCHEMA"}}
    ambiente.update(variabili)
    esito = subprocess.run([sys.executable, "-c", RICHIESTA_STATUS],
                           capture_output=True, text=True, env=ambiente, timeout=90)
    if esito.returncode != 0:
        raise AssertionError(
            "L'import dell'applicazione e' fallito: online sarebbe un 500 senza "
            f"spiegazione.\n{esito.stderr[-600:]}"
        )
    grezzo = esito.stdout
    stato = grezzo.split("#S#")[1].split("#C#")[0]
    corpo = grezzo.split("#C#")[1].split("#F#")[0]
    return stato, json.loads(corpo)


class AvvioTests(unittest.TestCase):
    def test_database_irraggiungibile_non_impedisce_la_partenza(self):
        stato, dati = chiedi_status(
            SERVERLESS="true", PUBLIC_DEPLOYMENT="true", DASHBOARD_USERS=UTENTE,
            DATABASE_URL="postgresql://tizio:segreto@host.invalid:6543/postgres")
        self.assertTrue(stato.startswith("503"))
        self.assertFalse(dati["ok"])
        self.assertEqual(dati["database"], "errore")
        self.assertIn("host.invalid", dati["database_errore"])

    def test_il_messaggio_di_errore_non_contiene_la_password(self):
        _, dati = chiedi_status(
            SERVERLESS="true", PUBLIC_DEPLOYMENT="true", DASHBOARD_USERS=UTENTE,
            DATABASE_URL="postgresql://tizio:segretissimo@host.invalid:6543/postgres")
        testo = json.dumps(dati)
        self.assertNotIn("segretissimo", testo)

    def test_serverless_senza_database_esterno_e_un_errore(self):
        # Il disco non sopravvive alla richiesta: SQLite perderebbe tutto.
        stato, dati = chiedi_status(SERVERLESS="true", PUBLIC_DEPLOYMENT="true",
                                    DASHBOARD_USERS=UTENTE)
        self.assertTrue(stato.startswith("503"))
        self.assertIn("DATABASE_URL", dati["database_errore"])

    def test_senza_operatori_lo_segnala(self):
        stato, dati = chiedi_status(SERVERLESS="true", PUBLIC_DEPLOYMENT="true")
        self.assertFalse(dati["ok"])
        self.assertIn("DASHBOARD_USERS", dati.get("avviso", ""))

    def test_lo_stato_non_espone_segreti(self):
        _, dati = chiedi_status(
            SERVERLESS="true", PUBLIC_DEPLOYMENT="true", DASHBOARD_USERS=UTENTE,
            DATABASE_URL="postgresql://tizio:segretissimo@host.invalid:6543/postgres",
            CRON_SECRET="cron-segreto", SESSION_SECRET="sessione-segreta",
            GLS_PASSWORD="gls-segreta", SHOPIFY_CLIENT_SECRET="shopify-segreta")
        testo = json.dumps(dati)
        for segreto in ("segretissimo", "cron-segreto", "sessione-segreta",
                        "gls-segreta", "shopify-segreta", "scrypt$"):
            self.assertNotIn(segreto, testo)
        # I booleani di configurazione invece servono per capire cosa manca.
        self.assertTrue(dati["configurato"]["cron_secret"])
        self.assertEqual(dati["configurato"]["operatori"], 1)


class SanificazioneTests(unittest.TestCase):
    def test_credenziali_rimosse_dagli_url(self):
        from app import _senza_credenziali

        testo = _senza_credenziali(
            "connection to postgresql://utente:password123@db.host:5432/x failed")
        self.assertNotIn("password123", testo)
        self.assertNotIn("utente", testo)
        self.assertIn("db.host", testo)

    def test_testo_senza_url_resta_invariato(self):
        from app import _senza_credenziali

        self.assertEqual(_senza_credenziali("errore semplice"), "errore semplice")


class FusoOrarioTests(unittest.TestCase):
    def test_fuso_italiano_disponibile(self):
        # Se fallisce manca il pacchetto tzdata fra le dipendenze.
        from core.timezones import ROME, tzdata_available

        self.assertTrue(tzdata_available(), "tzdata non installato")
        self.assertEqual(str(ROME), "Europe/Rome")

    def test_moduli_allineati_sullo_stesso_fuso(self):
        from core.classifier import ROME as a
        from core.gls import ROME as b
        from core.sync import ROME as c
        from core.timezones import ROME as base

        self.assertIs(a, base)
        self.assertIs(b, base)
        self.assertIs(c, base)


if __name__ == "__main__":
    unittest.main()
