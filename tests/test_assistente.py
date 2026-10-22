import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from core.assistente import Assistente, Vocabolario, interpreta
from core.db import Database
from core.timezones import ROME

OGGI = date(2026, 10, 21)


def momento_roma(giorno: date, ora: int = 10, minuto: int = 0) -> str:
    return datetime(giorno.year, giorno.month, giorno.day, ora, minuto, tzinfo=ROME).isoformat(timespec="seconds")


def momento_utc(giorno: date, ora: int = 10, minuto: int = 0) -> str:
    locale = datetime(giorno.year, giorno.month, giorno.day, ora, minuto, tzinfo=ROME)
    return locale.astimezone(timezone.utc).isoformat(timespec="milliseconds")


class AssistenteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "db.sqlite3")
        self.assistente = Assistente(self.db, durata_cache=0)

    def tearDown(self):
        self.tmp.cleanup()

    # -- aiutanti ---------------------------------------------------------
    def spedizione(self, tracking, **campi):
        base = {
            "tracking_number": tracking, "order_name": campi.pop("ordine", "#1000"),
            "order_created_at": campi.pop("ordine_at", momento_utc(OGGI - timedelta(days=5))),
            "customer_name": campi.pop("cliente", "Mario Rossi"),
            "city": campi.pop("citta", "Napoli"), "province": campi.pop("provincia", "NA"),
            "total_amount": 50.0, "currency": "EUR", "payment_gateways": [],
            "is_cod": campi.pop("cod", False), "gls_status": campi.pop("stato", "In transito"),
            "gls_note": campi.pop("nota", ""), "severity": campi.pop("gravita", "NORMAL"),
            "category": campi.pop("categoria", "IN_TRANSIT"),
            "gls_event_at": campi.pop("evento_at", momento_roma(OGGI)),
            "closed": campi.pop("chiusa", False),
            "workflow_status": campi.pop("workflow", "NEW"),
        }
        base.update(campi)
        self.db.upsert_shipment(base)

    def ricostruisci(self, *tracking):
        for numero in tracking:
            self.db.reconcile_stock_cases(numero)

    def evento(self, tracking, quando, stato, categoria, nota="", chiave=None):
        self.db.insert_event({
            "tracking_number": tracking, "event_hash": chiave or f"{tracking}-{quando}-{stato}",
            "event_at": quando, "code": "", "state": stato, "note": nota, "location": "Napoli",
            "severity": "CRITICAL" if categoria == "STORAGE" else "NORMAL",
            "category": categoria, "reason": "", "raw": {},
        })

    def azione(self, tracking, operatore, quando=None, etichetta="Cliente chiamato"):
        """Azione con data controllata: `add_operator_action` userebbe adesso."""
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO operator_actions (tracking_number, action_type, action_label,"
                " note, operator_name, created_at) VALUES (?,?,?,?,?,?)",
                (tracking, "NOTE", etichetta, "", operatore, quando or momento_utc(OGGI)),
            )
            conn.execute(
                "UPDATE shipments SET last_operator_action=?, last_operator_name=?,"
                " last_operator_action_at=? WHERE tracking_number=?",
                (etichetta, operatore, quando or momento_utc(OGGI), tracking),
            )

    def chiedi(self, domanda):
        return self.assistente.rispondi(domanda, oggi=OGGI)

    # -- interpretazione --------------------------------------------------
    def test_legge_le_quattro_domande_di_esempio(self):
        vocabolario = Vocabolario(operatori=["Daniela"], province=["NA"], citta=["Napoli"], stati=[])
        giacenza = interpreta("Quanti sono in giacenza ora?", vocabolario, OGGI)
        self.assertEqual(giacenza.soggetto, "GIACENZE")
        self.assertTrue(giacenza.adesso)

        percorso = interpreta("Quante spedizioni sono andate in giacenza, poi gestite e consegnate?", vocabolario, OGGI)
        self.assertTrue(percorso.percorso)

        operatore = interpreta("Quante spedizioni l'operatore Daniela ha gestito oggi", vocabolario, OGGI)
        self.assertEqual(operatore.soggetto, "AZIONI")
        self.assertEqual(operatore.operatore, "Daniela")
        self.assertEqual(operatore.periodo.etichetta, "oggi")

        consegne = interpreta("Quante consegnate oggi?", vocabolario, OGGI)
        self.assertEqual(consegne.categorie, ["DELIVERED"])
        self.assertEqual(consegne.periodo.etichetta, "oggi")

    def test_dichiara_le_parole_non_capite(self):
        vocabolario = Vocabolario()
        lettura = interpreta("quante spedizioni sono volate via ieri", vocabolario, OGGI)
        self.assertIn("volate", lettura.non_capito)

    def test_periodi(self):
        vocabolario = Vocabolario()
        self.assertEqual(interpreta("quante ieri", vocabolario, OGGI).periodo.inizio, date(2026, 10, 20))
        settimana = interpreta("quante la settimana scorsa", vocabolario, OGGI).periodo
        self.assertEqual((settimana.inizio, settimana.fine), (date(2026, 10, 12), date(2026, 10, 18)))
        mese = interpreta("quante a settembre", vocabolario, OGGI).periodo
        self.assertEqual((mese.inizio, mese.fine), (date(2026, 9, 1), date(2026, 9, 30)))
        ultimi = interpreta("quante negli ultimi 3 giorni", vocabolario, OGGI).periodo
        self.assertEqual(ultimi.inizio, date(2026, 10, 19))
        intervallo = interpreta("quante dal 1/10 al 5/10", vocabolario, OGGI).periodo
        self.assertEqual((intervallo.inizio, intervallo.fine), (date(2026, 10, 1), date(2026, 10, 5)))

    # -- conteggi ---------------------------------------------------------
    def test_conta_le_consegne_di_oggi(self):
        self.spedizione("NI1", categoria="DELIVERED", stato="Consegnata", evento_at=momento_roma(OGGI), chiusa=True)
        self.spedizione("NI2", ordine="#1002", categoria="DELIVERED", stato="Consegnata",
                        evento_at=momento_roma(OGGI - timedelta(days=1)), chiusa=True)
        esito = self.chiedi("Quante consegnate oggi?")
        self.assertEqual(esito["numero"], 1)
        self.assertIn("1", esito["risposta"])
        self.assertEqual(len(esito["righe"]), 1)

    def test_conta_le_giacenze_aperte_adesso(self):
        self.spedizione("NI1", categoria="STORAGE", stato="In giacenza")
        self.evento("NI1", momento_roma(OGGI - timedelta(days=2)), "Spedizione in giacenza",
                    "STORAGE", "In attesa di istruzioni dal mittente")
        self.spedizione("NI2", ordine="#1002", categoria="DELIVERED", stato="Consegnata", chiusa=True)
        self.evento("NI2", momento_roma(OGGI - timedelta(days=5)), "Spedizione in giacenza",
                    "STORAGE", "In attesa di istruzioni dal mittente")
        self.evento("NI2", momento_roma(OGGI - timedelta(days=4)), "Consegnata", "DELIVERED")
        self.ricostruisci("NI1", "NI2")
        esito = self.chiedi("Quanti sono in giacenza ora?")
        self.assertEqual(esito["numero"], 1)
        self.assertIn("Adesso", esito["risposta"])

    def test_percorso_delle_giacenze(self):
        # Una entrata, gestita e consegnata; una entrata e mai toccata.
        self.spedizione("NI1", categoria="DELIVERED", stato="Consegnata", chiusa=True)
        self.evento("NI1", momento_roma(OGGI - timedelta(days=6)), "Spedizione in giacenza",
                    "STORAGE", "In attesa di istruzioni dal mittente")
        self.azione("NI1", "Daniela", quando=momento_utc(OGGI - timedelta(days=4)))
        self.evento("NI1", momento_roma(OGGI - timedelta(days=2)), "Consegnata", "DELIVERED")
        self.spedizione("NI2", ordine="#1002", categoria="STORAGE", stato="In giacenza")
        self.evento("NI2", momento_roma(OGGI - timedelta(days=3)), "Spedizione in giacenza",
                    "STORAGE", "In attesa di istruzioni dal mittente")
        self.ricostruisci("NI1", "NI2")
        esito = self.chiedi("Quante spedizioni sono andate in giacenza, poi gestite e consegnate?")
        dati = esito["dettagli"]
        self.assertEqual(dati["entrate"], 2)
        self.assertEqual(dati["gestite"], 1)
        self.assertEqual(dati["gestite_consegnate"], 1)
        self.assertEqual(dati["aperte"], 1)

    def test_azioni_di_un_operatore_oggi(self):
        self.spedizione("NI1")
        self.spedizione("NI2", ordine="#1002")
        self.azione("NI1", "Daniela")
        self.azione("NI2", "Daniela", etichetta="GLS contattato")
        self.azione("NI2", "Marco", etichetta="Nota")
        esito = self.chiedi("Quante spedizioni l'operatore Daniela ha gestito oggi")
        self.assertEqual(esito["numero"], 2)
        self.assertIn("Daniela", esito["risposta"])
        self.assertIn("2 spedizioni", esito["risposta"])

    def test_azione_di_sera_resta_nel_giorno_italiano(self):
        # 23:30 in Italia sono le 21:30 UTC: tagliare la stringa UTC la
        # sposterebbe di un giorno.
        self.spedizione("NI1")
        self.azione("NI1", "Daniela", quando=momento_utc(OGGI, 23, 30), etichetta="Nota tarda")
        esito = self.chiedi("Quante ne ha gestite Daniela oggi?")
        self.assertEqual(esito["numero"], 1)

    def test_raggruppa_per_provincia(self):
        self.spedizione("NI1", categoria="RETURN", stato="Rientro", provincia="NA",
                        evento_at=momento_roma(OGGI))
        self.spedizione("NI2", ordine="#1002", categoria="RETURN", stato="Rientro", provincia="MI",
                        citta="Milano", evento_at=momento_roma(OGGI))
        self.spedizione("NI3", ordine="#1003", categoria="RETURN", stato="Rientro", provincia="NA",
                        evento_at=momento_roma(OGGI))
        esito = self.chiedi("quanti rientri oggi per provincia")
        self.assertEqual(esito["numero"], 3)
        self.assertEqual(esito["tabella"]["righe"][0], ["NA", 2])

    def test_da_verificare_usa_la_definizione_del_sito(self):
        self.spedizione("NI1", gravita="CRITICAL", workflow="NEW", categoria="ABSENT")
        self.spedizione("NI2", ordine="#1002", gravita="CRITICAL", workflow="IN_PROGRESS", categoria="ABSENT")
        self.spedizione("NI3", ordine="#1003", gravita="WATCH", workflow="NEW", categoria="ABSENT")
        self.assertEqual(self.chiedi("quante da verificare adesso")["numero"], 1)
        self.assertEqual(self.chiedi("quante in lavorazione")["numero"], 1)

    def test_dettaglio_per_ordine(self):
        self.spedizione("NI1", ordine="#278700", stato="Consegnata", categoria="DELIVERED")
        esito = self.chiedi("che fine ha fatto l'ordine 278700?")
        self.assertIn("NI1", esito["risposta"])
        self.assertIn("Consegnata", esito["risposta"])

    def test_domanda_senza_risultati_non_inventa(self):
        esito = self.chiedi("Quante consegnate oggi?")
        self.assertEqual(esito["numero"], 0)
        self.assertIn("Non risultano", esito["risposta"])

    def test_filtro_per_stato_gls_citato(self):
        self.spedizione("NI1", stato="Spedizione in giacenza, in attesa di istruzioni dal mittente",
                        categoria="STORAGE")
        self.evento("NI1", momento_roma(OGGI), "Spedizione in giacenza, in attesa di istruzioni dal mittente",
                    "STORAGE")
        self.ricostruisci("NI1")
        self.spedizione("NI2", ordine="#1002", stato="In transito", categoria="IN_TRANSIT")
        esito = self.chiedi("quante sono in attesa di istruzioni dal mittente")
        self.assertEqual(esito["numero"], 1)


if __name__ == "__main__":
    unittest.main()
