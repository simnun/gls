"""Esporta: le spedizioni filtrate a schermo diventano un foglio Excel."""

import io
import json
import re
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

from core.db import Database
from core.esporta import righe_spedizioni
from core.timezones import ROME
from core.xlsx_export import build_xlsx


def foglio(raw: bytes) -> str:
    with ZipFile(io.BytesIO(raw)) as z:
        return z.read("xl/worksheets/sheet1.xml").decode("utf-8")


class EsportaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "db.sqlite3")
        for numero, categoria, cod in (("NI1", "STORAGE", True), ("NI2", "DELIVERED", False), ("NI3", "RETURN", False)):
            self.db.upsert_shipment({
                "tracking_number": numero, "order_name": "#" + numero, "order_created_at": "2026-10-20T08:15:00Z",
                "customer_name": "Cliente " + numero, "customer_email": "c@example.com", "customer_phone": "3330000000",
                "city": "Napoli", "province": "NA", "total_amount": 69.72, "currency": "EUR",
                "payment_gateways": ["Contrassegno"] if cod else ["shopify_payments"], "is_cod": cod,
                "gls_status": "Stato " + numero, "gls_note": "", "severity": "CRITICAL" if categoria == "STORAGE" else "NORMAL",
                "category": categoria, "closed": categoria != "STORAGE",
                "workflow_status": "NEW" if categoria == "STORAGE" else "RESOLVED",
                "gls_event_at": datetime(2026, 11, 3, 9, 30, tzinfo=ROME).isoformat(timespec="seconds"),
            })

    def tearDown(self):
        self.tmp.cleanup()

    def test_ordine_dello_schermo_e_tracking_sconosciuti_ignorati(self):
        spedizioni = self.db.spedizioni_per_export(["NI3", "NI1", "NON_ESISTE", "NI3"])
        self.assertEqual([s["tracking_number"] for s in spedizioni], ["NI3", "NI1"])

    def test_colonne_leggibili_e_valori_tradotti(self):
        intestazioni, righe = righe_spedizioni(self.db.spedizioni_per_export(["NI1"]),
                                               lambda s: {"gls": "https://gls/x", "shopify": "https://shop/y"})
        riga = dict(zip(intestazioni, righe[0]))
        self.assertEqual(riga["Ordine"], "#NI1")
        self.assertEqual(riga["Situazione"], "In giacenza in sede GLS")
        self.assertEqual(riga["Priorita"], "Massima")
        self.assertEqual(riga["Pratica"], "Nuova")
        self.assertEqual(riga["Pagamento"], "Contrassegno")
        self.assertIs(riga["Contrassegno"], True)
        self.assertEqual(riga["Importo"], 69.72)
        # Ora italiana, senza fuso: 08:15 UTC del 20 ottobre sono le 10:15 a Roma.
        self.assertEqual(riga["Data ordine"], datetime(2026, 10, 20, 10, 15))
        self.assertEqual(riga["Link ordine Shopify"], "https://shop/y")
        chiusa = dict(zip(intestazioni, righe_spedizioni(self.db.spedizioni_per_export(["NI2"]))[1][0]))
        self.assertEqual(chiusa["Pratica"], "Chiusa")
        self.assertEqual(chiusa["Situazione"], "Consegnata")

    def test_numeri_e_date_restano_numeri_e_date_in_excel(self):
        xml = foglio(build_xlsx(["Importo", "Quando", "Testo", "Cod"],
                                [[69.72, datetime(2026, 11, 3, 9, 30), "ciao", True]]))
        self.assertIn('<c r="A2"><v>69.72</v></c>', xml)
        self.assertRegex(xml, r'<c r="B2" s="2"><v>46329\.39\d+</v></c>')
        self.assertIn("ciao", xml)
        self.assertIn(">Si<", xml)


class EsportaEndpointTests(unittest.TestCase):
    def test_l_endpoint_restituisce_un_file_excel(self):
        from core.wsgi import make_wsgi_app
        import app as applicazione

        corpo = json.dumps({"tracking": ["NI_INESISTENTE"], "nome": "Da verificare"}).encode()
        ambiente = {
            "REQUEST_METHOD": "POST", "PATH_INFO": "/api/shipments/export.xlsx", "QUERY_STRING": "",
            "SERVER_PROTOCOL": "HTTP/1.1", "wsgi.input": io.BytesIO(corpo), "CONTENT_LENGTH": str(len(corpo)),
            "CONTENT_TYPE": "application/json", "SERVER_NAME": "test", "SERVER_PORT": "80",
        }
        stato = {}
        uscita = b"".join(make_wsgi_app(applicazione.AppHandler)(
            ambiente, lambda s, h, e=None: stato.update(s=s, h={k.lower(): v for k, v in h})))
        if stato["s"].startswith("401"):
            self.skipTest("accesso protetto in questo ambiente")
        self.assertTrue(stato["s"].startswith("200"), stato["s"])
        self.assertIn("spreadsheetml", stato["h"]["content-type"])
        self.assertTrue(re.search(r'filename="GLS_Daverificare_\d{4}-\d{2}-\d{2}\.xlsx"', stato["h"]["content-disposition"]))
        with ZipFile(io.BytesIO(uscita)) as z:
            self.assertIn("xl/worksheets/sheet1.xml", z.namelist())


if __name__ == "__main__":
    unittest.main()
