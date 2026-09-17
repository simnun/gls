import tempfile
import unittest
from pathlib import Path

from core.db import Database


class FiltroStatiGLSTests(unittest.TestCase):
    """La dashboard porta gli stati attraversati da ogni spedizione, cosi'
    l'elenco si puo' filtrare sia sull'ultimo stato sia su tutto il percorso."""

    GIACENZA = "Spedizione in giacenza presso la sede GLS"
    IN_CONSEGNA = "Consegna prevista nel corso della giornata odierna."

    def setUp(self):
        self.db = Database(path=Path(tempfile.mkdtemp()) / "t.db")
        self.db.upsert_shipment({"tracking_number": "NI1", "gls_status": self.IN_CONSEGNA})
        self.db.upsert_shipment({"tracking_number": "NI2", "gls_status": self.IN_CONSEGNA})
        self.evento("NI1", self.GIACENZA)
        self.evento("NI1", self.IN_CONSEGNA)
        self.evento("NI2", self.IN_CONSEGNA)

    def evento(self, tracking, stato):
        self.db.insert_event({
            "tracking_number": tracking, "event_hash": tracking + stato,
            "event_at": "2026-09-17T08:00:00+02:00", "code": "", "state": stato,
            "note": "", "location": "", "severity": "NORMAL", "category": "X",
            "reason": "", "raw": {},
        })

    def test_il_vocabolario_raccoglie_gli_stati_incontrati(self):
        d = self.db.dashboard(include_closed=True)
        self.assertIn(self.GIACENZA, d["stati_gls"])
        self.assertIn(self.IN_CONSEGNA, d["stati_gls"])

    def test_distingue_chi_e_passato_da_uno_stato_da_chi_ci_si_trova_ora(self):
        d = self.db.dashboard(include_closed=True)
        indice = d["stati_gls"].index(self.GIACENZA)
        per_tracking = {x["tracking_number"]: x for x in d["shipments"]}
        # Entrambe hanno lo stesso ultimo stato...
        self.assertEqual(per_tracking["NI1"]["gls_status"], per_tracking["NI2"]["gls_status"])
        # ...ma solo una e' passata dalla giacenza.
        self.assertIn(indice, per_tracking["NI1"]["stati_storico"])
        self.assertNotIn(indice, per_tracking["NI2"]["stati_storico"])

    def test_una_spedizione_senza_eventi_non_rompe_nulla(self):
        self.db.upsert_shipment({"tracking_number": "NI3", "gls_status": ""})
        d = self.db.dashboard(include_closed=True)
        senza = next(x for x in d["shipments"] if x["tracking_number"] == "NI3")
        self.assertEqual(senza["stati_storico"], [])
