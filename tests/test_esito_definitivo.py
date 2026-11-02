"""Una pratica conclusa non si riapre con le scansioni del reso.

GLS continua a tracciare il collo anche dopo averlo reso al mittente: il
pacco torna indietro e ogni passaggio viene registrato sullo stesso numero.
Prendere l'ultima scansione come stato della spedizione faceva ricomparire in
coda, giorni dopo, pratiche gia' finite.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.classifier import Classifier
from core.db import Database
from core.gls import GLSClient
from core.sync import SyncEngine


RESA = "Abbiamo restituito la spedizione al mittente, ti invitiamo a contattare il mittente."
RITIRO = "La tua spedizione e' disponibile per il ritiro presso la sede destinataria."
CONSEGNATA = "Consegnata"


class EsitoDefinitivoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "db.sqlite3")
        self.engine = object.__new__(SyncEngine)
        self.engine.db = self.db
        self.engine.classifier = Classifier(Path("config/rules.json"), self.db)
        self.engine.gls = SimpleNamespace(event_hash=GLSClient.event_hash)
        self.db.upsert_shipment({
            "tracking_number": "NI665290321", "order_name": "#280237",
            "customer_name": "Cliente", "city": "Cannara", "province": "PG",
            "total_amount": 29.95, "currency": "EUR", "payment_gateways": [],
            "is_cod": True, "gls_status": "", "gls_note": "", "severity": "NORMAL",
            "category": "IN_TRANSIT", "closed": False, "workflow_status": "NEW",
        })

    def tearDown(self):
        self.tmp.cleanup()

    def spedizione(self):
        return self.db.get_shipment("NI665290321")

    @staticmethod
    def evento(quando, stato, nota=""):
        return {"event_at": quando, "state": stato, "note": nota, "code": "", "location": "Foligno"}

    def salva(self, eventi):
        self.engine._save_tracking(
            self.spedizione(),
            {"events": eventi, "current_event": eventi[-1], "status": eventi[-1]["state"],
             "note": eventi[-1].get("note", ""), "code": ""},
        )

    def test_le_scansioni_del_reso_non_riaprono_la_pratica(self):
        # Il caso reale: resa il 9, poi il collo torna indietro e il 19 risulta
        # disponibile al ritiro. La pratica resta chiusa come rientro.
        self.salva([
            self.evento("2026-10-07T15:38:00+02:00", "Non siamo riusciti a raggiungerti al primo tentativo di consegna"),
            self.evento("2026-10-09T17:02:00+02:00", RESA),
            self.evento("2026-10-19T19:10:00+02:00", RITIRO),
        ])
        riga = self.spedizione()
        self.assertEqual(riga["category"], "RETURN")
        self.assertTrue(riga["closed"])
        self.assertIn("restituito", riga["gls_status"])
        self.assertTrue(riga["gls_event_at"].startswith("2026-10-09"))

    def test_una_consegna_successiva_al_rientro_vince(self):
        # Se dopo la resa GLS consegna davvero, l'esito piu' recente e' quello.
        self.salva([
            self.evento("2026-10-09T17:02:00+02:00", RESA),
            self.evento("2026-10-20T10:00:00+02:00", CONSEGNATA),
        ])
        riga = self.spedizione()
        self.assertEqual(riga["category"], "DELIVERED")
        self.assertTrue(riga["closed"])

    def test_senza_esito_vale_sempre_l_ultimo_evento(self):
        # Fuori dai casi conclusi non cambia niente: una giacenza che riparte
        # deve risultare ripartita.
        self.salva([
            self.evento("2026-10-05T09:00:00+02:00", "Spedizione in giacenza",
                        "In attesa di istruzioni dal mittente"),
            self.evento("2026-10-06T08:00:00+02:00", "In transito"),
        ])
        riga = self.spedizione()
        self.assertEqual(riga["category"], "IN_TRANSIT")
        self.assertFalse(riga["closed"])

    def test_la_riclassificazione_dello_storico_segue_la_stessa_regola(self):
        for chiave, (quando, stato) in enumerate((
            ("2026-10-09T17:02:00+02:00", RESA),
            ("2026-10-19T19:10:00+02:00", RITIRO),
        )):
            self.db.insert_event({
                "tracking_number": "NI665290321", "event_hash": f"h{chiave}",
                "event_at": quando, "code": "", "state": stato, "note": "",
                "location": "Foligno", "severity": "INFO", "category": "IN_TRANSIT",
                "reason": "", "raw": {},
            })
        self.engine.reclassify_history()
        riga = self.spedizione()
        self.assertEqual(riga["category"], "RETURN")
        self.assertTrue(riga["closed"])
        # A schermo deve comparire l'evento che descrive la pratica, non la
        # scansione del reso che gli e' arrivata dopo.
        self.assertIn("restituito", riga["gls_status"])
        self.assertTrue(riga["gls_event_at"].startswith("2026-10-09"))


if __name__ == "__main__":
    unittest.main()
