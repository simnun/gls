import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from core.assistente import Assistente, Vocabolario, interpreta, lettura_incerta
from core.assistente_ai import LettoreAI, costruisci_lettura
from core.db import Database
from core.timezones import ROME

OGGI = date(2026, 10, 30)
VOCABOLARIO = Vocabolario(operatori=["Daniela", "Marco"], province=["NA", "MI"],
                          citta=["Napoli", "Milano"], stati=[])


def risposta_json(**campi):
    """Una risposta completa come la produrrebbe lo schema."""
    base = {
        "fuori_portata": False, "motivo": "", "soggetto": "SPEDIZIONI", "intento": "conta",
        "percorso": False, "adesso": False, "periodo_da": "", "periodo_a": "",
        "periodo_etichetta": "", "data_su": "evento", "categorie": [], "gruppo": "",
        "gravita": "", "operatore": "", "provincia": "", "citta": "", "stato_gls": "",
        "testo_libero": "", "solo_cod": False, "gestite": "", "raggruppa": "",
        "tracking": "", "ordine": "",
    }
    base.update(campi)
    return base


class ClienteFinto:
    """Sta al posto dell'SDK: registra la richiesta e restituisce una risposta."""

    def __init__(self, payload=None, errore=None):
        self.payload = payload if payload is not None else risposta_json()
        self.errore = errore
        self.richieste = []
        self.messages = self

    def create(self, **kwargs):
        self.richieste.append(kwargs)
        if self.errore:
            raise self.errore
        return Risposta(self.payload)


class Blocco:
    def __init__(self, testo):
        self.type = "text"
        self.text = testo


class Uso:
    input_tokens = 300
    output_tokens = 200
    cache_read_input_tokens = 800
    cache_creation_input_tokens = 0


class Risposta:
    def __init__(self, payload):
        self.content = [Blocco(json.dumps(payload))]
        self.stop_reason = "end_turn"
        self.usage = Uso()


def lettore(payload=None, errore=None, **kw):
    istanza = LettoreAI(api_key="prova", **kw)
    istanza._cliente = ClienteFinto(payload, errore)
    return istanza


