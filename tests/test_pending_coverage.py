"""Le spedizioni rinviate non devono andare perse.

Le spedizioni entrano nel monitor solo quando vengono interrogate a GLS. Con un
tempo massimo per richiesta, quelle rinviate venivano scartate: non essendo nel
database non venivano riprese, e la ricerca incrementale su Shopify non le
ritrovava piu'. Sparivano proprio le piu' vecchie, cioe' quelle dove stanno
giacenze e mancate consegne.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.db import Database  # noqa: E402


def spedizione_shopify(numero: str, ordine: str = "#1000") -> dict:
    return {
        "tracking_number": numero,
        "order_name": ordine,
        "order_legacy_id": "555",
        "customer_name": "Mario Rossi",
        "customer_phone": "+393331234567",
        "city": "Napoli",
        "total_amount": 49.9,
        "currency": "EUR",
        "is_cod": 1,
    }


class SpedizioniInAttesaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "monitor.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_una_rinviata_viene_salvata_e_resta_monitorata(self):
        self.db.register_pending_shipments([spedizione_shopify("NI001")])
        aperte = self.db.list_open_shipments_for_sync()
        self.assertEqual([x["tracking_number"] for x in aperte], ["NI001"])
        self.assertIsNone(aperte[0].get("gls_checked_at"))

    def test_i_dati_shopify_vengono_conservati(self):
        self.db.register_pending_shipments([spedizione_shopify("NI002", "#277649")])
        riga = self.db.get_shipment("NI002")
        self.assertEqual(riga["order_name"], "#277649")
        self.assertEqual(riga["customer_name"], "Mario Rossi")
        self.assertEqual(riga["category"], "PENDING_CHECK")

    def test_non_viene_presentata_come_problema(self):
        # Finche' non e' stata interrogata non si puo' dire che abbia un'anomalia.
        self.db.register_pending_shipments([spedizione_shopify("NI003")])
        riga = self.db.get_shipment("NI003")
        self.assertEqual(riga["severity"], "INFO")
        self.assertNotIn(riga["severity"], ("CRITICAL", "WARNING"))

    def test_chi_non_e_mai_stato_interrogato_viene_per_primo(self):
        self.db.upsert_shipment({
            **spedizione_shopify("NI-VISTA"),
            "gls_status": "In transito", "category": "IN_TRANSIT", "severity": "NORMAL",
        })
        self.db.register_pending_shipments([spedizione_shopify("NI-NUOVA")])
        coda = [x["tracking_number"] for x in self.db.list_open_shipments_for_sync()]
        self.assertEqual(coda[0], "NI-NUOVA", f"coda: {coda}")

    def test_registrare_di_nuovo_non_cancella_lo_stato_gls(self):
        self.db.upsert_shipment({
            **spedizione_shopify("NI004"),
            "gls_status": "In giacenza", "gls_code": "M0",
            "category": "STOCK", "severity": "CRITICAL",
        })
        self.db.register_pending_shipments([spedizione_shopify("NI004")])
        riga = self.db.get_shipment("NI004")
        self.assertEqual(riga["gls_status"], "In giacenza")
        self.assertEqual(riga["severity"], "CRITICAL")
        self.assertEqual(riga["category"], "STOCK")

    def test_registrare_di_nuovo_non_cancella_il_lavoro_dell_operatore(self):
        self.db.register_pending_shipments([spedizione_shopify("NI005")])
        self.db.update_workflow("NI005", "IN_PROGRESS", "cliente avvisato", "Simone")
        self.db.register_pending_shipments([spedizione_shopify("NI005")])
        riga = self.db.get_shipment("NI005")
        self.assertEqual(riga["workflow_status"], "IN_PROGRESS")
        self.assertEqual(riga["operator_note"], "cliente avvisato")

    def test_i_dati_shopify_si_aggiornano(self):
        self.db.register_pending_shipments([spedizione_shopify("NI006", "#1")])
        self.db.register_pending_shipments([spedizione_shopify("NI006", "#2")])
        self.assertEqual(self.db.get_shipment("NI006")["order_name"], "#2")

    def test_registrazione_in_blocco(self):
        lotto = [spedizione_shopify(f"NI{i:03d}") for i in range(20)]
        self.assertEqual(self.db.register_pending_shipments(lotto), 20)
        self.assertEqual(len(self.db.list_open_shipments_for_sync()), 20)

    def test_elenco_vuoto_e_tracking_mancante(self):
        self.assertEqual(self.db.register_pending_shipments([]), 0)
        self.db.register_pending_shipments([{"order_name": "#1"}])
        self.assertEqual(len(self.db.list_open_shipments_for_sync()), 0)

    def test_dopo_l_interrogazione_la_spedizione_scende_in_coda(self):
        self.db.register_pending_shipments([
            spedizione_shopify("NI-A"), spedizione_shopify("NI-B")])
        primo = self.db.list_open_shipments_for_sync()[0]["tracking_number"]
        self.db.upsert_shipment({
            **spedizione_shopify(primo),
            "gls_status": "In transito", "category": "IN_TRANSIT", "severity": "NORMAL",
        })
        coda = [x["tracking_number"] for x in self.db.list_open_shipments_for_sync()]
        self.assertNotEqual(coda[0], primo, "una spedizione appena interrogata non deve restare in testa")


if __name__ == "__main__":
    unittest.main()
