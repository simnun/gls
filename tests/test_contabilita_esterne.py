"""Le spedizioni dell'altro negozio: il monitor non le segue, la contabilita' le traccia su GLS."""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from core import contabilita
from core.db import Database
from core.gls import GLSError
from core.sync import SyncEngine

RADICE = Path(__file__).resolve().parents[1]


def evento(giorno, stato, nota=""):
    return {"event_at": f"2026-10-{giorno:02d}T10:00:00+02:00", "state": stato, "note": nota, "code": "",
            "location": "NAPOLI"}


class FakeGLS:
    def __init__(self, risposte):
        self.risposte = risposte
        self.chiamate = []

    def track(self, numero):
        self.chiamate.append(numero)
        risposta = self.risposte.get(numero)
        if risposta is None:
            raise GLSError("Spedizione non trovata")
        eventi = risposta["eventi"]
        return {"events": eventi, "current_event": eventi[-1], "status": eventi[-1]["state"],
                "tracking_source": "GLS XML", "cod_amount": risposta.get("cod", "")}


class SpedizioniEsterneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "db.sqlite3")
        cfg = SimpleNamespace(rules_path=RADICE / "config" / "rules.json", gls_public_workers=2,
                              gls_retry_attempts=3, mock_mode=False, shopify_configured=True,
                              gls_tracking_configured=True, sync_workers=2)
        self.motore = SyncEngine(cfg, self.db)
        self.gls = FakeGLS({
            "665000101": {"eventi": [evento(1, "IN TRANSITO"), evento(2, "CONSEGNATA")]},
            "665000102": {"eventi": [evento(1, "IN TRANSITO"), evento(2, "GIACENZA", "in attesa di istruzioni"),
                                     evento(5, "CONSEGNATA")], "cod": "35,90"},
            "665000103": {"eventi": [evento(1, "IN TRANSITO"), evento(2, "GIACENZA", "in attesa di istruzioni")]},
        })
        self.motore.gls = self.gls

    def tearDown(self):
        self.tmp.cleanup()

    def test_traccia_e_ricorda(self):
        info = self.motore.traccia_esterne(["665000101", "665000102", "665000103", "665000999"])
        self.assertEqual(info["665000101"]["category"], "DELIVERED")
        self.assertEqual(info["665000101"]["negozio"], contabilita.ALTRO_NEGOZIO)
        self.assertFalse(info["665000101"]["is_cod"])
        self.assertEqual((info["665000102"]["category"], info["665000102"]["giacenze"]), ("DELIVERED", 1))
        self.assertTrue(info["665000102"]["is_cod"])
        self.assertEqual(info["665000103"]["giacenza_aperta"], 1)
        self.assertNotIn("665000999", info)          # GLS non la conosce

        # Al giro dopo: le consegnate non si richiedono, le altre appena
        # tracciate nemmeno; scadute le due ore si richiedono quelle aperte.
        self.gls.chiamate.clear()
        self.motore.traccia_esterne(["665000101", "665000102", "665000103", "665000999"])
        self.assertEqual(self.gls.chiamate, [])
        vecchio = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(timespec="seconds")
        with self.db.connect() as conn:
            conn.execute("UPDATE spedizioni_esterne SET aggiornato_il=?", (vecchio,))
        self.motore.traccia_esterne(["665000101", "665000102", "665000103", "665000999"])
        self.assertEqual(sorted(self.gls.chiamate), ["665000103", "665000999"])

    def test_nell_analisi_contano_come_altro_negozio(self):
        intestazioni = ["Data", "Servizio/tipologia", "Nr. Spedizione", "Destinatario", "Nolo", "Diritto Contrassegno"]
        righe = [["01/10/2026", "Partenze", "665000101", "A", "3,90", "0"],
                 ["01/10/2026", "Partenze", "665000102", "B", "3,90", "0"],
                 ["01/10/2026", "Partenze", "665000999", "C", "3,90", "0"]]
        monitor = self.db.spedizioni_per_contabilita()
        mancanti = contabilita.non_trovate(intestazioni, righe, monitor)
        self.assertEqual(mancanti, ["665000101", "665000102", "665000999"])
        monitor.update(self.motore.traccia_esterne(mancanti))
        analisi = contabilita.analizza(intestazioni, righe, monitor)
        esiti = [(r["esito"], r["controlli"]) for r in analisi["righe"]]
        self.assertEqual(esiti[0], ("consegnata", []))
        # In contrassegno secondo GLS, ma il diritto non c'e' in fattura.
        self.assertEqual(esiti[1], ("consegnata", ["Ordine in contrassegno, ma il contrassegno non è in fattura"]))
        self.assertEqual(esiti[2][0], "sconosciuta")
        self.assertEqual(analisi["riepilogo"]["negozi"],
                         [{"nome": contabilita.ALTRO_NEGOZIO, "spedizioni": 2, "costo": 7.8}])
        schermo = contabilita.per_lo_schermo(analisi, intestazioni)
        self.assertEqual(schermo["righe"][0]["negozio"], contabilita.ALTRO_NEGOZIO)
        self.assertEqual(schermo["righe"][0]["tracking"], "")   # nessun dettaglio nel monitor


if __name__ == "__main__":
    unittest.main()
