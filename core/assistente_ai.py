"""Il collega che risponde alle domande sul monitor.

Qui dentro non c'e' un traduttore di parole chiave: c'e' una conversazione.
L'operatore scrive come parlerebbe a un collega, Claude capisce, va a prendere
il dato e risponde a parole sue.

La regola che non cambia e' una sola, ed e' quella che rende il tutto
affidabile: **Claude non puo' inventare un numero**. Non ha accesso al
database; ha un solo strumento, `interroga_monitor`, con cui dichiara che cosa
vuole contare. Il conteggio lo esegue il monitor e glielo restituisce. Claude
mette le parole intorno a un numero che gli e' arrivato gia' fatto.

Un giro di domanda sono due chiamate: con la prima Claude dice che cosa
serve, con la seconda scrive la risposta avendo il dato in mano. E' lo stesso
che farebbe una persona: prima guarda, poi ti dice.

Se la chiave manca, il credito finisce o l'API non risponde, il monitor torna
alla lettura a regole e lo dichiara. Il sito non resta muto.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from .assistente import ETICHETTE_CATEGORIA, ETICHETTE_GRUPPO, Lettura, Periodo, Vocabolario
from .timezones import ROME

MODELLO_PREDEFINITO = "claude-opus-5-5"
MAX_TOKEN_USCITA = 1024
MAX_GIRI = 3
# Quante spedizioni di esempio accompagnano un conteggio: servono a far dire a
# Claude "per esempio l'ordine #278004", non a fargli rifare i conti.
MAX_ESEMPI = 6
# Prezzi per milione di token (entrata, uscita, lettura dalla cache): servono
# solo a mostrare la spesa stimata.
PREZZI = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}

CATEGORIE_VALIDE = sorted(ETICHETTE_CATEGORIA)
GRUPPI_VALIDI = sorted(ETICHETTE_GRUPPO)
RAGGRUPPAMENTI = ["", "provincia", "citta", "operatore", "stato", "categoria",
                  "giorno", "settimana", "mese"]

# Lo schema viaggia a ogni domanda e si paga a peso: qui restano solo i
# vincoli, cioe' i valori ammessi. Le spiegazioni stanno nelle istruzioni, che
# sono identiche ogni volta e quindi vengono lette dalla cache.
SCHEMA_STRUMENTO = {
    "type": "object",
    "properties": {
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
        "soggetto", "intento", "percorso", "adesso", "periodo_da", "periodo_a",
        "periodo_etichetta", "data_su", "categorie", "gruppo", "gravita",
        "operatore", "provincia", "citta", "stato_gls", "testo_libero",
        "solo_cod", "gestite", "raggruppa", "tracking", "ordine",
    ],
    "additionalProperties": False,
}

STRUMENTO = {
    "name": "interroga_monitor",
    "description": (
        "Conta o elenca quello che c'e' nel monitor. Dichiara che cosa vuoi sapere e "
        "ricevi i numeri calcolati sul database. Usalo ogni volta che la risposta "
        "contiene anche un solo dato: non ci sono altri modi per saperlo."
    ),
    "strict": True,
    "input_schema": SCHEMA_STRUMENTO,
}

ISTRUZIONI = """Sei il collega che in questo negozio online segue le spedizioni GLS: giacenze, consegne mancate, rientri, svincoli. Conosci il lavoro e rispondi a chi te lo chiede dall'ufficio, in italiano, come si parla fra colleghi.

Hai davanti il monitor delle spedizioni e un solo modo per leggerlo: lo strumento interroga_monitor. Tu non sai nulla a memoria.

REGOLA ASSOLUTA: non dire mai un numero che non ti sia stato restituito dallo strumento. Se per rispondere serve anche un solo dato, chiama lo strumento. Quando ti arrivano i numeri, riportali esattamente: non arrotondare, non sommare a mente, non stimare.

