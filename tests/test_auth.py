"""Password, sessioni e vincolo di rete dell'accesso operatori."""
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import auth  # noqa: E402

PASSWORD = "PasswordDiProva2026!"
IP_UFFICIO = "91.80.10.5"
IP_ESTERNO = "203.0.113.9"


class PasswordTests(unittest.TestCase):
    def test_hash_diverso_a_ogni_generazione(self):
        # Il sale casuale evita che due operatori con la stessa password
        # abbiano lo stesso hash.
        self.assertNotEqual(auth.hash_password(PASSWORD), auth.hash_password(PASSWORD))

    def test_verifica_password(self):
        stored = auth.hash_password(PASSWORD)
        self.assertTrue(auth.verify_password(PASSWORD, stored))
        self.assertFalse(auth.verify_password("PasswordDiProva2026", stored))
        self.assertFalse(auth.verify_password("", stored))

    def test_hash_malformato_non_autentica(self):
        for stored in ("", "non-un-hash", "md5$1$2$3$4$5", "scrypt$a$b$c$d$e"):
            self.assertFalse(auth.verify_password(PASSWORD, stored))

    def test_la_password_in_chiaro_non_compare_nell_hash(self):
        self.assertNotIn(PASSWORD, auth.hash_password(PASSWORD))


class UserStoreTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.dumps([
            {"username": "Simone@Zuiki.it", "name": "Simone", "password_hash": auth.hash_password(PASSWORD)},
            {"username": "shoponline@zuiki.it", "name": "Daniela", "password_hash": auth.hash_password(PASSWORD)},
        ])
        self.users = auth.load_users(self.raw)

    def test_email_normalizzata_minuscola(self):
        self.assertIn("simone@zuiki.it", self.users)
        self.assertEqual(self.users["simone@zuiki.it"].display_name, "Simone")

    def test_accesso_riuscito_e_non_riuscito(self):
        self.assertIsNotNone(auth.authenticate(self.users, "SIMONE@zuiki.it", PASSWORD))
        self.assertIsNone(auth.authenticate(self.users, "simone@zuiki.it", "sbagliata"))
        self.assertIsNone(auth.authenticate(self.users, "sconosciuto@zuiki.it", PASSWORD))

    def test_configurazione_non_valida_non_crea_utenti(self):
        for raw in ("", "   ", "non json", "{}", "[]", '[{"username":"x"}]'):
            self.assertEqual(auth.load_users(raw), {})


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.secret = auth.derive_secret("[]", "segreto-di-prova")

    def token(self, ip=IP_UFFICIO, username="simone@zuiki.it"):
        return auth.create_session(username, self.secret, ip)

    def read(self, token, ip=IP_UFFICIO, max_age=30 * 86400, bind_ip=True):
        return auth.read_session(token, self.secret, max_age_seconds=max_age,
                                 client_ip=ip, bind_ip=bind_ip)

    def test_sessione_valida_dalla_stessa_rete(self):
        self.assertEqual(self.read(self.token()), "simone@zuiki.it")

    def test_sessione_rifiutata_da_un_altra_rete(self):
        # E' la richiesta esplicita: l'accesso resta valido solo dall'IP di origine.
        self.assertIsNone(self.read(self.token(), ip=IP_ESTERNO))

    def test_vincolo_di_rete_disattivabile(self):
        self.assertEqual(self.read(self.token(), ip=IP_ESTERNO, bind_ip=False), "simone@zuiki.it")

    def test_firma_manomessa_rifiutata(self):
        token = self.token()
        corpo, _, firma = token.partition(".")
        self.assertIsNone(self.read(f"{corpo}x.{firma}"))
        self.assertIsNone(self.read(f"{corpo}.{firma[:-2]}aa"))
        self.assertIsNone(self.read(corpo))

    def test_segreto_diverso_rifiutato(self):
        altro = auth.derive_secret("[]", "un-altro-segreto")
        self.assertIsNone(auth.read_session(self.token(), altro, max_age_seconds=86400,
                                            client_ip=IP_UFFICIO))

    def test_sessione_scaduta(self):
        with patch("core.auth.time.time", return_value=time.time() - 40 * 86400):
            vecchio = self.token()
        self.assertIsNone(self.read(vecchio, max_age=30 * 86400))
        self.assertEqual(self.read(vecchio, max_age=0), "simone@zuiki.it")

    def test_il_token_non_contiene_l_ip_in_chiaro(self):
        self.assertNotIn(IP_UFFICIO, self.token())

    def test_segreto_derivato_stabile_tra_istanze(self):
        # Se il segreto cambiasse per processo, ogni istanza serverless
        # disconnetterebbe gli utenti delle altre.
        self.assertEqual(auth.derive_secret("config-utenti"), auth.derive_secret("config-utenti"))
        self.assertNotEqual(auth.derive_secret("config-a"), auth.derive_secret("config-b"))


