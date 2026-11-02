import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from core.assistente import Assistente, Vocabolario
from core.assistente_ai import Collaboratore, _risultato, _storia_pulita, costruisci_lettura
from core.db import Database
from core.timezones import ROME

OGGI = date(2026, 11, 2)
VOCABOLARIO = Vocabolario(operatori=["Daniela", "Marco"], province=["NA", "MI"],
                          citta=["Napoli", "Milano"], stati=[])


def filtri(**campi):
    base = {
        "soggetto": "SPEDIZIONI", "intento": "conta", "percorso": False, "adesso": False,
        "periodo_da": "", "periodo_a": "", "periodo_etichetta": "", "data_su": "evento",
        "categorie": [], "gruppo": "", "gravita": "", "operatore": "", "provincia": "",
        "citta": "", "stato_gls": "", "testo_libero": "", "solo_cod": False,
        "gestite": "", "raggruppa": "", "tracking": "", "ordine": "",
    }
    base.update(campi)
    return base


class BloccoTesto:
    def __init__(self, testo):
        self.type, self.text = "text", testo


class BloccoStrumento:
    def __init__(self, dati, identificativo="tu_1"):
        self.type, self.id, self.input, self.name = "tool_use", identificativo, dati, "interroga_monitor"


class Uso:
    input_tokens = 200
    output_tokens = 150
    cache_read_input_tokens = 1100
    cache_creation_input_tokens = 0


class RispostaFinta:
    def __init__(self, blocchi, motivo):
        self.content, self.stop_reason, self.usage = blocchi, motivo, Uso()


class ClienteFinto:
    """Restituisce una sequenza di risposte, registrando ogni richiesta."""

    def __init__(self, sequenza, errore=None):
        self.sequenza, self.errore, self.richieste = list(sequenza), errore, []
        self.messages = self

    def create(self, **kwargs):
        self.richieste.append(kwargs)
        if self.errore:
            raise self.errore
        return self.sequenza.pop(0)


def collega(sequenza=(), errore=None, **kw):
    istanza = Collaboratore(api_key="prova", **kw)
    istanza._cliente = ClienteFinto(sequenza, errore)
    return istanza


def conversazione_tipo(dati_strumento, frase):
    return [RispostaFinta([BloccoStrumento(dati_strumento)], "tool_use"),
            RispostaFinta([BloccoTesto(frase)], "end_turn")]


class BaseDati(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "db.sqlite3")
        for numero, categoria, chiusa in (("NI1", "DELIVERED", True), ("NI2", "DELIVERED", True),
                                          ("NI3", "RETURN", True)):
            self.db.upsert_shipment({
                "tracking_number": numero, "order_name": "#" + numero,
                "customer_name": "Mario Rossi", "customer_email": "mario@example.com",
                "customer_phone": "3330000000", "city": "Napoli", "province": "NA",
                "total_amount": 10.0, "currency": "EUR", "payment_gateways": [],
                "is_cod": False, "gls_status": "Consegnata", "gls_note": "",
                "severity": "NORMAL", "category": categoria, "closed": chiusa,
                "gls_event_at": datetime(2026, 11, 1, 10, tzinfo=ROME).isoformat(timespec="seconds"),
                "workflow_status": "NEW",
            })

    def tearDown(self):
        self.tmp.cleanup()


