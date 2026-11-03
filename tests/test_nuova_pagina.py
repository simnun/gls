"""Dati per la nuova interfaccia: novita' da GLS e riepilogo della giornata.

La nuova pagina vive accanto a quella di sempre e legge gli stessi dati;
questi due riepiloghi servono solo a lei e non toccano nulla.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.db import Database
from core.timezones import ROME


class NuovaPaginaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "db.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def spedizione(self, numero, categoria="IN_TRANSIT", evento_at=None):
        self.db.upsert_shipment({
            "tracking_number": numero, "order_name": "#" + numero,
            "customer_name": "Cliente", "city": "Cannara", "province": "PG",
            "total_amount": 29.95, "currency": "EUR", "payment_gateways": [],
            "is_cod": False, "gls_status": "In transito", "gls_note": "", "severity": "NORMAL",
            "category": categoria, "closed": False, "workflow_status": "NEW",
            "gls_event_at": evento_at or datetime.now(ROME).isoformat(timespec="seconds"),
        })

    def evento(self, numero, stato, categoria, event_at, impronta):
        self.db.insert_event({
            "tracking_number": numero, "event_hash": impronta, "event_at": event_at,
            "state": stato, "category": categoria, "severity": "NORMAL",
        })

    def test_novita_mostra_lo_stato_di_prima_e_quello_nuovo(self):
        self.spedizione("NI1")
        self.evento("NI1", "In transito", "IN_TRANSIT", "2026-11-01T09:00:00", "a")
        soglia = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        # created_at e' scritto all'inserimento: il secondo evento arriva dopo la soglia.
        with self.db.connect() as conn:
            conn.execute("UPDATE events SET created_at=? WHERE event_hash='a'",
                         ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),))
        self.evento("NI1", "In giacenza", "STOCK", "2026-11-02T09:00:00", "b")
        esito = self.db.novita_dal((datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat())
        self.assertEqual(esito["totale"], 1)
        voce = esito["pratiche"][0]
        self.assertEqual(voce["tracking_number"], "NI1")
        self.assertEqual(voce["prima"]["state"], "In transito")
        self.assertEqual(voce["dopo"]["state"], "In giacenza")
        self.assertTrue(soglia)

    def test_senza_novita_non_restituisce_nulla(self):
        self.spedizione("NI2")
        self.evento("NI2", "In transito", "IN_TRANSIT", "2026-11-01T09:00:00", "c")
        futuro = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        self.assertEqual(self.db.novita_dal(futuro)["pratiche"], [])

    def test_data_non_valida_vale_ultime_24_ore(self):
        self.spedizione("NI3")
        self.evento("NI3", "In transito", "IN_TRANSIT", "2026-11-01T09:00:00", "d")
        self.assertEqual(self.db.novita_dal("non una data")["totale"], 1)

    def test_riepilogo_di_oggi(self):
        oggi = datetime.now(ROME)
        self.spedizione("NI4", "DELIVERED", oggi.isoformat(timespec="seconds"))
        self.spedizione("NI5", "DELIVERED", (oggi - timedelta(days=3)).isoformat(timespec="seconds"))
        self.spedizione("NI6")
        self.db.add_operator_action("NI4", "NOTE", "Nota", "ok", "Daniela")
        self.db.add_operator_action("NI6", "NOTE", "Nota", "ok", "Daniela")
        self.db.add_operator_action("NI6", "CUSTOMER_CALLED", "Chiamato", "", "Marco")
        r = self.db.riepilogo_oggi()
        self.assertEqual(r["gestite"], 2)
        self.assertEqual(r["consegnate"], 1)
        self.assertEqual(r["svincoli"], 0)
        self.assertEqual(r["per_operatore"][0], {"nome": "Daniela", "pratiche": 2})


if __name__ == "__main__":
    unittest.main()
