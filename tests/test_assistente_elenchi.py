"""Quando l'operatore chiede un elenco, l'elenco sotto la risposta e' quello giusto.

Il caso vero: "dammi tutti i tracking delle giacenze rientrate a ottobre".
L'assistente rispondeva che non poteva, e sotto mostrava le prime 200 di tutte
le giacenze, consegnate comprese.
"""

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from core.assistente import LIMITE_RIGHE, Assistente
from core.assistente_ai import ISTRUZIONI, _risultato, costruisci_lettura
from core.db import Database
from core.timezones import ROME

from tests.test_assistente_ai import VOCABOLARIO, filtri

OGGI = date(2026, 11, 13)
RIENTRATE = 230
CONSEGNATE = 45


class ElenchiGiacenzeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "db.sqlite3")
        esiti = ["RETURN"] * RIENTRATE + ["DELIVERED"] * CONSEGNATE
        uscita = datetime(2026, 11, 2, 9, tzinfo=ROME).isoformat(timespec="seconds")
        giacenze = []
        # Prima le spedizioni, poi le giacenze: due connessioni aperte insieme
        # bloccherebbero il database.
        for i, esito in enumerate(esiti):
            numero = f"NI{665000000 + i}"
            self.db.upsert_shipment({
                "tracking_number": numero, "order_name": f"#28{i:04d}",
                "customer_name": "Cliente", "city": "Napoli", "province": "NA",
                "total_amount": 10.0, "currency": "EUR", "payment_gateways": [],
                "is_cod": False, "gls_status": "Esito", "gls_note": "", "severity": "NORMAL",
                "category": esito, "closed": True, "workflow_status": "RESOLVED",
                "gls_event_at": uscita,
            })
            entrata = datetime(2026, 10, 1 + i % 28, 9, tzinfo=ROME).isoformat(timespec="seconds")
            giacenze.append((numero, f"h{i}", entrata, uscita, esito, entrata, uscita))
        with self.db.connect() as conn:
            for valori in giacenze:
                conn.execute(
                    """INSERT INTO stock_cases(tracking_number, entry_event_hash, entered_at,
                       exited_at, outcome_category, status, created_at, updated_at)
                       VALUES(?,?,?,?,?,'CLOSED',?,?)""",
                    valori,
                )
        self.assistente = Assistente(self.db, durata_cache=0)

    def tearDown(self):
        self.tmp.cleanup()

    def leggi(self, **campi):
        return costruisci_lettura(filtri(**campi), VOCABOLARIO, OGGI)

    def test_le_rientrate_si_elencano_tutte_e_sole(self):
        lettura = self.leggi(soggetto="GIACENZE", intento="elenca", categorie=["RETURN"],
                             periodo_da="2026-10-01", periodo_a="2026-10-31",
                             periodo_etichetta="a ottobre")
        calcolo = self.assistente.calcola(lettura)
        self.assertEqual(calcolo["numero"], RIENTRATE)
        # Piu' di 200: l'elenco non si ferma piu' a meta'.
        self.assertEqual(len(calcolo["righe"]), RIENTRATE)
        self.assertEqual({r["outcome_category"] for r in calcolo["righe"]}, {"RETURN"})

    def test_il_modello_sa_che_l_elenco_completo_e_sotto_la_risposta(self):
        lettura = self.leggi(soggetto="GIACENZE", intento="elenca", categorie=["RETURN"],
                             periodo_da="2026-10-01", periodo_a="2026-10-31",
                             periodo_etichetta="a ottobre")
        fuori = _risultato(self.assistente.calcola(lettura), lettura)
        self.assertEqual(fuori["elenco_sotto_la_risposta"], RIENTRATE)
        self.assertLessEqual(len(fuori["esempi"]), 6)   # al modello solo esempi
        self.assertIn("Copia i tracking", ISTRUZIONI)
        self.assertIn("RETURN le rientrate", ISTRUZIONI)

    def test_il_limite_basta_per_un_mese_di_giacenze(self):
        self.assertGreaterEqual(LIMITE_RIGHE, 1000)


if __name__ == "__main__":
    unittest.main()