Come rispondere:
- Parla come una persona, non come un modulo. Due o tre frasi, al massimo, salvo che i dati chiedano di piu'.
- Niente preamboli ("Certo", "Ecco a te"), niente elenchi dei filtri che hai usato, niente codici interni: si dice "in giacenza", non STORAGE; "consegnate", non DELIVERED.
- Il numero va detto chiaro e subito, poi il contesto.
- Se nei dati c'e' qualcosa che un collega farebbe notare (molte giacenze ancora non gestite, un numero molto diverso dal solito, una provincia che spicca), dillo in una riga. Senza allarmismi e senza inventare cause.
- Se la domanda e' ambigua in un modo che cambia la risposta, chiedi la precisazione invece di tirare a indovinare.
- Se il monitor non ha quel dato (per esempio i costi di spedizione, o il perche' di un andamento), dillo in una riga e spiega cosa manca. Non girarci intorno.
- Se ti scrivono per salutare o per una cosa fuori tema, rispondi normalmente senza chiamare lo strumento.

Come compilare interroga_monitor:
- soggetto: SPEDIZIONI (i colli e il loro ultimo stato), GIACENZE (ogni volta che un collo si e' fermato in sede GLS in attesa di istruzioni, con data di entrata ed esito), AZIONI (quello che registrano gli operatori), SVINCOLI (le istruzioni di riconsegna mandate a GLS).
- intento: conta un numero, elenca le righe, raggruppa per una dimensione, dettaglio di una spedizione precisa.
- adesso=true per la situazione di questo momento; in quel caso lascia il periodo vuoto.
- periodo_da e periodo_a sono date AAAA-MM-GG; periodo_etichetta e' come lo chiameresti parlando.
- percorso=true solo per le domande su che fine hanno fatto le giacenze: entrate, gestite, poi consegnate o rientrate.
- data_su="ordine" solo se la domanda parla della data dell'ordine; altrimenti "evento".
- solo_cod riguarda il pagamento in contrassegno; se invece il problema e' l'importo, usa la categoria COD_ISSUE.
- operatore, provincia e citta': soltanto valori presenti negli elenchi qui sotto, altrimenti lascia vuoto.
- gestite: "si" se il team e' gia' intervenuto, "no" se la pratica e' intatta.
- Lascia vuoto tutto cio' che la domanda non chiede: ogni filtro in piu' e' una risposta piu' stretta del dovuto.

Nei risultati non ci sono i dati personali dei clienti: nome, email, telefono e indirizzo restano sul monitor e non ti vengono passati. Se servono, di' all'operatore di aprire l'elenco."""


@dataclass
class Consumo:
    chiamate: int = 0
    conversazioni: int = 0
    token_entrata: int = 0
    token_cache: int = 0
    token_uscita: int = 0
    errori: int = 0
    giorno: str = ""

    def costo(self, modello: str) -> float:
        entrata, uscita, cache = PREZZI.get(modello, PREZZI[MODELLO_PREDEFINITO])
        return (self.token_entrata * entrata / 1_000_000
                + self.token_cache * cache / 1_000_000
                + self.token_uscita * uscita / 1_000_000)


class Collaboratore:
    """Tiene la conversazione e lascia i conti al monitor."""

    def __init__(self, api_key: str | None = None, modello: str | None = None,
                 max_al_giorno: int = 0):
        self.api_key = (api_key if api_key is not None
                        else os.environ.get("ANTHROPIC_API_KEY", "")).strip()
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

    @property
    def configurato(self) -> bool:
        return bool(self.api_key)

    def stato(self) -> dict[str, Any]:
        return {
            "attivo": self.configurato,
            "modello": self.modello,
            "conversazioni_oggi": self.consumo.conversazioni,
            "chiamate_oggi": self.consumo.chiamate,
            "tetto_giornaliero": self.max_al_giorno,
            "costo_stimato_oggi": round(self.consumo.costo(self.modello), 4),
            "errori": self.consumo.errori,
            "ultimo_errore": self.ultimo_errore[:200],
        }

    def _entro_il_tetto(self, oggi: date) -> bool:
        if self.consumo.giorno != oggi.isoformat():
            self.consumo = Consumo(giorno=oggi.isoformat())
        return self.consumo.conversazioni < self.max_al_giorno

    def _ottieni_cliente(self) -> Any:
        """L'SDK viene importato alla prima domanda, non all'avvio del sito."""
        if self._cliente is not None:
            return self._cliente
        with self._lock:
            if self._cliente is None:
                import anthropic

                self._cliente = anthropic.Anthropic(api_key=self.api_key, max_retries=1, timeout=40.0)
        return self._cliente

    # -- conversazione ----------------------------------------------------
    def conversa(
        self,
        domanda: str,
        storia: list[dict[str, str]],
        assistente: Any,
        oggi: date | None = None,
    ) -> dict[str, Any] | None:
        """Porta avanti il botta e risposta. None se non si e' potuto fare."""
        oggi = oggi or datetime.now(ROME).date()
        if not self.configurato or not self._entro_il_tetto(oggi):
            return None
        try:
            cliente = self._ottieni_cliente()
        except Exception as exc:
            self.ultimo_errore = f"{type(exc).__name__}: {exc}"
            self.consumo.errori += 1
            return None

        vocabolario = assistente.vocabolario()
        messaggi = _storia_pulita(storia) + [{"role": "user", "content": domanda[:400]}]
        istruzioni = self._istruzioni(vocabolario, oggi)
        self.consumo.conversazioni += 1

        ultimo_calcolo: dict[str, Any] | None = None
        for _ in range(MAX_GIRI):
            try:
                risposta = cliente.messages.create(
                    model=self.modello,
                    max_tokens=MAX_TOKEN_USCITA,
                    system=[{"type": "text", "text": istruzioni,
                             "cache_control": {"type": "ephemeral"}}],
                    tools=[STRUMENTO],
                    messages=messaggi,
                    output_config={"effort": "low"},
                )
            except Exception as exc:
                self.ultimo_errore = f"{type(exc).__name__}: {exc}"
                self.consumo.errori += 1
                return None
            self._registra(risposta)
            if getattr(risposta, "stop_reason", "") == "refusal":
                return None
            if risposta.stop_reason != "tool_use":
                return self._confeziona(risposta, ultimo_calcolo)

            messaggi.append({"role": "assistant", "content": risposta.content})
            esiti = []
            for blocco in risposta.content:
                if getattr(blocco, "type", "") != "tool_use":
                    continue
                lettura = costruisci_lettura(blocco.input or {}, vocabolario, oggi)
                try:
                    calcolo = assistente.calcola(lettura)
                except Exception as exc:
                    self.ultimo_errore = f"{type(exc).__name__}: {exc}"
                    esiti.append({"type": "tool_result", "tool_use_id": blocco.id,
                                  "is_error": True,
                                  "content": "Il monitor non e' riuscito a eseguire la ricerca."})
                    continue
                ultimo_calcolo = calcolo
                esiti.append({"type": "tool_result", "tool_use_id": blocco.id,
                              "content": json.dumps(_risultato(calcolo, lettura), ensure_ascii=False)})
            messaggi.append({"role": "user", "content": esiti})

        # Troppi giri: meglio dirlo che restare in silenzio.
        return None

    def _confeziona(self, risposta: Any, calcolo: dict[str, Any] | None) -> dict[str, Any]:
        testo = "\n\n".join(b.text.strip() for b in risposta.content
                            if getattr(b, "type", "") == "text" and b.text.strip())
        calcolo = calcolo or {}
        return {
            "ok": True,
            "risposta": testo or "Non sono riuscito a formulare la risposta.",
            "numero": calcolo.get("numero"),
            "righe": calcolo.get("righe", []),
            "totale_righe": calcolo.get("totale_righe", 0),
            "tabella": calcolo.get("tabella"),
            "dettagli": calcolo.get("dettagli"),
            "fonte": "claude",
        }

    def _registra(self, risposta: Any) -> None:
        self.consumo.chiamate += 1
        uso = getattr(risposta, "usage", None)
        if not uso:
            return
        self.consumo.token_entrata += int(getattr(uso, "input_tokens", 0) or 0)
        self.consumo.token_entrata += int(getattr(uso, "cache_creation_input_tokens", 0) or 0)
        self.consumo.token_uscita += int(getattr(uso, "output_tokens", 0) or 0)
        self.consumo.token_cache += int(getattr(uso, "cache_read_input_tokens", 0) or 0)

    def _istruzioni(self, vocabolario: Vocabolario, oggi: date) -> str:
        pezzi = [ISTRUZIONI, "", f"Oggi e' {oggi.isoformat()} ({_giorno_italiano(oggi)})."]
        if vocabolario.operatori:
            pezzi.append("Operatori: " + ", ".join(sorted(vocabolario.operatori)) + ".")
        if vocabolario.province:
            pezzi.append("Province presenti: " + ", ".join(sorted(vocabolario.province)) + ".")
        pezzi.append("Categorie: " + ", ".join(
            f"{c} ({ETICHETTE_CATEGORIA[c]})" for c in CATEGORIE_VALIDE) + ".")
        return "\n".join(pezzi)


GIORNI = ("lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica")


def _giorno_italiano(giorno: date) -> str:
    return GIORNI[giorno.weekday()]


def _storia_pulita(storia: list[dict[str, str]] | None) -> list[dict[str, str]]:
    """Gli ultimi scambi, come solo testo.

    Bastano a reggere un "e ieri?" senza trascinarsi dietro i risultati degli
    strumenti dei giri precedenti, che costerebbero e non servono.
    """
    messaggi: list[dict[str, str]] = []
    for voce in (storia or [])[-8:]:
        ruolo = "assistant" if str(voce.get("ruolo")) == "assistente" else "user"
        testo = str(voce.get("testo") or "").strip()[:600]
        if testo:
            messaggi.append({"role": ruolo, "content": testo})
    while messaggi and messaggi[0]["role"] != "user":
        messaggi.pop(0)
    return messaggi


def _risultato(calcolo: dict[str, Any], lettura: Lettura) -> dict[str, Any]:
    """Quello che torna a Claude: numeri e stati, mai dati dei clienti."""
    fuori: dict[str, Any] = {
        "conteggio": calcolo.get("numero"),
        "totale_trovate": calcolo.get("totale_righe", 0),
        "ho_cercato": _riassunto_filtri(lettura),
    }
    if calcolo.get("dettagli"):
        fuori["dettagli"] = calcolo["dettagli"]
    tabella = calcolo.get("tabella")
    if tabella and tabella.get("righe"):
        fuori["ripartizione"] = {
            "per": tabella.get("titolo"),
            "voci": [[str(r[0]), r[1]] for r in tabella["righe"][:25]],
        }
    esempi = []
    for riga in (calcolo.get("righe") or [])[:MAX_ESEMPI]:
        esempi.append({
            "ordine": riga.get("order_name"),
            "tracking": riga.get("tracking_number"),
            "dove": " ".join(x for x in (riga.get("city"), riga.get("province")) if x),
            "stato": riga.get("gls_status"),
            "quando": str(riga.get("gls_event_at") or "")[:10],
            "lavorazione": riga.get("workflow_status"),
            "operatore": riga.get("last_operator_name") or "",
        })
    if esempi:
        fuori["esempi"] = esempi
    return fuori


def _riassunto_filtri(lettura: Lettura) -> dict[str, Any]:
    nomi = {"SPEDIZIONI": "spedizioni", "GIACENZE": "giacenze",
            "AZIONI": "azioni degli operatori", "SVINCOLI": "svincoli"}
    riassunto: dict[str, Any] = {"cosa": nomi.get(lettura.soggetto, lettura.soggetto)}
    if lettura.periodo:
        riassunto["quando"] = lettura.periodo.etichetta
    elif lettura.adesso:
        riassunto["quando"] = "adesso"
    if lettura.categorie:
        riassunto["categorie"] = [ETICHETTE_CATEGORIA.get(c, c) for c in lettura.categorie]
    if lettura.gruppo:
        riassunto["stato_pratica"] = ETICHETTE_GRUPPO.get(lettura.gruppo, lettura.gruppo)
    for campo in ("operatore", "provincia", "citta", "stato_gls", "gestite"):
        valore = getattr(lettura, campo)
        if valore:
            riassunto[campo] = valore
    if lettura.solo_cod:
        riassunto["pagamento"] = "contrassegno"
    return riassunto


def costruisci_lettura(dati: dict[str, Any], vocabolario: Vocabolario, oggi: date) -> Lettura:
    """Dal JSON dello strumento ai filtri, scartando cio' che non esiste.

    Lo schema vincola Claude ai valori previsti per categorie, gruppi e
    raggruppamenti, ma nomi di persone e di citta' restano testo libero:
    vengono accettati solo se corrispondono a un valore presente nel database.
    Meglio un filtro in meno che un filtro inventato.
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
        lettura.operatore = next((o for o in vocabolario.operatori if o.lower() == nome.lower()), "")
    sigla = str(dati.get("provincia") or "").strip()
    if sigla:
        lettura.provincia = next((p for p in vocabolario.province if p.lower() == sigla.lower()), "")
    citta = str(dati.get("citta") or "").strip()
    if citta:
        lettura.citta = next((c for c in vocabolario.citta if c.lower() == citta.lower()), "")

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
