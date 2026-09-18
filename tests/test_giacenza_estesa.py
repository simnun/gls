import tempfile
import unittest
from pathlib import Path

from core.db import Database, apre_giacenza

# Testi reali di GLS: le prove valgono solo se usano le parole vere.
INDIRIZZO_ERRATO = ("Indirizzo errato, ti invitiamo a contattare il mittente "
                    "per programmare la riconsegna.")
IN_GIACENZA = ("Spedizione in giacenza presso la sede GLS, siamo in attesa di istruzioni. "
               "Ti invitiamo a contattare il mittente per programmare la riconsegna.")


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
                    INDIRIZZO_ERRATO, "ADDRESS_ERROR", "Manca civico")
        self.db.reconcile_stock_cases("NI1")
        casi = self.giacenze()
        self.assertEqual(len(casi), 1)
        self.assertEqual(casi[0]["status"], "OPEN")

    def test_anche_rifiuto_secondo_tentativo_e_ritiro_in_sede(self):
        testi = [
            ("REFUSED", "La spedizione e' stata rifiutata. Ti invitiamo a contattare il mittente per programmare la riconsegna."),
            ("ACTION_REQUIRED", "Non siamo riusciti a raggiungerti al secondo tentativo di consegna, ti invitiamo a contattare il mittente per programmare la riconsegna."),
            ("PICKUP_AT_DEPOT", "Richiesto il ritiro della spedizione presso la sede GLS destinataria."),
            ("ADDRESS_ERROR", "Destinatario sconosciuto. In attesa di istruzioni dal mittente per eventuale riconsegna."),
        ]
        for categoria, testo in testi:
            with self.subTest(categoria):
                db = Database(path=Path(tempfile.mkdtemp()) / "t.db")
                db.upsert_shipment({"tracking_number": "NI1"})
                db.insert_event({
                    "tracking_number": "NI1", "event_hash": "x", "event_at": "2026-09-17T10:07:00+02:00",
                    "code": "", "state": testo, "note": "", "location": "",
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
        self.evento("2026-09-17T10:07:00+02:00", INDIRIZZO_ERRATO, "ADDRESS_ERROR")
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
        self.evento("2026-09-17T12:00:00+02:00", INDIRIZZO_ERRATO, "ADDRESS_ERROR")
        self.evento("2026-09-18T10:49:00+02:00", IN_GIACENZA, "STORAGE")
        self.db.reconcile_stock_cases("NI1")
        casi = self.db.get_shipment("NI1").get("stock_cases") or []
        self.assertEqual(len(casi), 1)
        self.assertEqual(casi[0]["status"], "OPEN")
        self.assertIn("Indirizzo errato", casi[0]["entry_state"])

    def test_una_riga_vecchia_scollegata_viene_ripulita(self):
        # Come si presentava il dato creato dalla versione precedente.
        self.evento("2026-09-18T10:49:00+02:00", IN_GIACENZA, "STORAGE")
        self.db.reconcile_stock_cases("NI1")
        self.assertEqual(len(self.db.get_shipment("NI1").get("stock_cases") or []), 1)
        self.evento("2026-09-17T12:00:00+02:00", INDIRIZZO_ERRATO, "ADDRESS_ERROR")
        self.db.reconcile_stock_cases("NI1")
        casi = self.db.get_shipment("NI1").get("stock_cases") or []
        self.assertEqual(len(casi), 1)
        self.assertIn("Indirizzo errato", casi[0]["entry_state"])


class MarcatoreGiacenzaTests(unittest.TestCase):
    """La giacenza si riconosce dalle parole con cui GLS chiede istruzioni al
    mittente, non dalla categoria che le abbiamo dato noi: un testo nuovo viene
    capito subito, senza aspettare che qualcuno lo classifichi."""

    FERME = [
        "Spedizione in giacenza presso la sede GLS, siamo in attesa di istruzioni.",
        "Indirizzo errato, ti invitiamo a contattare il mittente per programmare la riconsegna.",
        "La spedizione e' stata rifiutata. Ti invitiamo a contattare il mittente per programmare la riconsegna.",
        "Non siamo riusciti a raggiungerti al secondo tentativo di consegna, ti invitiamo a contattare il mittente per programmare la riconsegna.",
        "Destinatario chiuso per cessata attivita', ti invitiamo a contattare il mittente per programmare la riconsegna.",
        "Destinatario sconosciuto. In attesa di istruzioni dal mittente per eventuale riconsegna.",
        "Disponibile per il ritiro in Sede GLS.",
        "Richiesto il ritiro della spedizione presso la sede GLS destinataria.",
    ]

    # GLS annuncia un nuovo tentativo: il collo torna sul mezzo da solo.
    IN_VIAGGIO = [
        "Non siamo riusciti a raggiungerti al primo tentativo di consegna, ritenteremo la consegna nel primo giorno lavorativo disponibile.",
        "Oggi non siamo riusciti a consegnare. La consegna e' prevista nel primo giorno lavorativo disponibile.",
        "Non e' stato possibile consegnare a causa di un ritardo nel network. La consegna e' prevista nel primo giorno lavorativo disponibile.",
        "Destinatario chiuso per turno. Consegna prevista per il giorno lavorativo successivo.",
        "Destinatario chiuso per ferie. Consegna prevista alla data di riapertura.",
        "Importo contrassegno non disponibile. La consegna e' prevista nel primo giorno lavorativo disponibile.",
        "Consegna prevista nel corso della giornata odierna.",
        "Consegnata.",
        "Abbiamo restituito la spedizione al mittente, ti invitiamo a contattare il mittente.",
    ]

    def test_riconosce_ogni_collo_fermo_in_attesa_di_istruzioni(self):
        for testo in self.FERME:
            with self.subTest(testo[:45]):
                self.assertTrue(apre_giacenza(testo))

    def test_non_scambia_per_giacenza_un_tentativo_automatico(self):
        for testo in self.IN_VIAGGIO:
            with self.subTest(testo[:45]):
                self.assertFalse(apre_giacenza(testo))

    def test_una_giacenza_dichiarata_vale_anche_con_un_testo_mai_visto(self):
        self.assertTrue(apre_giacenza("Testo che GLS non ha mai usato", "", "STORAGE"))

    def test_il_marcatore_vale_anche_se_sta_nella_causale(self):
        self.assertTrue(apre_giacenza("Consegna non riuscita", "in attesa di istruzioni dal mittente"))