class HeaderTests(unittest.TestCase):
    def test_ip_dalla_catena_di_proxy(self):
        headers = {"x-forwarded-for": "91.80.10.5, 10.0.0.1, 172.16.0.2"}
        self.assertEqual(auth.client_ip_from_headers(headers), "91.80.10.5")

    def test_ip_di_riserva_senza_header(self):
        self.assertEqual(auth.client_ip_from_headers({}, "127.0.0.1"), "127.0.0.1")

    def test_lettura_cookie(self):
        cookies = auth.parse_cookies("altro=1; gls_session=abc.def; vuoto")
        self.assertEqual(cookies["gls_session"], "abc.def")
        self.assertEqual(cookies["altro"], "1")


class PrefissoDiReteTests(unittest.TestCase):
    """La sessione e' legata alla rete, non al singolo indirizzo.

    Reti aziendali con piu' uscite, reti mobili e CGNAT mostrano indirizzi
    diversi a ogni richiesta: con il vincolo sull'indirizzo esatto l'operatore
    verrebbe disconnesso di continuo.
    """

    def test_prefisso_ipv4_e_la_rete_24(self):
        self.assertEqual(auth.network_prefix("160.79.106.130"), "160.79.106.0/24")
        self.assertEqual(auth.network_prefix("160.79.106.136"), "160.79.106.0/24")

    def test_prefisso_ipv6_e_la_rete_64(self):
        self.assertEqual(auth.network_prefix("2a03:b0c0:1:e0::ff"), "2a03:b0c0:1:e0::/64")

    def test_valore_non_valido_resta_invariato(self):
        self.assertEqual(auth.network_prefix("non-un-ip"), "non-un-ip")
        self.assertEqual(auth.network_prefix(""), "")

    def test_sessione_valida_da_indirizzi_diversi_della_stessa_rete(self):
        secret = auth.derive_secret("[]", "prova-rete")
        token = auth.create_session("simone@zuiki.it", secret, "160.79.106.130")
        for ip in ("160.79.106.130", "160.79.106.136", "160.79.106.254"):
            self.assertEqual(
                auth.read_session(token, secret, max_age_seconds=86400, client_ip=ip),
                "simone@zuiki.it",
                f"respinta da {ip}, stessa rete",
            )

    def test_sessione_respinta_da_una_rete_diversa(self):
        secret = auth.derive_secret("[]", "prova-rete")
        token = auth.create_session("simone@zuiki.it", secret, "160.79.106.130")
        for ip in ("160.79.107.1", "203.0.113.9", "10.0.0.1"):
            self.assertIsNone(
                auth.read_session(token, secret, max_age_seconds=86400, client_ip=ip),
                f"accettata da {ip}, rete diversa",
            )

    def test_il_token_non_contiene_la_rete_in_chiaro(self):
        secret = auth.derive_secret("[]", "prova-rete")
        token = auth.create_session("simone@zuiki.it", secret, "160.79.106.130")
        self.assertNotIn("160.79.106", token)

if __name__ == "__main__":
    unittest.main()
