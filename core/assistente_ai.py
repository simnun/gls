"""Interpretazione della domanda affidata a Claude, quando serve davvero.

Il motore locale (`core/assistente.py`) capisce le domande fatte con le parole
del dominio, ma si ferma appena la frase si articola. Qui entra Claude, e fa
una cosa sola: **leggere la domanda e riempire la stessa scheda di filtri** che
riempirebbe il motore locale. I numeri continuano a uscire dal database, le
query sono le stesse, l'elenco «mostrami quali» pure.

E' la scelta piu' economica possibile, oltre che la piu' sicura:

- in uscita Claude scrive un oggetto JSON di poche righe, e l'uscita costa
  cinque volte l'entrata: meno scrive, meno costa;
- l'entrata (istruzioni + schema) e' sempre identica, quindi viene messa in
  cache dal secondo messaggio in poi e costa un ventesimo;
- il motore locale risponde da solo alle domande che gia' capisce, quindi
  Claude viene chiamato solo quando serve;
- se la chiave manca, il credito finisce o l'API non risponde, il sito
  continua a funzionare con la lettura locale. Non si rompe niente.

Nulla di cio' che Claude scrive finisce in una query senza essere prima
confrontato con i valori che esistono davvero nel database: un operatore
inventato o una categoria inesistente vengono scartati, non cercati.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .assistente import (
    ETICHETTE_CATEGORIA,
    ETICHETTE_GRUPPO,
    Lettura,
    Periodo,
    Vocabolario,
)
from .timezones import ROME

MODELLO_PREDEFINITO = "claude-opus-5-5"
# L'uscita e' un oggetto JSON di poche righe: il tetto serve solo a impedire
# che una risposta anomala costi molto. Resta largo per il ragionamento.
MAX_TOKEN_USCITA = 1024
# Prezzi per milione di token (entrata, uscita, lettura dalla cache), usati
# solo per mostrare la spesa stimata: non cambiano il comportamento.
PREZZI = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}

CATEGORIE_VALIDE = sorted(ETICHETTE_CATEGORIA)
GRUPPI_VALIDI = sorted(ETICHETTE_GRUPPO)
RAGGRUPPAMENTI = ["", "provincia", "citta", "operatore", "stato", "categoria",
                  "giorno", "settimana", "mese"]

# Lo schema viaggia a ogni domanda e si paga a peso, mentre le istruzioni di
# sistema vengono lette dalla cache: qui restano solo i vincoli (i valori
# ammessi), le spiegazioni stanno nelle istruzioni.
SCHEMA = {
    "type": "object",
    "properties": {
        "fuori_portata": {"type": "boolean"},
        "motivo": {"type": "string"},
        "soggetto": {"type": "string", "enum": ["SPEDIZIONI", "GIACENZE", "AZIONI", "SVINCOLI"]},
        "intento": {"type": "string", "enum": ["conta", "elenca", "raggruppa", "dettaglio"]},
        "percorso": {"type": "boolean"},
        "adesso": {"type": "boolean"},
        "periodo_da": {"type": "string"},
        "periodo_a": {"type": "string"},
        "periodo_etichetta": {"type": "string"},
        "data_su": {"type": "string", "enum": ["evento", "ordine"]},
        "categorie": {"type": "array", "items": {"type": "string", "enum": CATEGORIE_VALIDE}},
        "gruppo": {"type": "string", "enum": [""] + GRUPPI_VALIDI},
        "gravita": {"type": "string", "enum": ["", "CRITICAL", "WARNING", "WATCH", "INFO", "NORMAL"]},
        "operatore": {"type": "string"},
        "provincia": {"type": "string"},
        "citta": {"type": "string"},
        "stato_gls": {"type": "string"},
        "testo_libero": {"type": "string"},
        "solo_cod": {"type": "boolean"},
        "gestite": {"type": "string", "enum": ["", "si", "no"]},
        "raggruppa": {"type": "string", "enum": RAGGRUPPAMENTI},
        "tracking": {"type": "string"},
        "ordine": {"type": "string"},
    },
    "required": [
        "fuori_portata", "motivo", "soggetto", "intento", "percorso", "adesso",
        "periodo_da", "periodo_a", "periodo_etichetta", "data_su", "categorie",
        "gruppo", "gravita", "operatore", "provincia", "citta", "stato_gls",
        "testo_libero", "solo_cod", "gestite", "raggruppa", "tracking", "ordine",
    ],
    "additionalProperties": False,
}

ISTRUZIONI = """Sei il traduttore di domande di un monitor di spedizioni GLS di un negozio online italiano.

