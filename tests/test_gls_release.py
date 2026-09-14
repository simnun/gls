import unittest
from types import SimpleNamespace

from core.gls import GLSClient


class FakeGLS(GLSClient):
    def __init__(self):
        cfg = SimpleNamespace(
            gls_list_configured=True,
            gls_site="NI",
            gls_customer_code="123456",
            gls_contract_code="5038",
            gls_password="secret",
            gls_release_endpoint="https://example.invalid/release",
            request_timeout_seconds=5,
        )
        super().__init__(cfg)
        self.sent = None

    def _post_form_bytes(self, url, form):
        self.sent = (url, form)
        return b'<?xml version="1.0"?><xml><Risultati><Spedizione Numero="NI123"><Svincolo>Ok</Svincolo></Spedizione></Risultati></xml>'


class ReleaseTests(unittest.TestCase):
    def test_release_builds_request_and_parses_ok(self):
        gls = FakeGLS()
        result = gls.release_shipment_stock("NI123", {
            "release_type": "1",
            "delivery_date": "2026-09-15",
            "expense_payer": "sender",
            "recipient_phone": "3330000000",
            "phone_notice": True,
            "note": "Riconsegnare",
        })
        self.assertTrue(result["success"])
        self.assertEqual(result["result"], "Ok")
        xml = gls.sent[1]["XMLInfoReleaseShipmentStock"]
        self.assertIn("<TipoSvincolo>1</TipoSvincolo>", xml)
        self.assertIn("<DataRiconsegna>15/09/26</DataRiconsegna>", xml)
        self.assertIn("<ConPreavvisoTelefonico>S</ConPreavvisoTelefonico>", xml)
        self.assertIn("<SpeseAdMittente>S</SpeseAdMittente>", xml)
        self.assertIn("<SpeseAdDestinatario>N</SpeseAdDestinatario>", xml)
        self.assertIn("<PasswordClienteGls>secret</PasswordClienteGls>", xml)

    def test_type_two_requires_new_address(self):
        gls = FakeGLS()
        with self.assertRaises(ValueError):
            gls.release_shipment_stock("NI123", {"release_type": "2"})


if __name__ == "__main__":
    unittest.main()
