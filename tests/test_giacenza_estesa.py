import tempfile
import unittest
from pathlib import Path

from core.db import Database


class GiacenzaOltreLaParolaTests(unittest.TestCase):
    """GLS scrive "giacenza" solo per alcune cause, ma un collo fermo in sede in
    attesa di istruzioni e' una giacenza comunque la si chiami: nel gestionale
    GLS compare nell'elenco giacenze insieme alle altre."""

    def setUp(self):
        self.db = Database(path=Path(tempfile.mkdtemp()) / "t.db")
        self.db.upsert_shipment({"tracking_number": "NI1"})

    def evento(self, quando, stato, categoria, nota=""):
        self.db.insert_event({
            "tracking_number": "NI1", "event_hash": f"{quando}{categoria}",
            "event_at": quando, "code": "", "state": stato, "note": nota,
            "location": "Catanzaro", "severity": "CRITICAL", "category": categoria,
            "reason": "", "raw": {},
        })

    def giacenze(self):
        return self.db.get_shipment("NI1").get("stock_cases") or []

    def test_indirizzo_errato_apre_una_giacenza(self):
        self.evento("2026-09-17T07:23:00+02:00", "Arrivata nella Sede GLS locale.", "IN_TRANSIT")
        self.evento("2026-09-17T10:07:00+02:00",
                    "Indirizzo errato, ti invitiamo a contattare il mittente per programmare la riconsegna.",
                    "ADDRESS_ERROR", "Manca civico")
        self.db.reconcile_stock_cases("NI1")
        casi = self.giacenze()
        self.assertEqual(len(casi), 1)
        self.assertEqual(casi[0]["status"], "OPEN")

    def test_anche_rifiuto_assenza_e_ritiro_in_sede(self):
        for categoria in ("REFUSED", "ABSENT", "PICKUP_AT_DEPOT", "RECIPIENT_CLOSED", "ACTION_REQUIRED"):
            with self.subTest(categoria):
                db = Database(path=Path(tempfile.mkdtemp()) / "t.db")
                db.upsert_shipment({"tracking_number": "NI1"})
                db.insert_event({
                    "tracking_number": "NI1", "event_hash": "x", "event_at": "2026-09-17T10:07:00+02:00",
                    "code": "", "state": "Fermo in sede", "note": "", "location": "",
                    "severity": "CRITICAL", "category": categoria, "reason": "", "raw": {},
                })
                db.reconcile_stock_cases("NI1")
                self.assertEqual(len(db.get_shipment("NI1").get("stock_cases") or []), 1)

    def test_un_nuovo_tentativo_automatico_non_e_una_giacenza(self):
        # Qui il collo torna sul mezzo da solo: non c'e' niente da svincolare.
        self.evento("2026-09-17T10:07:00+02:00",
                    "Oggi non siamo riusciti a consegnare. La consegna e' prevista nel primo giorno lavorativo.",
                    "DELIVERY_RETRY")
        self.db.reconcile_stock_cases("NI1")
        self.assertEqual(self.giacenze(), [])

    def test_la_ripartenza_chiude_la_giacenza(self):
        self.evento("2026-09-17T10:07:00+02:00", "Indirizzo errato", "ADDRESS_ERROR")
        self.evento("2026-09-18T08:00:00+02:00", "Consegna prevista nel corso della giornata odierna.",
                    "OUT_FOR_DELIVERY")
        self.db.reconcile_stock_cases("NI1")
        casi = self.giacenze()
        self.assertEqual(len(casi), 1)
        self.assertEqual(casi[0]["status"], "CLOSED")


class NessunDoppioneDiGiacenzaTests(unittest.TestCase):
    """Una giacenza che comincia dall'indirizzo errato e prosegue fino
    all'evento "in giacenza" resta un episodio solo, non due."""

    def setUp(self):
        self.db = Database(path=Path(tempfile.mkdtemp()) / "t.db")
        self.db.upsert_shipment({"tracking_number": "NI1"})

    def evento(self, quando, stato, categoria):
        self.db.insert_event({
            "tracking_number": "NI1", "event_hash": quando, "event_at": quando,
            "code": "", "state": stato, "note": "", "location": "",
            "severity": "CRITICAL", "category": categoria, "reason": "", "raw": {},
        })

    def test_l_episodio_resta_uno_solo(self):
        self.evento("2026-09-17T08:41:00+02:00", "Consegna prevista oggi", "OUT_FOR_DELIVERY")
        self.evento("2026-09-17T12:00:00+02:00", "Indirizzo errato", "ADDRESS_ERROR")
        self.evento("2026-09-18T10:49:00+02:00", "Spedizione in giacenza", "STORAGE")
        self.db.reconcile_stock_cases("NI1")
        casi = self.db.get_shipment("NI1").get("stock_cases") or []
        self.assertEqual(len(casi), 1)
        self.assertEqual(casi[0]["status"], "OPEN")
        self.assertIn("Indirizzo errato", casi[0]["entry_state"])

    def test_una_riga_vecchia_scollegata_viene_ripulita(self):
        # Come si presentava il dato creato dalla versione precedente.
        self.evento("2026-09-18T10:49:00+02:00", "Spedizione in giacenza", "STORAGE")
        self.db.reconcile_stock_cases("NI1")
        self.assertEqual(len(self.db.get_shipment("NI1").get("stock_cases") or []), 1)
        self.evento("2026-09-17T12:00:00+02:00", "Indirizzo errato", "ADDRESS_ERROR")
        self.db.reconcile_stock_cases("NI1")
        casi = self.db.get_shipment("NI1").get("stock_cases") or []
        self.assertEqual(len(casi), 1)
        self.assertIn("Indirizzo errato", casi[0]["entry_state"])