Non devi rispondere alla domanda e non devi calcolare numeri: devi solo tradurre la domanda dell'operatore nei filtri con cui il monitor interroga il proprio database. I conteggi li fa il database.

Cosa contiene il monitor:
- SPEDIZIONI: una riga per collo affidato a GLS, con l'ultimo stato GLS, una categoria, una gravita', la provincia e la citta' del destinatario, se e' in contrassegno, e la lavorazione interna.
- GIACENZE: ogni volta che un collo si e' fermato in sede GLS in attesa di istruzioni. Ha una data di entrata, un esito (consegnata, rientrata, ripartita) e puo' essere ancora aperta.
- AZIONI: quello che gli operatori registrano sulle pratiche (messaggio al cliente, telefonata, GLS contattato, nota).
- SVINCOLI: le istruzioni di riconsegna inviate a GLS.

Come scegliere il soggetto:
- "quante spedizioni...", "quante consegnate", "quanti rientri" -> SPEDIZIONI
- "quante in giacenza", "quante sono andate in giacenza" -> GIACENZE
- "quante ne ha gestite <nome>", "chi ha lavorato di piu'" -> AZIONI
- "quanti svincoli abbiamo mandato" -> SVINCOLI

Regole importanti:
- Se la domanda riguarda la situazione di adesso ("quante sono in giacenza ora"), metti adesso=true e lascia il periodo vuoto.
- Se la domanda ha un periodo, scrivi periodo_da e periodo_a come date AAAA-MM-GG, e periodo_etichetta come lo direbbe un italiano.
- percorso=true solo quando si chiede che fine hanno fatto le giacenze (entrate, poi gestite, poi consegnate o rientrate).
- data_su="ordine" solo se la domanda parla esplicitamente della data dell'ordine; altrimenti sempre "evento".
- solo_cod=true solo per il metodo di pagamento in contrassegno. Se il problema e' l'importo del contrassegno, usa invece la categoria COD_ISSUE.
- Usa operatore, provincia e citta' soltanto con i valori elencati qui sotto. Se il nome citato non c'e', lascia il campo vuoto.
- Se la domanda non si puo' rispondere con questi dati (per esempio chiede il perche' di un andamento, o dati che il monitor non ha come i costi di spedizione), metti fuori_portata=true e spiega in una riga cosa manca. Non inventare un filtro pur di rispondere.

I campi da compilare:
- fuori_portata, motivo: vedi la regola sopra; motivo e' stringa vuota quando la domanda e' rispondibile.
- soggetto, intento: conta un numero, elenca le righe, raggruppa per una dimensione, dettaglio di una spedizione precisa.
- percorso, adesso: vedi le regole sopra.
- periodo_da, periodo_a: date AAAA-MM-GG, stringa vuota se la domanda non ha periodo. periodo_etichetta: come si chiama quel periodo in italiano.
- data_su: "evento" (data dell'ultimo stato GLS) oppure "ordine" (data dell'ordine Shopify).
- categorie: zero o piu' codici fra quelli elencati sotto. gruppo e gravita': una sola voce o stringa vuota.
- operatore, provincia, citta': solo valori presenti negli elenchi qui sotto, altrimenti stringa vuota.
- stato_gls: un pezzo del testo dello stato GLS da cercare. testo_libero: testo da cercare in ordine, cliente, stato o nota.
- solo_cod: solo per il pagamento in contrassegno. gestite: "si" se il team e' gia' intervenuto, "no" se la pratica e' intatta, altrimenti stringa vuota.
- raggruppa: la dimensione su cui dividere il conteggio, stringa vuota se non serve.
- tracking, ordine: compilali solo se la domanda cita un numero preciso; ordine senza cancelletto.