class ConversazioneTests(BaseDati):
    def test_il_numero_viene_dal_database_la_frase_dal_collega(self):
        ai = collega(conversazione_tipo(
            filtri(categorie=["DELIVERED"], periodo_da="2026-11-01", periodo_a="2026-11-01",
                   periodo_etichetta="ieri"),
            "Ieri ne sono state consegnate 2, entrambe a Napoli."))
        assistente = Assistente(self.db, durata_cache=0, collaboratore=ai)
        esito = assistente.rispondi("com'e' andata ieri con le consegne?", oggi=OGGI)
        self.assertEqual(esito["risposta"], "Ieri ne sono state consegnate 2, entrambe a Napoli.")
        self.assertEqual(esito["numero"], 2)          # calcolato, non raccontato
        self.assertEqual(esito["fonte"], "claude")
        self.assertEqual(len(esito["righe"]), 2)      # "mostrami quali" resta esatto
        self.assertNotIn("interpretazione", esito)    # niente parole chiave a schermo

    def test_il_risultato_mandato_al_modello_non_contiene_dati_dei_clienti(self):
        ai = collega(conversazione_tipo(filtri(categorie=["DELIVERED"]), "Due."))
        Assistente(self.db, durata_cache=0, collaboratore=ai).rispondi("quante consegnate?", oggi=OGGI)
        seconda = ai._cliente.richieste[1]
        inviato = json.dumps(seconda["messages"], default=str, ensure_ascii=False)
        for riservato in ("Mario Rossi", "mario@example.com", "3330000000"):
            self.assertNotIn(riservato, inviato)
        self.assertIn("NI1", inviato)                 # tracking e ordine servono

    def test_senza_chiamata_allo_strumento_risponde_e_basta(self):
        ai = collega([RispostaFinta([BloccoTesto("Ciao! Dimmi pure.")], "end_turn")])
        esito = Assistente(self.db, durata_cache=0, collaboratore=ai).rispondi("ciao", oggi=OGGI)
        self.assertEqual(esito["risposta"], "Ciao! Dimmi pure.")
        self.assertIsNone(esito["numero"])

    def test_se_il_collega_non_risponde_il_monitor_ripiega_e_lo_dice(self):
        ai = collega(errore=RuntimeError("API irraggiungibile"))
        esito = Assistente(self.db, durata_cache=0, collaboratore=ai).rispondi(
            "quante consegnate ieri?", oggi=OGGI)
        self.assertTrue(esito["ok"])
        self.assertTrue(esito["ripiego"])
        self.assertEqual(esito["numero"], 2)
        self.assertEqual(ai.consumo.errori, 1)

    def test_senza_chiave_nessun_ripiego_dichiarato(self):
        ai = Collaboratore(api_key="")
        esito = Assistente(self.db, durata_cache=0, collaboratore=ai).rispondi(
            "quante consegnate ieri?", oggi=OGGI)
        self.assertNotIn("ripiego", esito)
        self.assertEqual(esito["numero"], 2)

    def test_la_richiesta_porta_lo_strumento_e_le_istruzioni_in_cache(self):
        ai = collega(conversazione_tipo(filtri(), "Due."))
        Assistente(self.db, durata_cache=0, collaboratore=ai).rispondi("quante?", oggi=OGGI)
        prima = ai._cliente.richieste[0]
        self.assertEqual(prima["tools"][0]["name"], "interroga_monitor")
        self.assertTrue(prima["tools"][0]["strict"])
        self.assertEqual(prima["system"][0]["cache_control"], {"type": "ephemeral"})
        # Il vocabolario del database entra nelle istruzioni: senza, il modello
        # inventerebbe nomi di province e di operatori.
        self.assertIn("Province presenti: NA", prima["system"][0]["text"])
        self.assertIn("2026-11-02", prima["system"][0]["text"])

    def test_il_tetto_giornaliero_ferma_le_conversazioni(self):
        ai = collega(conversazione_tipo(filtri(), "Due.") * 1, max_al_giorno=1)
        assistente = Assistente(self.db, durata_cache=0, collaboratore=ai)
        assistente.rispondi("quante consegnate?", oggi=OGGI)
        esito = assistente.rispondi("e ieri?", oggi=OGGI)
        self.assertTrue(esito["ripiego"])


class StoriaTests(unittest.TestCase):
    def test_tiene_gli_ultimi_scambi_e_parte_sempre_da_chi_chiede(self):
        storia = [{"ruolo": "assistente", "testo": "vecchia"},
                  {"ruolo": "operatore", "testo": "quante oggi?"},
                  {"ruolo": "assistente", "testo": "tre"}]
        pulita = _storia_pulita(storia)
        self.assertEqual([m["role"] for m in pulita], ["user", "assistant"])

    def test_scarta_le_voci_vuote(self):
        self.assertEqual(_storia_pulita([{"ruolo": "operatore", "testo": "  "}]), [])


class FiltriTests(unittest.TestCase):
    def test_scarta_i_valori_che_non_esistono(self):
        letta = costruisci_lettura(
            filtri(operatore="Giuseppina", provincia="ZZ", citta="Lisbona",
                   categorie=["DELIVERED", "INVENTATA"]), VOCABOLARIO, OGGI)
        self.assertEqual((letta.operatore, letta.provincia, letta.citta), ("", "", ""))
        self.assertEqual(letta.categorie, ["DELIVERED"])

    def test_tiene_i_valori_che_esistono(self):
        letta = costruisci_lettura(filtri(operatore="daniela", provincia="na"), VOCABOLARIO, OGGI)
        self.assertEqual((letta.operatore, letta.provincia), ("Daniela", "NA"))

    def test_periodi_assurdi_corretti_o_rifiutati(self):
        self.assertIsNone(costruisci_lettura(
            filtri(periodo_da="2027-01-01", periodo_a="2027-01-31"), VOCABOLARIO, OGGI).periodo)
        rovesciato = costruisci_lettura(
            filtri(periodo_da="2026-11-02", periodo_a="2026-10-01"), VOCABOLARIO, OGGI)
        self.assertEqual(rovesciato.periodo.inizio, date(2026, 10, 1))
        oltre = costruisci_lettura(
            filtri(periodo_da="2026-10-01", periodo_a="2026-12-31"), VOCABOLARIO, OGGI)
        self.assertEqual(oltre.periodo.fine, OGGI)

    def test_il_risultato_riassume_cosa_e_stato_cercato(self):
        letta = costruisci_lettura(filtri(soggetto="GIACENZE", gestite="no"), VOCABOLARIO, OGGI)
        fuori = _risultato({"numero": 7, "totale_righe": 7, "righe": []}, letta)
        self.assertEqual(fuori["conteggio"], 7)
        self.assertEqual(fuori["ho_cercato"]["cosa"], "giacenze")
        self.assertEqual(fuori["ho_cercato"]["gestite"], "no")


if __name__ == "__main__":
    unittest.main()