class LetturaAITests(unittest.TestCase):
    def test_la_richiesta_ha_la_forma_prevista(self):
        ai = lettore()
        ai.interpreta("una domanda qualsiasi", VOCABOLARIO, OGGI)
        inviata = ai._cliente.richieste[0]
        self.assertEqual(inviata["max_tokens"], 1024)
        self.assertEqual(inviata["output_config"]["effort"], "low")
        self.assertEqual(inviata["output_config"]["format"]["type"], "json_schema")
        # Le istruzioni devono essere marcate per la cache, altrimenti si paga
        # ogni volta un testo che non cambia mai.
        self.assertEqual(inviata["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertIn("Daniela", inviata["system"][0]["text"])
        self.assertIn("2026-10-30", inviata["system"][0]["text"])

    def test_scarta_i_valori_che_non_esistono(self):
        dati = risposta_json(operatore="Giuseppina", provincia="ZZ", citta="Lisbona",
                             categorie=["DELIVERED", "INVENTATA"])
        letta = costruisci_lettura(dati, VOCABOLARIO, OGGI)
        self.assertEqual(letta.operatore, "")
        self.assertEqual(letta.provincia, "")
        self.assertEqual(letta.citta, "")
        self.assertEqual(letta.categorie, ["DELIVERED"])

    def test_tiene_i_valori_che_esistono(self):
        dati = risposta_json(operatore="daniela", provincia="na", citta="Napoli")
        letta = costruisci_lettura(dati, VOCABOLARIO, OGGI)
        self.assertEqual((letta.operatore, letta.provincia, letta.citta),
                         ("Daniela", "NA", "Napoli"))

    def test_periodi_assurdi_vengono_rifiutati_o_corretti(self):
        futuro = costruisci_lettura(
            risposta_json(periodo_da="2027-01-01", periodo_a="2027-01-31"), VOCABOLARIO, OGGI)
        self.assertIsNone(futuro.periodo)
        rovesciato = costruisci_lettura(
            risposta_json(periodo_da="2026-10-30", periodo_a="2026-10-01"), VOCABOLARIO, OGGI)
        self.assertEqual(rovesciato.periodo.inizio, date(2026, 10, 1))
        oltre = costruisci_lettura(
            risposta_json(periodo_da="2026-10-01", periodo_a="2026-12-31"), VOCABOLARIO, OGGI)
        self.assertEqual(oltre.periodo.fine, OGGI)

    def test_si_ferma_al_tetto_giornaliero(self):
        ai = lettore(max_al_giorno=2)
        for _ in range(2):
            self.assertIsNotNone(ai.interpreta("domanda", VOCABOLARIO, OGGI)[0])
        letta, avviso = ai.interpreta("domanda", VOCABOLARIO, OGGI)
        self.assertIsNone(letta)
        self.assertIn("tetto", avviso)
        self.assertEqual(len(ai._cliente.richieste), 2)

    def test_senza_chiave_non_chiama_nessuno(self):
        ai = LettoreAI(api_key="")
        self.assertFalse(ai.configurato)
        self.assertEqual(ai.interpreta("domanda", VOCABOLARIO, OGGI), (None, ""))


class AssistenteConAITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "db.sqlite3")
        for numero, categoria, chiusa in (("NI1", "DELIVERED", True), ("NI2", "DELIVERED", True),
                                          ("NI3", "RETURN", True)):
            self.db.upsert_shipment({
                "tracking_number": numero, "order_name": "#" + numero, "customer_name": "Cliente",
                "city": "Napoli", "province": "NA", "total_amount": 10.0, "currency": "EUR",
                "payment_gateways": [], "is_cod": False, "gls_status": "Consegnata",
                "gls_note": "", "severity": "NORMAL", "category": categoria, "closed": chiusa,
                "gls_event_at": datetime(2026, 10, 29, 10, tzinfo=ROME).isoformat(timespec="seconds"),
                "workflow_status": "NEW",
            })

    def tearDown(self):
        self.tmp.cleanup()

    def test_claude_interviene_solo_sulle_domande_che_le_regole_non_capiscono(self):
        ai = lettore(risposta_json(categorie=["DELIVERED"], periodo_da="2026-10-29",
                                   periodo_a="2026-10-29", periodo_etichetta="ieri"))
        assistente = Assistente(self.db, durata_cache=0, lettore_ai=ai)
        # Domanda che le regole capiscono da sole: nessuna chiamata.
        assistente.rispondi("quante consegnate ieri?", oggi=OGGI)
        self.assertEqual(len(ai._cliente.richieste), 0)
        # Domanda sghemba: interviene Claude e il numero resta del database.
        esito = assistente.rispondi("com'e' andata la faccenda delle consegne di ieri?", oggi=OGGI)
        self.assertEqual(len(ai._cliente.richieste), 1)
        self.assertEqual(esito["numero"], 2)
        self.assertEqual(esito["interpretazione"]["fonte"], "claude")

    def test_fuori_portata_risponde_senza_numeri(self):
        ai = lettore(risposta_json(fuori_portata=True,
                                   motivo="Il monitor non conserva i costi di spedizione."))
        assistente = Assistente(self.db, durata_cache=0, lettore_ai=ai)
        esito = assistente.rispondi("quanto abbiamo speso di spedizioni?", oggi=OGGI)
        self.assertIsNone(esito["numero"])
        self.assertIn("costi di spedizione", esito["risposta"])

    def test_se_claude_non_risponde_il_sito_risponde_lo_stesso(self):
        ai = lettore(errore=RuntimeError("API irraggiungibile"))
        assistente = Assistente(self.db, durata_cache=0, lettore_ai=ai)
        esito = assistente.rispondi("com'e' andata la faccenda di ieri?", oggi=OGGI)
        self.assertTrue(esito["ok"])
        self.assertEqual(esito["interpretazione"]["fonte"], "regole")
        self.assertEqual(ai.consumo.errori, 1)

    def test_le_domande_semplici_non_costano_niente(self):
        ai = lettore()
        assistente = Assistente(self.db, durata_cache=0, lettore_ai=ai)
        for domanda in ("quante consegnate ieri?", "quanti rientri?",
                        "quante in giacenza adesso?", "quante a Napoli?"):
            assistente.rispondi(domanda, oggi=OGGI)
        self.assertEqual(len(ai._cliente.richieste), 0)


class SogliaTests(unittest.TestCase):
    def test_quando_scatta_la_chiamata(self):
        gratis = ["quante consegnate oggi", "quante in giacenza adesso",
                  "quanti rientri il mese scorso per provincia"]
        a_pagamento = ["come siamo messi stamattina", "quante sono volate via",
                       "fammi un quadro della situazione al nord"]
        for domanda in gratis:
            self.assertFalse(lettura_incerta(interpreta(domanda, VOCABOLARIO, OGGI)), domanda)
        for domanda in a_pagamento:
            self.assertTrue(lettura_incerta(interpreta(domanda, VOCABOLARIO, OGGI)), domanda)


if __name__ == "__main__":
    unittest.main()