Rispondi solo con l'oggetto JSON richiesto."""


@dataclass
class Consumo:
    """Quanto e' costato finora, per poterlo mostrare e per fermarsi in tempo."""

    chiamate: int = 0
    token_entrata: int = 0
    token_cache: int = 0
    token_uscita: int = 0
    errori: int = 0
    giorno: str = ""

    def costo(self, modello: str) -> float:
        entrata, uscita, cache = PREZZI.get(modello, PREZZI[MODELLO_PREDEFINITO])
        return (
            self.token_entrata * entrata / 1_000_000
            + self.token_cache * cache / 1_000_000
            + self.token_uscita * uscita / 1_000_000
        )


class LettoreAI:
    """Fa leggere la domanda a Claude e restituisce una `Lettura` verificata."""

    def __init__(
        self,
        api_key: str | None = None,
        modello: str | None = None,
        max_al_giorno: int = 0,
    ):
        self.api_key = (api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")).strip()
        self.modello = (modello or os.environ.get("ASSISTENTE_MODELLO") or MODELLO_PREDEFINITO).strip()
        if max_al_giorno:
            self.max_al_giorno = max_al_giorno
        else:
            try:
                self.max_al_giorno = int(os.environ.get("ASSISTENTE_MAX_GIORNO", "300"))
            except ValueError:
                self.max_al_giorno = 300
        self.consumo = Consumo()
        self._cliente: Any = None
        self._lock = threading.Lock()
        self.ultimo_errore = ""

    # -- stato ------------------------------------------------------------
    @property
    def configurato(self) -> bool:
        return bool(self.api_key)

    def _entro_il_tetto(self, oggi: date) -> bool:
        if self.consumo.giorno != oggi.isoformat():
            self.consumo = Consumo(giorno=oggi.isoformat())
        return self.consumo.chiamate < self.max_al_giorno

    def stato(self) -> dict[str, Any]:
        return {
            "attivo": self.configurato,
            "modello": self.modello,
            "chiamate_oggi": self.consumo.chiamate,
            "tetto_giornaliero": self.max_al_giorno,
            "costo_stimato_oggi": round(self.consumo.costo(self.modello), 4),
            "errori": self.consumo.errori,
            "ultimo_errore": self.ultimo_errore[:200],
        }

    # -- cliente ----------------------------------------------------------
    def _ottieni_cliente(self) -> Any:
        """Importa l'SDK solo alla prima domanda che lo richiede.

        Su un servizio con 512 MB di memoria conta: se nessuno usa
        l'assistente, la libreria non viene nemmeno caricata.
        """
        if self._cliente is not None:
            return self._cliente
        with self._lock:
            if self._cliente is None:
                import anthropic

                self._cliente = anthropic.Anthropic(api_key=self.api_key, max_retries=1, timeout=25.0)
        return self._cliente

    # -- lettura ----------------------------------------------------------
    def interpreta(
        self,
        domanda: str,
        vocabolario: Vocabolario,
        oggi: date | None = None,
    ) -> tuple[Lettura | None, str]:
        """Ritorna la lettura di Claude e un eventuale messaggio da mostrare."""
        oggi = oggi or datetime.now(ROME).date()
        if not self.configurato:
            return None, ""
        if not self._entro_il_tetto(oggi):
            return None, "Ho raggiunto il tetto di domande complesse per oggi."
        try:
            cliente = self._ottieni_cliente()
        except Exception as exc:  # libreria assente o chiave malformata
            self.ultimo_errore = f"{type(exc).__name__}: {exc}"
            self.consumo.errori += 1
            return None, ""

        istruzioni = self._istruzioni(vocabolario, oggi)
        try:
            risposta = cliente.messages.create(
                model=self.modello,
                max_tokens=MAX_TOKEN_USCITA,
                # Le istruzioni non cambiano mai: dalla seconda domanda in poi
                # vengono lette dalla cache e costano un ventesimo.
                system=[{"type": "text", "text": istruzioni,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": domanda[:400]}],
                # Tradurre una frase in filtri non richiede ragionamento
                # profondo: l'uscita e' la voce cara, e qui va tenuta corta.
                output_config={"effort": "low",
                               "format": {"type": "json_schema", "schema": SCHEMA}},
            )
        except Exception as exc:
            self.ultimo_errore = f"{type(exc).__name__}: {exc}"
            self.consumo.errori += 1
            return None, ""

        self._registra(risposta)
        if getattr(risposta, "stop_reason", "") == "refusal":
            return None, ""
        testo = next((b.text for b in risposta.content if getattr(b, "type", "") == "text"), "")
        if not testo:
            return None, ""
        try:
            dati = json.loads(testo)
        except json.JSONDecodeError:
            self.consumo.errori += 1
            return None, ""
        if dati.get("fuori_portata"):
            return None, (dati.get("motivo") or "").strip()
        return costruisci_lettura(dati, vocabolario, oggi), ""

    def _registra(self, risposta: Any) -> None:
        uso = getattr(risposta, "usage", None)
        self.consumo.chiamate += 1
        if not uso:
            return
        self.consumo.token_entrata += int(getattr(uso, "input_tokens", 0) or 0)
        self.consumo.token_uscita += int(getattr(uso, "output_tokens", 0) or 0)
        self.consumo.token_cache += int(getattr(uso, "cache_read_input_tokens", 0) or 0)
        # La scrittura in cache costa un quarto in piu' della prima lettura:
        # va contata con l'entrata, non con la cache.
        self.consumo.token_entrata += int(getattr(uso, "cache_creation_input_tokens", 0) or 0)

    def _istruzioni(self, vocabolario: Vocabolario, oggi: date) -> str:
        """Le istruzioni cambiano solo quando cambia il vocabolario.

        Restano in testa al messaggio e identiche fra una domanda e l'altra:
        e' questa stabilita' che le fa costare quasi niente dalla seconda in
        poi. La data entra qui perche' serve a interpretare "ieri", e cambia
        una volta al giorno: la cache si rinnova all'alba, non a ogni domanda.
        """
        pezzi = [ISTRUZIONI, "", f"Oggi e' {oggi.isoformat()} ({_giorno_italiano(oggi)})."]
        if vocabolario.operatori:
            pezzi.append("Operatori esistenti: " + ", ".join(sorted(vocabolario.operatori)) + ".")
        if vocabolario.province:
            pezzi.append("Province presenti: " + ", ".join(sorted(vocabolario.province)) + ".")
        pezzi.append("Categorie disponibili: " + ", ".join(
            f"{codice} ({ETICHETTE_CATEGORIA[codice]})" for codice in CATEGORIE_VALIDE) + ".")
        return "\n".join(pezzi)


GIORNI = ("lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica")


def _giorno_italiano(giorno: date) -> str:
    return GIORNI[giorno.weekday()]


def costruisci_lettura(dati: dict[str, Any], vocabolario: Vocabolario, oggi: date) -> Lettura:
    """Trasforma il JSON in una `Lettura`, scartando cio' che non esiste.

    Lo schema obbliga Claude a scegliere fra valori previsti per categorie,
    gruppi e raggruppamenti, ma nomi di persone e di citta' restano testo
    libero: vengono accettati solo se corrispondono a un valore presente nel
    database. Meglio un filtro in meno che un filtro inventato.
    """
    lettura = Lettura()
    lettura.soggetto = _scelta(dati.get("soggetto"), ("SPEDIZIONI", "GIACENZE", "AZIONI", "SVINCOLI"), "SPEDIZIONI")
    lettura.intento = _scelta(dati.get("intento"), ("conta", "elenca", "raggruppa", "dettaglio"), "conta")
    lettura.percorso = bool(dati.get("percorso"))
    lettura.adesso = bool(dati.get("adesso"))
    lettura.data_su = _scelta(dati.get("data_su"), ("evento", "ordine"), "evento")
    lettura.categorie = [c for c in (dati.get("categorie") or []) if c in ETICHETTE_CATEGORIA]
    lettura.gruppo = _scelta(dati.get("gruppo"), tuple(GRUPPI_VALIDI), "")
    lettura.gravita = _scelta(dati.get("gravita"), ("CRITICAL", "WARNING", "WATCH", "INFO", "NORMAL"), "")
    lettura.raggruppa = _scelta(dati.get("raggruppa"), tuple(r for r in RAGGRUPPAMENTI if r), "")
    lettura.solo_cod = bool(dati.get("solo_cod"))
    lettura.gestite = _scelta(dati.get("gestite"), ("si", "no"), "")
    lettura.stato_gls = str(dati.get("stato_gls") or "").strip()[:120]
    lettura.testo_libero = str(dati.get("testo_libero") or "").strip()[:120]
    lettura.tracking = str(dati.get("tracking") or "").strip().upper()[:30]
    lettura.ordine = "".join(ch for ch in str(dati.get("ordine") or "") if ch.isdigit())[:10]

    nome = str(dati.get("operatore") or "").strip()
    if nome:
        lettura.operatore = next(
            (o for o in vocabolario.operatori if o.lower() == nome.lower()), "")
    sigla = str(dati.get("provincia") or "").strip()
    if sigla:
        lettura.provincia = next(
            (p for p in vocabolario.province if p.lower() == sigla.lower()), "")
    citta = str(dati.get("citta") or "").strip()
    if citta:
        lettura.citta = next(
            (c for c in vocabolario.citta if c.lower() == citta.lower()), "")

    lettura.periodo = _periodo(dati, oggi)
    if lettura.intento == "raggruppa" and not lettura.raggruppa:
        lettura.raggruppa = "categoria"
    if lettura.raggruppa:
        lettura.intento = "raggruppa"
    if lettura.tracking or lettura.ordine:
        lettura.intento = "dettaglio"
    if lettura.soggetto == "GIACENZE" and not lettura.periodo and not lettura.percorso:
        lettura.adesso = True
    if lettura.percorso:
        lettura.categorie = []
        lettura.gestite = ""
    return lettura


def _scelta(valore: Any, ammessi: tuple[str, ...], predefinito: str) -> str:
    testo = str(valore or "").strip()
    return testo if testo in ammessi else predefinito


def _periodo(dati: dict[str, Any], oggi: date) -> Periodo | None:
    """Accetta il periodo solo se e' un intervallo di date sensato."""
    inizio = _data(dati.get("periodo_da"))
    fine = _data(dati.get("periodo_a"))
    if not inizio and not fine:
        return None
    inizio = inizio or fine
    fine = fine or inizio
    if inizio > fine:
        inizio, fine = fine, inizio
    # Un periodo che comincia prima del monitor o finisce nel futuro e' un
    # errore di lettura, non una domanda: meglio nessun filtro che uno storto.
    if inizio.year < 2020 or (inizio - oggi).days > 1:
        return None
    if fine > oggi:
        fine = oggi
    etichetta = str(dati.get("periodo_etichetta") or "").strip()[:60]
    if not etichetta:
        etichetta = (f"il {inizio.strftime('%d/%m/%Y')}" if inizio == fine
                     else f"dal {inizio.strftime('%d/%m/%Y')} al {fine.strftime('%d/%m/%Y')}")
    return Periodo(inizio, fine, etichetta)


def _data(valore: Any) -> date | None:
    testo = str(valore or "").strip()
    if not testo:
        return None
    try:
        return date.fromisoformat(testo[:10])
    except ValueError:
        return None
