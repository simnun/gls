import tempfile
import unittest
from pathlib import Path

from core.db import Database


class DashboardLeggeraTests(unittest.TestCase):
    """Le pratiche concluse sono la gran parte dell'archivio e servono solo in
    tre viste: l'elenco di partenza porta solo quelle ancora in corso, con i
    conteggi delle altre per non falsare i contatori."""

    def setUp(self):
        self.db = Database(path=Path(tempfile.mkdtemp()) / "t.db")
        for i in range(10):
            self.db.upsert_shipment({
                "tracking_number": f"NI{i}",
                "category": "DELIVERED" if i < 6 else "RETURN" if i < 8 else "STORAGE",
                "closed": i < 8,
            })

    def test_di_base_arrivano_solo_le_spedizioni_in_corso(self):
        d = self.db.dashboard()
        self.assertEqual(len(d["shipments"]), 2)
        self.assertTrue(all(not x["closed"] for x in d["shipments"]))

    def test_i_conteggi_delle_concluse_ci_sono_comunque(self):
        d = self.db.dashboard()
        self.assertEqual(d["chiuse"], {"totale": 8, "consegnate": 6, "rientrate": 2})

    def test_su_richiesta_arriva_tutto(self):
        d = self.db.dashboard(include_closed=True)
        self.assertEqual(len(d["shipments"]), 10)
        self.assertEqual(d["chiuse"]["totale"], 8)

    def test_i_conteggi_reggono_un_archivio_senza_concluse(self):
        db = Database(path=Path(tempfile.mkdtemp()) / "v.db")
        db.upsert_shipment({"tracking_number": "NI1", "closed": False})
        self.assertEqual(db.dashboard()["chiuse"], {"totale": 0, "consegnate": 0, "rientrate": 0})
