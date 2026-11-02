"""Quante interrogazioni costa una pagina.

Non e' una questione di eleganza: la dashboard si ricarica da sola ogni
trenta secondi, su un database remoto condiviso con la sincronizzazione.
Una query per riga, moltiplicata per le centinaia di giacenze in archivio,
diventa traffico che rallenta il lavoro vero.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.db import Database
from core.timezones import ROME


class ContaQuery:
    """Avvolge una connessione e tiene il conto delle istruzioni eseguite."""

    def __init__(self, conn):
        self._conn = conn
        self.quante = 0

    def execute(self, sql, params=None):
        self.quante += 1
        return self._conn.execute(sql, params) if params is not None else self._conn.execute(sql)

    def __getattr__(self, nome):
        return getattr(self._conn, nome)


class CaricoStoricoGiacenze(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "db.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def giacenza(self, numero, giorni_fa):
        quando = (datetime.now(ROME) - timedelta(days=giorni_fa)).replace(microsecond=0)
        self.db.upsert_shipment({
            "tracking_number": numero, "order_name": "#" + numero, "customer_name": "Cliente",
            "city": "Napoli", "province": "NA", "total_amount": 10.0, "currency": "EUR",
            "payment_gateways": [], "is_cod": False,
            "gls_status": "In giacenza", "gls_note": "", "severity": "CRITICAL",
            "category": "STORAGE", "closed": False, "workflow_status": "NEW",
        })
        self.db.insert_event({
            "tracking_number": numero, "event_hash": numero + "-g",
            "event_at": quando.isoformat(timespec="seconds"), "code": "",
            "state": "Spedizione in giacenza", "note": "In attesa di istruzioni dal mittente",
            "location": "Napoli", "severity": "CRITICAL", "category": "STORAGE",
            "reason": "", "raw": {},
        })
        self.db.reconcile_stock_cases(numero)

    def conta(self):
        originale = self.db.connect
        conteggi = []

        from contextlib import contextmanager

        @contextmanager
        def spia():
            with originale() as conn:
                finta = ContaQuery(conn)
                try:
                    yield finta
                finally:
                    conteggi.append(finta.quante)

        self.db.connect = spia
        try:
            self.db.stock_history()
        finally:
            self.db.connect = originale
        return sum(conteggi)

    def test_il_costo_non_cresce_con_il_numero_di_giacenze(self):
        for i in range(3):
            self.giacenza(f"NI{i:09d}", i + 1)
        poche = self.conta()
        for i in range(3, 30):
            self.giacenza(f"NI{i:09d}", i + 1)
        molte = self.conta()
        self.assertEqual(self.db.stock_history()["summary"]["total"], 30)
        # Dieci volte le giacenze, lo stesso numero di interrogazioni.
        self.assertEqual(poche, molte)
        self.assertLessEqual(molte, 5)

    def test_le_azioni_contano_solo_dentro_la_finestra_della_giacenza(self):
        self.giacenza("NI000000001", 5)
        vecchia = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat(timespec="milliseconds")
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO operator_actions (tracking_number, action_type, action_label,"
                " note, operator_name, created_at) VALUES (?,?,?,?,?,?)",
                ("NI000000001", "NOTE", "Prima della giacenza", "", "Daniela", vecchia),
            )
        caso = self.db.stock_history()["cases"][0]
        self.assertEqual(caso["operator_actions"], [])
        self.assertFalse(caso["handled"])

        self.db.add_operator_action("NI000000001", "NOTE", "Cliente chiamato", operator_name="Daniela")
        caso = self.db.stock_history()["cases"][0]
        self.assertEqual(len(caso["operator_actions"]), 1)
        self.assertTrue(caso["handled"])


class CaricoDashboard(unittest.TestCase):
    def test_la_dashboard_non_calcola_le_analisi(self):
        # Le analisi stanno dietro a un pulsante: non devono pesare su ogni
        # caricamento della pagina.
        with tempfile.TemporaryDirectory() as cartella:
            db = Database(Path(cartella) / "db.sqlite3")
            dati = db.dashboard()
            self.assertNotIn("mese", dati)
            self.assertNotIn("serie", dati)
            self.assertIn("voci", db.confronto_mensile())


if __name__ == "__main__":
    unittest.main()
