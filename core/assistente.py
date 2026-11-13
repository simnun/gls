"""Assistente in linguaggio naturale, senza modelli esterni.

Il monitor parla di poche cose: spedizioni, giacenze, azioni degli operatori,
svincoli. Su un dominio cosi' chiuso una domanda non va capita "in generale":
va ricondotta a quattro informazioni (che cosa conto, in che periodo, con quali
filtri, come lo raggruppo). Quello che segue estrae queste quattro cose dalla
frase scritta dall'operatore. Non e' un modello di linguaggio: gira dentro il
server, non costa niente e non manda fuori nessun dato.

Tre regole di fondo:

1. I numeri non li scrive l'interprete, li calcola il database. La frase di
   risposta e' solo la cornice attorno a un conteggio SQL.
2. Quello che non viene capito viene dichiarato, non indovinato: una parola
   rimasta fuori finisce in `non_capito` e si vede nella risposta.
3. I conteggi devono coincidere con quelli che si leggono nei tab del sito.
   Dove la dashboard usa una definizione (per esempio "da verificare" = pratica
   nuova con gravita' alta) qui si usa la stessa, anche quando se ne potrebbe
   scrivere una piu' elegante: un assistente che dice 40 mentre il tab dice 41
   non serve a niente.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

from .timezones import ROME

# Le stesse quattro categorie del tab "In consegna": il collo si sta muovendo.
CATEGORIE_IN_CONSEGNA = ("OUT_FOR_DELIVERY", "SCHEDULED", "IN_TRANSIT", "CORRESPONDENT")
IN_LAVORAZIONE = ("IN_PROGRESS", "WAITING_CUSTOMER", "WAITING_GLS")
# Le righe dietro a una risposta, per «Mostrami quali» e «Copia i tracking».
# Nessun tetto pratico: un anno di spedizioni deve starci tutto. Il valore
# esiste solo come protezione contro un'interrogazione impazzita.
LIMITE_RIGHE = 100000

# ---------------------------------------------------------------------------
# Vocabolario del dominio
# ---------------------------------------------------------------------------

# Frase -> categoria GLS. L'ordine conta: le frasi lunghe vanno provate prima
# delle corte, altrimenti "mancata consegna" viene mangiata da "consegna".
SINONIMI_CATEGORIA: tuple[tuple[str, str], ...] = (
    ("destinatario assente", "ABSENT"),
    ("nessuno in casa", "ABSENT"),
    ("indirizzo errato", "ADDRESS_ERROR"),
    ("indirizzo sbagliato", "ADDRESS_ERROR"),
    ("indirizzo insufficiente", "ADDRESS_ERROR"),
    ("mancata consegna", "DELIVERY_FAILURE"),
    ("consegna fallita", "DELIVERY_FAILURE"),
    ("tentativo fallito", "DELIVERY_FAILURE"),
    ("nuovo tentativo", "DELIVERY_RETRY"),
    ("secondo tentativo", "DELIVERY_RETRY"),
    ("zona disagiata", "SERVICE_AREA"),
    ("ritiro in sede", "PICKUP_AT_DEPOT"),
    ("ritiro in filiale", "PICKUP_AT_DEPOT"),
    ("destinatario chiuso", "RECIPIENT_CLOSED"),
    ("esercizio chiuso", "RECIPIENT_CLOSED"),
    ("etichetta creata", "LABEL_CREATED"),
    ("affidata a gls", "LABEL_CREATED"),
    ("in transito", "IN_TRANSIT"),
    ("in distribuzione", "OUT_FOR_DELIVERY"),
    ("su furgone", "OUT_FOR_DELIVERY"),
    ("corrispondente", "CORRESPONDENT"),
    ("danneggiata", "DAMAGE_OR_LOSS"),
    ("danneggiate", "DAMAGE_OR_LOSS"),
    ("smarrita", "DAMAGE_OR_LOSS"),
    ("smarrite", "DAMAGE_OR_LOSS"),
    ("rifiutata", "REFUSED"),
    ("rifiutate", "REFUSED"),
    ("respinta", "REFUSED"),
    ("respinte", "REFUSED"),
    ("consegnata", "DELIVERED"),
    ("consegnate", "DELIVERED"),
    ("consegnati", "DELIVERED"),
    ("consegnato", "DELIVERED"),
    ("consegne", "DELIVERED"),
    ("rientrate", "RETURN"),
    ("rientrati", "RETURN"),
    ("rientro", "RETURN"),
    ("rientri", "RETURN"),
    ("tornate indietro", "RETURN"),
    ("resi", "RETURN"),
    ("appuntamento", "SCHEDULED"),
    ("programmata", "SCHEDULED"),
    ("ritardo", "LINEHAUL_DELAY"),
    ("ritardi", "LINEHAUL_DELAY"),
    ("assente", "ABSENT"),
    ("assenti", "ABSENT"),
)

# Gruppi di categorie e viste: le stesse del sito, chiamate come le chiama
# l'operatore a voce.
GRUPPI: tuple[tuple[str, str], ...] = (
    ("in consegna", "IN_CONSEGNA"),
    ("da verificare", "DA_VERIFICARE"),
    ("da gestire", "DA_VERIFICARE"),
    ("in lavorazione", "IN_LAVORAZIONE"),
    ("prese in carico", "IN_LAVORAZIONE"),
    ("in osservazione", "OSSERVAZIONE"),
    ("sotto osservazione", "OSSERVAZIONE"),
    ("ancora aperte", "ATTIVE"),
    ("ancora attive", "ATTIVE"),
    ("attive", "ATTIVE"),
    ("aperte", "ATTIVE"),
    ("chiuse", "CHIUSE"),
    ("concluse", "CHIUSE"),
    ("risolte", "CHIUSE"),
)

SINONIMI_GRAVITA: tuple[tuple[str, str], ...] = (
    ("gravita massima", "CRITICAL"),
    ("priorita massima", "CRITICAL"),
    ("critiche", "CRITICAL"),
    ("critici", "CRITICAL"),
    ("urgenti", "CRITICAL"),
    ("gravita alta", "WARNING"),
    ("priorita alta", "WARNING"),
    ("informative", "INFO"),
    ("informativi", "INFO"),
)

SINONIMI_SOGGETTO: tuple[tuple[str, str], ...] = (
    ("richieste di svincolo", "SVINCOLI"),
    ("istruzioni di svincolo", "SVINCOLI"),
    ("svincoli", "SVINCOLI"),
    ("svincolo", "SVINCOLI"),
    ("svincolate", "SVINCOLI"),
    ("giacenze", "GIACENZE"),
    ("giacenza", "GIACENZE"),
    ("azioni", "AZIONI"),
    ("interventi", "AZIONI"),
    ("note registrate", "AZIONI"),
)

# Verbi che dicono "quante ne sono entrate in quel periodo", distinguendoli da
# "quante ce ne sono adesso".
VERBI_INGRESSO = ("andate", "andati", "entrate", "entrati", "finite", "finiti", "nuove", "nuovi", "aperte in")

NUMERI_A_PAROLE = {
    "un": 1, "uno": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5,
    "sei": 6, "sette": 7, "otto": 8, "nove": 9, "dieci": 10, "undici": 11,
    "dodici": 12, "quindici": 15, "venti": 20, "trenta": 30, "sessanta": 60,
}

MESI = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5,
    "giugno": 6, "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10,
    "novembre": 11, "dicembre": 12,
}

ETICHETTE_CATEGORIA = {
    "STORAGE": "in giacenza", "DELIVERED": "consegnate", "RETURN": "rientrate",
    "IN_TRANSIT": "in transito", "OUT_FOR_DELIVERY": "in distribuzione",
    "SCHEDULED": "con consegna programmata", "ABSENT": "destinatario assente",
    "REFUSED": "rifiutate", "ADDRESS_ERROR": "con indirizzo errato",
    "COD_ISSUE": "con problema di contrassegno", "DAMAGE_OR_LOSS": "danneggiate o smarrite",
    "DELIVERY_FAILURE": "con consegna fallita", "DELIVERY_RETRY": "con nuovo tentativo",
    "RECIPIENT_CLOSED": "con destinatario chiuso", "PICKUP_AT_DEPOT": "da ritirare in sede",
    "LINEHAUL_DELAY": "in ritardo", "CORRESPONDENT": "presso corrispondente",
    "SERVICE_AREA": "in zona disagiata", "LABEL_CREATED": "appena affidate a GLS",
    "ACTION_REQUIRED": "che richiedono un intervento", "UNCLASSIFIED": "ancora da classificare",
    "ORDER_CANCELLED": "con l\u2019ordine annullato", "REPLACED_CARRIER": "passate a un altro corriere",
}

ETICHETTE_GRUPPO = {
    "IN_CONSEGNA": "in consegna", "DA_VERIFICARE": "da verificare",
    "IN_LAVORAZIONE": "in lavorazione", "OSSERVAZIONE": "in osservazione",
    "ATTIVE": "ancora attive", "CHIUSE": "chiuse",
}

ETICHETTE_GRAVITA = {
    "CRITICAL": "priorita\u2019 massima", "WARNING": "priorita\u2019 alta",
    "WATCH": "in osservazione", "INFO": "informative", "NORMAL": "normali",
}


# ---------------------------------------------------------------------------
# Periodi
# ---------------------------------------------------------------------------


@dataclass
class Periodo:
    """Un intervallo di giorni, in ora italiana."""

    inizio: date | None
    fine: date | None
    etichetta: str

    def giorni(self) -> int:
        if not self.inizio or not self.fine:
            return 0
        return (self.fine - self.inizio).days + 1


def _numero(testo: str) -> int | None:
    testo = testo.strip()
    if testo.isdigit():
        return int(testo)
    return NUMERI_A_PAROLE.get(testo)


def _primo_del_mese(giorno: date) -> date:
    return giorno.replace(day=1)


def _ultimo_del_mese(giorno: date) -> date:
    if giorno.month == 12:
        return giorno.replace(day=31)
    return giorno.replace(month=giorno.month + 1, day=1) - timedelta(days=1)


def interpreta_periodo(testo: str, oggi: date) -> tuple[str, Periodo | None, bool]:
    """Estrae il periodo dalla frase e lo toglie dal testo.

    Ritorna il testo ripulito, il periodo e un flag che dice se la domanda
    chiede lo stato di adesso ("quanti sono in giacenza ora") invece che un
    conteggio su un intervallo.
    """
    adesso = False
    for frase in ("in questo momento", "attualmente", "al momento", "adesso", "ad oggi", "ora"):
        testo, trovato = _consuma(testo, frase)
        adesso = adesso or trovato

    # "dal 1/10 al 15/10" e "dal 2026-10-01 al 2026-10-15"
    intervallo = re.search(
        r"\bdal\s+(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{4}-\d{2}-\d{2})\s+al\s+(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{4}-\d{2}-\d{2})",
        testo,
    )
    if intervallo:
        a = _data_libera(intervallo.group(1), oggi)
        b = _data_libera(intervallo.group(2), oggi)
        if a and b:
            testo = testo[: intervallo.start()] + " " + testo[intervallo.end() :]
            if a > b:
                a, b = b, a
            return testo, Periodo(a, b, f"dal {a.strftime('%d/%m/%Y')} al {b.strftime('%d/%m/%Y')}"), adesso

    # "ultimi 7 giorni", "ultime tre settimane", "ultimi due mesi"
    ultimi = re.search(r"\bultim[aeio]\s+(\d{1,3}|[a-z]+)\s+(giorn[io]|settiman[ae]|mes[ei])\b", testo)
    if ultimi:
        quanti = _numero(ultimi.group(1))
        if quanti:
            unita = ultimi.group(2)
            giorni = quanti * (1 if unita.startswith("giorn") else 7 if unita.startswith("settiman") else 30)
            testo = testo[: ultimi.start()] + " " + testo[ultimi.end() :]
            inizio = oggi - timedelta(days=giorni - 1)
            return testo, Periodo(inizio, oggi, f"negli ultimi {quanti} {unita}"), adesso

    fisse: tuple[tuple[str, Periodo], ...] = (
        ("l altro ieri", Periodo(oggi - timedelta(days=2), oggi - timedelta(days=2), "l'altro ieri")),
        ("altro ieri", Periodo(oggi - timedelta(days=2), oggi - timedelta(days=2), "l'altro ieri")),
        ("oggi", Periodo(oggi, oggi, "oggi")),
        ("ieri", Periodo(oggi - timedelta(days=1), oggi - timedelta(days=1), "ieri")),
        ("questa settimana", Periodo(oggi - timedelta(days=oggi.weekday()), oggi, "questa settimana")),
        ("settimana scorsa", _settimana_scorsa(oggi)),
        ("la scorsa settimana", _settimana_scorsa(oggi)),
        ("ultima settimana", Periodo(oggi - timedelta(days=6), oggi, "negli ultimi 7 giorni")),
        ("questo mese", Periodo(_primo_del_mese(oggi), oggi, "questo mese")),
        ("mese scorso", _mese_scorso(oggi)),
        ("lo scorso mese", _mese_scorso(oggi)),
        ("ultimo mese", Periodo(oggi - timedelta(days=29), oggi, "negli ultimi 30 giorni")),
        ("quest anno", Periodo(date(oggi.year, 1, 1), oggi, "quest'anno")),
        ("anno scorso", Periodo(date(oggi.year - 1, 1, 1), date(oggi.year - 1, 12, 31), "l'anno scorso")),
    )
    for frase, periodo in fisse:
        testo, trovato = _consuma(testo, frase)
        if trovato:
            return testo, periodo, adesso

    # "a ottobre", "nel mese di marzo", "a ottobre 2025"
    mese = re.search(r"\b(?:a|ad|in|nel mese di|di)\s+(" + "|".join(MESI) + r")(?:\s+(\d{4}))?\b", testo)
    if mese:
        numero_mese = MESI[mese.group(1)]
        anno = int(mese.group(2)) if mese.group(2) else oggi.year
        if not mese.group(2) and numero_mese > oggi.month:
            anno -= 1  # "a dicembre" detto a ottobre e' il dicembre passato
        inizio = date(anno, numero_mese, 1)
        testo = testo[: mese.start()] + " " + testo[mese.end() :]
        return testo, Periodo(inizio, _ultimo_del_mese(inizio), f"a {mese.group(1)} {anno}"), adesso

    # "il 12/10" oppure "il 12 ottobre"
    giorno_singolo = re.search(r"\bil\s+(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", testo)
    if giorno_singolo:
        valore = _data_libera(giorno_singolo.group(0).replace("il ", ""), oggi)
        if valore:
            testo = testo[: giorno_singolo.start()] + " " + testo[giorno_singolo.end() :]
            return testo, Periodo(valore, valore, f"il {valore.strftime('%d/%m/%Y')}"), adesso
    giorno_esteso = re.search(r"\b(\d{1,2})\s+(" + "|".join(MESI) + r")(?:\s+(\d{4}))?\b", testo)
    if giorno_esteso:
        anno = int(giorno_esteso.group(3)) if giorno_esteso.group(3) else oggi.year
        try:
            valore = date(anno, MESI[giorno_esteso.group(2)], int(giorno_esteso.group(1)))
        except ValueError:
            valore = None
        if valore:
            testo = testo[: giorno_esteso.start()] + " " + testo[giorno_esteso.end() :]
            return testo, Periodo(valore, valore, f"il {valore.strftime('%d/%m/%Y')}"), adesso

    return testo, None, adesso


def _settimana_scorsa(oggi: date) -> Periodo:
    lunedi = oggi - timedelta(days=oggi.weekday() + 7)
    return Periodo(lunedi, lunedi + timedelta(days=6), "la settimana scorsa")


def _mese_scorso(oggi: date) -> Periodo:
    ultimo = _primo_del_mese(oggi) - timedelta(days=1)
    return Periodo(_primo_del_mese(ultimo), ultimo, "il mese scorso")


def _data_libera(testo: str, oggi: date) -> date | None:
    testo = testo.strip()
    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", testo)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    pezzi = re.match(r"^(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?$", testo)
    if not pezzi:
        return None
    anno = oggi.year
    if pezzi.group(3):
        anno = int(pezzi.group(3))
        if anno < 100:
            anno += 2000
    try:
        return date(anno, int(pezzi.group(2)), int(pezzi.group(1)))
    except ValueError:
        return None


def _consuma(testo: str, frase: str) -> tuple[str, bool]:
    """Toglie la frase dal testo, a confine di parola, e dice se c'era."""
    pattern = r"(?<![a-z0-9])" + re.escape(frase) + r"(?![a-z0-9])"
    nuovo, quante = re.subn(pattern, " ", testo)
    return nuovo, quante > 0


def _normalizza(testo: str) -> str:
    """Minuscole, niente accenti, apostrofi e punteggiatura diventano spazi.

    L'apostrofo va trasformato in spazio e non tolto: "l'operatore daniela"
    deve restare due parole, altrimenti il nome non si riconosce piu'.
    """
    testo = (testo or "").strip().lower()
    testo = testo.replace("’", "'").replace("`", "'")
    testo = unicodedata.normalize("NFKD", testo)
    testo = "".join(ch for ch in testo if not unicodedata.combining(ch))
    testo = re.sub(r"[^a-z0-9/\-]+", " ", testo)
    return re.sub(r"\s+", " ", testo).strip()


def _somiglianza(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# Lettura della domanda
# ---------------------------------------------------------------------------


@dataclass
class Lettura:
    """Come l'assistente ha capito la domanda. Viene mostrata all'operatore."""

    soggetto: str = "SPEDIZIONI"
    intento: str = "conta"
    periodo: Periodo | None = None
    adesso: bool = False
    categorie: list[str] = field(default_factory=list)
    gruppo: str = ""
    gravita: str = ""
    operatore: str = ""
    provincia: str = ""
    citta: str = ""
    stato_gls: str = ""
    testo_libero: str = ""
    solo_cod: bool = False
    tracking: str = ""
    ordine: str = ""
    raggruppa: str = ""
    data_su: str = "evento"
    percorso: bool = False
    gestite: str = ""  # "si" | "no" | ""
    non_capito: list[str] = field(default_factory=list)

    def chips(self) -> list[dict[str, str]]:
        """Le pastiglie che il sito mostra sotto la risposta."""
        voci: list[dict[str, str]] = []
        nomi = {"SPEDIZIONI": "spedizioni", "GIACENZE": "giacenze",
                "AZIONI": "azioni operatore", "SVINCOLI": "svincoli"}
        voci.append({"campo": "soggetto", "testo": nomi.get(self.soggetto, self.soggetto)})
        if self.periodo:
            su = {"AZIONI": "data azione", "SVINCOLI": "data svincolo",
                  "GIACENZE": "entrata in giacenza"}.get(
                      self.soggetto, "data ordine" if self.data_su == "ordine" else "data GLS")
            voci.append({"campo": "periodo", "testo": f"{self.periodo.etichetta} ({su})"})
        elif self.adesso:
            voci.append({"campo": "periodo", "testo": "adesso"})
        if self.gruppo:
            voci.append({"campo": "gruppo", "testo": ETICHETTE_GRUPPO.get(self.gruppo, self.gruppo)})
        for categoria in self.categorie:
            voci.append({"campo": "categoria", "testo": ETICHETTE_CATEGORIA.get(categoria, categoria)})
        if self.gravita:
            voci.append({"campo": "gravita", "testo": ETICHETTE_GRAVITA.get(self.gravita, self.gravita)})
        if self.operatore:
            voci.append({"campo": "operatore", "testo": f"operatore {self.operatore}"})
        if self.provincia:
            voci.append({"campo": "provincia", "testo": f"provincia {self.provincia}"})
        if self.citta:
            voci.append({"campo": "citta", "testo": self.citta})
        if self.stato_gls:
            voci.append({"campo": "stato", "testo": f"stato: {self.stato_gls}"})
        if self.testo_libero:
            voci.append({"campo": "testo", "testo": f"contiene: {self.testo_libero}"})
        if self.solo_cod:
            voci.append({"campo": "cod", "testo": "solo contrassegno"})
        if self.gestite == "si":
            voci.append({"campo": "gestite", "testo": "gia\u2019 gestite dal team"})
        if self.gestite == "no":
            voci.append({"campo": "gestite", "testo": "non ancora gestite"})
        if self.tracking:
            voci.append({"campo": "tracking", "testo": f"tracking {self.tracking}"})
        if self.ordine:
            voci.append({"campo": "ordine", "testo": f"ordine #{self.ordine}"})
        if self.raggruppa:
            voci.append({"campo": "raggruppa", "testo": f"per {self.raggruppa}"})
        return voci


@dataclass
class Vocabolario:
    """I valori che esistono davvero nel database, non quelli immaginabili."""

    operatori: list[str] = field(default_factory=list)
    province: list[str] = field(default_factory=list)
    citta: list[str] = field(default_factory=list)
    stati: list[str] = field(default_factory=list)


def interpreta(domanda: str, vocabolario: Vocabolario, oggi: date | None = None) -> Lettura:
    """Dalla frase alle quattro informazioni che servono per interrogare il db."""
    oggi = oggi or datetime.now(ROME).date()
    lettura = Lettura()
    testo = _normalizza(domanda)

    # 1. Numeri identificativi: se c'e' un tracking o un ordine la domanda e'
    #    su quella spedizione, non un conteggio.
    tracking = re.search(r"\b([a-z]{0,2}\d{8,14})\b", testo)
    if tracking:
        lettura.tracking = tracking.group(1).upper()
        lettura.intento = "dettaglio"
        testo = testo[: tracking.start()] + " " + testo[tracking.end() :]
    ordine = re.search(r"(?:ordine|#)\s*#?\s*(\d{3,10})", testo)
    if ordine:
        lettura.ordine = ordine.group(1)
        lettura.intento = "dettaglio"
        testo = testo[: ordine.start()] + " " + testo[ordine.end() :]

    # 2. Periodo, e su quale data va misurato: GLS o Shopify.
    if re.search(r"\bordinat[aeio]\b|\bdata ordine\b|\bordini (?:di|del|della)\b", testo):
        lettura.data_su = "ordine"
        for frase in ("ordinate", "ordinati", "ordinato", "ordinata", "data ordine"):
            testo, _ = _consuma(testo, frase)
    testo, lettura.periodo, lettura.adesso = interpreta_periodo(testo, oggi)

    # 3. Operatore: prima per nome intero, poi parola per parola con una
    #    tolleranza sulla scrittura ("danela", "daniella").
    testo = _trova_operatore(testo, vocabolario, lettura)

    # 4. Soggetto esplicito.
    for frase, soggetto in SINONIMI_SOGGETTO:
        testo, trovato = _consuma(testo, frase)
        if trovato:
            lettura.soggetto = soggetto
            if soggetto == "GIACENZE":
                lettura.categorie = [c for c in lettura.categorie if c != "STORAGE"]
            break

    # 5. Gruppi, gravita', categorie.
    for frase, gruppo in GRUPPI:
        testo, trovato = _consuma(testo, frase)
        if trovato:
            lettura.gruppo = gruppo
            break
    for frase, gravita in SINONIMI_GRAVITA:
        testo, trovato = _consuma(testo, frase)
        if trovato:
            lettura.gravita = gravita
            break
    # Il contrassegno va letto prima delle categorie, e in due tempi: prima il
    # problema ("importo non disponibile"), poi il metodo di pagamento. Senza
    # quest'ordine "quante in contrassegno a Napoli" verrebbe letta come una
    # domanda sulle anomalie di incasso.
    problema_cod = False
    for frase in ("problema di contrassegno", "problema del contrassegno",
                  "importo contrassegno", "importo del contrassegno",
                  "importo non disponibile"):
        testo, trovato = _consuma(testo, frase)
        if trovato:
            problema_cod = True
            lettura.categorie.append("COD_ISSUE")
            break
    if not problema_cod:
        for frase in ("in contrassegno", "con contrassegno", "contrassegno", "contrassegni", "cod"):
            testo, trovato = _consuma(testo, frase)
            if trovato:
                lettura.solo_cod = True
                break
    for frase, categoria in SINONIMI_CATEGORIA:
        testo, trovato = _consuma(testo, frase)
        if trovato and categoria not in lettura.categorie:
            lettura.categorie.append(categoria)

    # 6. Luoghi.
    testo = _trova_luogo(testo, vocabolario, lettura)

    # 7. Gia' gestite o no.
    for frase in ("non ancora gestite", "mai gestite", "non gestite", "senza intervento", "intatte"):
        testo, trovato = _consuma(testo, frase)
        if trovato:
            lettura.gestite = "no"
            break
    if not lettura.gestite:
        for frase in ("gia gestite", "gia lavorate", "gestite dal team"):
            testo, trovato = _consuma(testo, frase)
            if trovato:
                lettura.gestite = "si"
                break

    # 8. Stato GLS citato per esteso.
    testo = _trova_stato(testo, vocabolario, lettura)

    # 9. Intento e raggruppamento.
    testo = _trova_intento(testo, lettura)

    # 9bis. "Daniela ha gestito": la domanda non e' sulle spedizioni ma sul
    #       lavoro fatto. Va letta dopo il resto, perche' "poi gestite" nella
    #       domanda sul percorso delle giacenze non c'entra con un operatore.
    testo = _trova_lavoro(testo, lettura)

    # 10. "andate in giacenza" con un periodo vuol dire entrate in quel
    #     periodo; senza periodo vuol dire quelle aperte adesso.
    if "STORAGE" in lettura.categorie and lettura.soggetto == "SPEDIZIONI":
        lettura.soggetto = "GIACENZE"
        lettura.categorie.remove("STORAGE")
    if lettura.soggetto == "GIACENZE" and not lettura.periodo:
        lettura.adesso = True
    if lettura.percorso:
        # Sul percorso si risponde con tutta la catena (entrate, gestite,
        # consegnate, rientrate): un filtro per categoria la amputerebbe.
        lettura.categorie = []
        lettura.gestite = ""

    # 11. Quello che resta e che non e' una parola di servizio viene dichiarato.
    lettura.non_capito = _parole_rimaste(testo)
    return lettura


PAROLE_DI_SERVIZIO = {
    "quante", "quanti", "quanto", "quale", "quali", "che", "cosa", "come", "quando", "dove",
    "sono", "e", "ed", "ci", "c", "il", "lo", "la", "i", "gli", "le", "un", "uno", "una",
    "di", "a", "ad", "da", "in", "con", "su", "per", "tra", "fra", "del", "dello", "della",
    "dei", "degli", "delle", "al", "allo", "alla", "ai", "agli", "alle", "dal", "dalla",
    "nel", "nella", "nei", "negli", "nelle", "sul", "sulla", "mi", "me", "ho", "abbiamo",
    "stato", "stati", "state", "poi", "ancora", "gia", "non", "piu", "meno", "solo",
    "spedizione", "spedizioni", "collo", "colli", "pacco", "pacchi", "ordini", "pratiche",
    "pratica", "ha", "hanno", "fatto", "fatte", "totale", "totali", "numero", "operatore",
    "operatori", "team", "gls", "attuale", "attuali", "adesso", "risultano", "risulta",
    "dimmi", "dammi", "vorrei", "sapere", "elenco", "quelle", "quelli", "queste", "questi",
    "tutte", "tutti", "ne", "si", "no", "nostre", "nostri", "loro", "sua", "suo",
    "fine", "capitato", "successo", "andata", "andato",
    "andate", "andati", "entrate", "entrati", "finite", "finiti", "arrivate", "arrivati",
    "partite", "partiti", "passate", "passati", "diventate", "risultate", "rimaste",
    "gestito", "gestite", "gestiti", "lavorato", "lavorate", "registrato", "aperte", "aperti",
}


VERBI_LAVORO = (
    "ha preso in carico", "hanno preso in carico", "ha gestito", "hanno gestito",
    "ha lavorato", "hanno lavorato", "ha registrato", "hanno registrato",
    "ha chiuso", "hanno chiuso", "ha toccato", "ha fatto", "hanno fatto",
    "gestito", "gestite", "gestiti", "lavorato", "lavorate", "lavorati",
)


def _trova_lavoro(testo: str, lettura: Lettura) -> str:
    lavoro = False
    for frase in VERBI_LAVORO:
        testo, trovato = _consuma(testo, frase)
        lavoro = lavoro or trovato
    if not lavoro or lettura.intento == "dettaglio":
        return testo
    if lettura.soggetto == "SPEDIZIONI" and (lettura.operatore or lettura.raggruppa == "operatore"):
        lettura.soggetto = "AZIONI"
    elif not lettura.percorso and not lettura.gestite and lettura.soggetto in {"GIACENZE", "SPEDIZIONI"}:
        # "quante giacenze gestite" senza nome: sono quelle su cui il team e'
        # gia' intervenuto.
        lettura.gestite = "si"
    return testo


def _parole_rimaste(testo: str) -> list[str]:
    fuori = []
    for parola in testo.split():
        if len(parola) < 3 or parola in PAROLE_DI_SERVIZIO:
            continue
        if parola.isdigit():
            continue
        fuori.append(parola)
    return fuori[:6]


def _trova_operatore(testo: str, vocabolario: Vocabolario, lettura: Lettura) -> str:
    for nome in sorted(vocabolario.operatori, key=len, reverse=True):
        pulito = _normalizza(nome)
        if not pulito or len(pulito) < 3:
            continue
        nuovo, trovato = _consuma(testo, pulito)
        if trovato:
            lettura.operatore = nome
            return nuovo
    # Nome scritto storto: confronto parola per parola, ma solo su parole che
    # non fanno parte del lessico del monitor.
    parole = testo.split()
    for indice, parola in enumerate(parole):
        if len(parola) < 4 or parola in PAROLE_DI_SERVIZIO:
            continue
        for nome in vocabolario.operatori:
            pezzi = _normalizza(nome).split()
            if any(_somiglianza(parola, pezzo) >= 0.86 for pezzo in pezzi if len(pezzo) >= 4):
                lettura.operatore = nome
                parole[indice] = " "
                return " ".join(parole)
    return testo


def _trova_luogo(testo: str, vocabolario: Vocabolario, lettura: Lettura) -> str:
    sigla = re.search(r"\b(?:provincia|prov)\s+(?:di\s+)?([a-z]{2,})\b", testo)
    if sigla:
        candidato = sigla.group(1)
        for provincia in vocabolario.province:
            if _normalizza(provincia) == candidato:
                lettura.provincia = provincia
                return testo[: sigla.start()] + " " + testo[sigla.end() :]
        for citta in vocabolario.citta:
            if _normalizza(citta) == candidato:
                lettura.citta = citta
                return testo[: sigla.start()] + " " + testo[sigla.end() :]
    for citta in sorted(vocabolario.citta, key=len, reverse=True):
        pulito = _normalizza(citta)
        if len(pulito) < 4:
            continue
        nuovo, trovato = _consuma(testo, pulito)
        if trovato:
            lettura.citta = citta
            return nuovo
    return testo


def _trova_stato(testo: str, vocabolario: Vocabolario, lettura: Lettura) -> str:
    """Riconosce un pezzo di stato GLS citato nella domanda.

    Si accettano solo frasi lunghe: una parola sola ("consegna") comparirebbe
    in meta' degli stati e farebbe dire numeri sbagliati con sicurezza.
    """
    virgolette = re.search(r'"([^"]{3,})"', testo)
    if virgolette:
        lettura.testo_libero = virgolette.group(1).strip()
        return testo[: virgolette.start()] + " " + testo[virgolette.end() :]
    parole = [p for p in testo.split() if p]
    migliore = ""
    posizione: tuple[int, int] | None = None
    for lunghezza in range(min(8, len(parole)), 2, -1):
        for inizio in range(0, len(parole) - lunghezza + 1):
            frammento = " ".join(parole[inizio : inizio + lunghezza])
            if len(frammento) < 14:
                continue
            for stato in vocabolario.stati:
                if frammento in _normalizza(stato) and len(frammento) > len(migliore):
                    migliore = frammento
                    posizione = (inizio, inizio + lunghezza)
        if migliore:
            break
    if migliore and posizione:
        lettura.stato_gls = migliore
        parole[posizione[0] : posizione[1]] = [" "]
        return " ".join(parole)
    return testo


DIMENSIONI = (
    ("per provincia", "provincia"), ("quali province", "provincia"), ("province", "provincia"),
    ("per citta", "citta"), ("quali citta", "citta"),
    ("per operatore", "operatore"), ("ogni operatore", "operatore"), ("chi", "operatore"),
    ("per stato", "stato"), ("quali stati", "stato"),
    ("per categoria", "categoria"), ("per tipo", "categoria"), ("per motivo", "categoria"),
    ("giorno per giorno", "giorno"), ("per giorno", "giorno"), ("al giorno", "giorno"),
    ("per settimana", "settimana"), ("per mese", "mese"),
)


def _trova_intento(testo: str, lettura: Lettura) -> str:
    for frase, dimensione in DIMENSIONI:
        testo, trovato = _consuma(testo, frase)
        if trovato:
            lettura.raggruppa = dimensione
            lettura.intento = "raggruppa"
            break
    for frase in ("andamento", "trend", "come sta andando", "come stanno andando"):
        testo, trovato = _consuma(testo, frase)
        if trovato:
            lettura.intento = "raggruppa"
            lettura.raggruppa = lettura.raggruppa or "giorno"
            break
    if lettura.intento == "conta":
        for frase in ("elencami", "elenca", "mostrami", "mostra", "fammi vedere", "fammi un elenco", "lista", "quali sono"):
            testo, trovato = _consuma(testo, frase)
            if trovato:
                lettura.intento = "elenca"
                break
    # "poi gestite e consegnate": e' la domanda sul percorso delle giacenze.
    if lettura.soggetto == "GIACENZE" or "giacenz" in testo:
        for frase in ("poi", "e poi", "quante di queste", "percorso", "quante finiscono"):
            testo, trovato = _consuma(testo, frase)
            if trovato:
                lettura.percorso = True
                break
    return testo


# ---------------------------------------------------------------------------
# Dalla lettura alla query
# ---------------------------------------------------------------------------


def _confini_utc(periodo: Periodo) -> tuple[str, str]:
    """Estremi del periodo italiano, espressi in UTC come li scrive il monitor.

    Le colonne `created_at` sono scritte in UTC: per sapere che cosa e'
    successo "oggi" a Roma bisogna convertire i confini, non tagliare la
    stringa, altrimenti un'azione delle 23:30 finisce nel giorno dopo.
    """
    inizio = datetime.combine(periodo.inizio, time(0, 0), tzinfo=ROME)
    fine = datetime.combine(periodo.fine + timedelta(days=1), time(0, 0), tzinfo=ROME)
    return (
        inizio.astimezone(timezone.utc).isoformat(timespec="seconds"),
        fine.astimezone(timezone.utc).isoformat(timespec="seconds"),
    )


def _vincolo_periodo(colonna: str, periodo: Periodo, utc: bool) -> tuple[str, list[Any]]:
    if utc:
        da, a = _confini_utc(periodo)
        return f"({colonna} >= ? AND {colonna} < ?)", [da, a]
    # Colonne scritte in ora italiana: il confronto sulla data e' esatto.
    return (
        f"(substr({colonna},1,10) >= ? AND substr({colonna},1,10) <= ?)",
        [periodo.inizio.isoformat(), periodo.fine.isoformat()],
    )


def _filtri_spedizione(lettura: Lettura, alias: str = "s") -> tuple[list[str], list[Any]]:
    """Condizioni che valgono sulla riga spedizione, qualunque sia il soggetto."""
    dove: list[str] = []
    valori: list[Any] = []
    gruppo = lettura.gruppo
    if gruppo == "ATTIVE":
        dove.append(f"{alias}.closed=0")
    elif gruppo == "CHIUSE":
        dove.append(f"{alias}.closed=1")
    elif gruppo == "DA_VERIFICARE":
        # Stessa definizione del tab del sito: nuova e di gravita' alta.
        dove.append(f"{alias}.closed=0 AND {alias}.workflow_status='NEW'"
                    f" AND {alias}.severity IN ('CRITICAL','WARNING')")
    elif gruppo == "IN_LAVORAZIONE":
        segnaposti = ",".join("?" for _ in IN_LAVORAZIONE)
        dove.append(f"{alias}.closed=0 AND {alias}.workflow_status IN ({segnaposti})")
        valori.extend(IN_LAVORAZIONE)
    elif gruppo == "OSSERVAZIONE":
        dove.append(f"{alias}.closed=0 AND {alias}.severity='WATCH'")
    elif gruppo == "IN_CONSEGNA":
        segnaposti = ",".join("?" for _ in CATEGORIE_IN_CONSEGNA)
        dove.append(f"{alias}.closed=0 AND {alias}.category IN ({segnaposti})")
        valori.extend(CATEGORIE_IN_CONSEGNA)

    if lettura.categorie and lettura.soggetto != "GIACENZE":
        segnaposti = ",".join("?" for _ in lettura.categorie)
        dove.append(f"{alias}.category IN ({segnaposti})")
        valori.extend(lettura.categorie)
    if lettura.gravita:
        dove.append(f"{alias}.severity=?")
        valori.append(lettura.gravita)
    if lettura.provincia:
        dove.append(f"LOWER(COALESCE({alias}.province,''))=?")
        valori.append(lettura.provincia.lower())
    if lettura.citta:
        dove.append(f"LOWER(COALESCE({alias}.city,''))=?")
        valori.append(lettura.citta.lower())
    if lettura.solo_cod:
        dove.append(f"{alias}.is_cod=1")
    if lettura.stato_gls:
        dove.append(f"(LOWER(COALESCE({alias}.gls_status,'')) LIKE ?"
                    f" OR LOWER(COALESCE({alias}.gls_note,'')) LIKE ?)")
        valori.extend([f"%{lettura.stato_gls.lower()}%"] * 2)
    if lettura.testo_libero:
        campi = ("gls_status", "gls_note", "order_name", "customer_name", "city")
        pezzo = " OR ".join(f"LOWER(COALESCE({alias}.{c},'')) LIKE ?" for c in campi)
        dove.append(f"({pezzo})")
        valori.extend([f"%{lettura.testo_libero.lower()}%"] * len(campi))
    if lettura.tracking:
        dove.append(f"UPPER({alias}.tracking_number) LIKE ?")
        valori.append(f"%{lettura.tracking.upper()}%")
    if lettura.ordine:
        dove.append(f"REPLACE(COALESCE({alias}.order_name,''),'#','') = ?")
        valori.append(lettura.ordine)
    return dove, valori


COLONNE_RIGA = (
    "tracking_number", "order_name", "customer_name", "city", "province",
    "gls_status", "gls_event_at", "category", "severity", "workflow_status",
    "last_operator_name", "last_operator_action_at", "closed", "is_cod",
)


class Assistente:
    """Risponde a domande scritte, leggendo solo il database del monitor."""

    def __init__(self, db: Any, durata_cache: int = 300, collaboratore: Any = None):
        self.db = db
        self.durata_cache = durata_cache
        # Quando c'e', risponde lui: capisce la domanda, chiede al monitor i
        # numeri e li racconta a parole sue. Le regole qui sotto restano la
        # rete di sicurezza per quando non e' raggiungibile.
        self.collaboratore = collaboratore
        self._vocabolario: Vocabolario | None = None
        self._vocabolario_at: float = 0.0

    # -- vocabolario ------------------------------------------------------
    def vocabolario(self) -> Vocabolario:
        import time as _time

        adesso = _time.monotonic()
        if self._vocabolario and adesso - self._vocabolario_at < self.durata_cache:
            return self._vocabolario
        with self.db.connect() as conn:
            operatori = [r["nome"] for r in conn.execute(
                "SELECT DISTINCT operator_name AS nome FROM operator_actions"
                " WHERE COALESCE(operator_name,'')<>''"
            ).fetchall()]
            luoghi = conn.execute(
                "SELECT DISTINCT COALESCE(province,'') AS provincia, COALESCE(city,'') AS citta"
                " FROM shipments"
            ).fetchall()
            stati = [r["stato"] for r in conn.execute(
                "SELECT DISTINCT state AS stato FROM events WHERE COALESCE(state,'')<>''"
            ).fetchall()]
        province = sorted({(r["provincia"] or "").strip() for r in luoghi} - {""})
        citta = sorted({(r["citta"] or "").strip() for r in luoghi} - {""})
        self._vocabolario = Vocabolario(
            operatori=sorted({o.strip() for o in operatori if o and o.strip()}),
            province=province, citta=citta, stati=stati,
        )
        self._vocabolario_at = adesso
        return self._vocabolario

    # -- ingresso ---------------------------------------------------------
    def rispondi(self, domanda: str, oggi: date | None = None,
                 storia: list[dict[str, str]] | None = None) -> dict[str, Any]:
        domanda = (domanda or "").strip()
        if not domanda:
            return self._vuoto("Scrivimi una domanda: per esempio «quante sono in giacenza adesso?».")
        if len(domanda) > 400:
            domanda = domanda[:4000]
        oggi = oggi or datetime.now(ROME).date()
        # Prima il collega: e' lui che deve rispondere. Le regole intervengono
        # solo se non e' raggiungibile, e in quel caso si vede dalla risposta.
        if self.collaboratore is not None and self.collaboratore.configurato:
            esito = self.collaboratore.conversa(domanda, storia or [], self, oggi)
            if esito is not None:
                esito["domanda"] = domanda
                return esito
        ripiego = self.collaboratore is not None and self.collaboratore.configurato
        lettura = interpreta(domanda, self.vocabolario(), oggi)
        fonte = "regole"
        esito = self.calcola(lettura)
        if ripiego:
            esito["ripiego"] = True
        esito.update({
            "ok": True,
            "domanda": domanda,
            "interpretazione": {
                "chips": lettura.chips(),
                "non_capito": lettura.non_capito if fonte == "regole" else [],
                "fonte": fonte,
            },
        })
        if lettura.non_capito and fonte == "regole":
            esito["avviso"] = ("Non ho considerato queste parole: "
                               + ", ".join(lettura.non_capito)
                               + ". Se cambiano la domanda, riscrivila in altro modo.")
        esito.setdefault("suggerimenti", [])
        return esito

    def calcola(self, lettura: Lettura) -> dict[str, Any]:
        """Esegue la lettura e restituisce i numeri, senza decidere le parole."""
        if lettura.intento == "dettaglio":
            return self._dettaglio(lettura)
        if lettura.percorso:
            return self._percorso(lettura)
        if lettura.intento == "raggruppa":
            return self._raggruppa(lettura)
        return self._conta(lettura)

    @staticmethod
    def _vuoto(messaggio: str) -> dict[str, Any]:
        return {
            "ok": True, "risposta": messaggio, "numero": None, "righe": [],
            "totale_righe": 0, "interpretazione": {"chips": [], "non_capito": []},
            "suggerimenti": list(SUGGERIMENTI),
        }

    # -- esecuzione -------------------------------------------------------
    def _base(self, lettura: Lettura) -> tuple[str, list[str], list[Any], str, bool]:
        """Tabella di partenza, condizioni, valori, colonna data e se e' UTC."""
        dove, valori = _filtri_spedizione(lettura)
        if lettura.soggetto == "GIACENZE":
            da = ("FROM stock_cases sc"
                  " JOIN shipments s ON s.tracking_number=sc.tracking_number")
            colonna, utc = "sc.entered_at", False
            if lettura.categorie:
                segnaposti = ",".join("?" for _ in lettura.categorie)
                dove.append(f"UPPER(COALESCE(sc.outcome_category,'')) IN ({segnaposti})")
                valori.extend(lettura.categorie)
            if lettura.adesso and not lettura.periodo:
                dove.append("sc.status='OPEN'")
            if lettura.gestite:
                esiste = self._esiste_lavoro()
                dove.append(esiste if lettura.gestite == "si" else f"NOT {esiste}")
            if lettura.operatore:
                dove.append(
                    "EXISTS (SELECT 1 FROM operator_actions a"
                    " WHERE a.tracking_number=sc.tracking_number"
                    " AND LOWER(COALESCE(a.operator_name,''))=?)"
                )
                valori.append(lettura.operatore.lower())
            return da, dove, valori, colonna, utc
        if lettura.soggetto == "AZIONI":
            da = ("FROM operator_actions a"
                  " JOIN shipments s ON s.tracking_number=a.tracking_number")
            if lettura.operatore:
                dove.append("LOWER(COALESCE(a.operator_name,''))=?")
                valori.append(lettura.operatore.lower())
            return da, dove, valori, "a.created_at", True
        if lettura.soggetto == "SVINCOLI":
            da = ("FROM gls_release_requests r"
                  " JOIN shipments s ON s.tracking_number=r.tracking_number")
            if lettura.operatore:
                dove.append("LOWER(COALESCE(r.operator_name,''))=?")
                valori.append(lettura.operatore.lower())
            return da, dove, valori, "r.created_at", True
        # Spedizioni.
        if lettura.operatore:
            dove.append("LOWER(COALESCE(s.last_operator_name,''))=?")
            valori.append(lettura.operatore.lower())
        if lettura.gestite == "si":
            dove.append("COALESCE(s.last_operator_action,'')<>''")
        elif lettura.gestite == "no":
            dove.append("COALESCE(s.last_operator_action,'')=''")
        if lettura.adesso and not lettura.gruppo and not lettura.periodo:
            dove.append("s.closed=0")
        colonna = "s.order_created_at" if lettura.data_su == "ordine" else "s.gls_event_at"
        return "FROM shipments s", dove, valori, colonna, lettura.data_su == "ordine"

    @staticmethod
    def _esiste_lavoro() -> str:
        """Giacenza gia' lavorata: un'azione dell'operatore o uno svincolo.

        Stessa definizione usata dallo storico giacenze del sito, confronto fra
        date compreso, perche' i due numeri devono coincidere.
        """
        return (
            "(EXISTS (SELECT 1 FROM operator_actions a"
            " WHERE a.tracking_number=sc.tracking_number"
            " AND a.created_at>=COALESCE(sc.entered_at,sc.created_at)"
            " AND (sc.exited_at IS NULL OR a.created_at<=sc.exited_at))"
            " OR EXISTS (SELECT 1 FROM gls_release_requests r WHERE r.stock_case_id=sc.id))"
        )

    def _assembla(self, lettura: Lettura) -> tuple[str, str, list[Any]]:
        da, dove, valori, colonna, utc = self._base(lettura)
        if lettura.periodo:
            vincolo, extra = _vincolo_periodo(colonna, lettura.periodo, utc)
            dove.append(vincolo)
            valori.extend(extra)
        where = " AND ".join(f"({d})" for d in dove) if dove else "1=1"
        return da, where, valori, colonna

    # -- conteggio semplice ----------------------------------------------
    def _conta(self, lettura: Lettura) -> dict[str, Any]:
        da, where, valori, _ = self._assembla(lettura)
        with self.db.connect() as conn:
            if lettura.soggetto == "AZIONI":
                riga = conn.execute(
                    f"SELECT COUNT(*) AS azioni, COUNT(DISTINCT a.tracking_number) AS pratiche"
                    f" {da} WHERE {where}", valori
                ).fetchone()
                numero, pratiche = int(riga["azioni"] or 0), int(riga["pratiche"] or 0)
                extra = {"pratiche": pratiche}
            elif lettura.soggetto == "SVINCOLI":
                riga = conn.execute(
                    f"SELECT COUNT(*) AS totale,"
                    f" SUM(CASE WHEN r.gls_success=1 THEN 1 ELSE 0 END) AS riusciti"
                    f" {da} WHERE {where}", valori
                ).fetchone()
                numero = int(riga["totale"] or 0)
                extra = {"riusciti": int(riga["riusciti"] or 0)}
            else:
                chiave = "sc.id" if lettura.soggetto == "GIACENZE" else "s.tracking_number"
                riga = conn.execute(
                    f"SELECT COUNT(DISTINCT {chiave}) AS totale {da} WHERE {where}", valori
                ).fetchone()
                numero = int(riga["totale"] or 0)
                extra = {}
            righe = self._righe(conn, lettura, da, where, valori)
        risposta = _frase(lettura, numero, extra)
        return {"risposta": risposta, "numero": numero, "righe": righe,
                "totale_righe": numero, "dettagli": extra}

    def _righe(self, conn: Any, lettura: Lettura, da: str, where: str, valori: list[Any]) -> list[dict[str, Any]]:
        """Le spedizioni dietro al numero, per il pulsante «mostrami quali»."""
        colonne = ", ".join(f"s.{c}" for c in COLONNE_RIGA)
        ordine = "COALESCE(s.gls_event_at, s.last_seen_at) DESC"
        if lettura.soggetto == "GIACENZE":
            colonne += ", sc.entered_at, sc.status AS stock_status, sc.outcome_category"
            ordine = "COALESCE(sc.entered_at, sc.created_at) DESC"
        elif lettura.soggetto == "AZIONI":
            colonne += ", a.action_label, a.operator_name, a.created_at AS azione_at"
            ordine = "a.created_at DESC"
        elif lettura.soggetto == "SVINCOLI":
            colonne += ", r.release_label, r.gls_success, r.created_at AS svincolo_at"
            ordine = "r.created_at DESC"
        righe = conn.execute(
            f"SELECT {colonne} {da} WHERE {where} ORDER BY {ordine} LIMIT {LIMITE_RIGHE}", valori
        ).fetchall()
        fuori = []
        for riga in righe:
            voce = dict(riga)
            voce["closed"] = bool(voce.get("closed"))
            voce["is_cod"] = bool(voce.get("is_cod"))
            fuori.append(voce)
        return fuori

    # -- percorso delle giacenze -----------------------------------------
    def _percorso(self, lettura: Lettura) -> dict[str, Any]:
        """La catena completa: entrate, gestite, consegnate, rientrate.

        E' la risposta a "quante sono andate in giacenza, poi gestite e poi
        consegnate": un numero solo non direbbe dove si perdono.
        """
        lettura.soggetto = "GIACENZE"
        lettura.adesso = False
        da, where, valori, _ = self._assembla(lettura)
        lavoro = self._esiste_lavoro()
        with self.db.connect() as conn:
            riga = conn.execute(
                f"""SELECT COUNT(*) AS entrate,
                           SUM(CASE WHEN sc.status='OPEN' THEN 1 ELSE 0 END) AS aperte,
                           SUM(CASE WHEN {lavoro} THEN 1 ELSE 0 END) AS gestite,
                           SUM(CASE WHEN UPPER(COALESCE(sc.outcome_category,''))='DELIVERED' THEN 1 ELSE 0 END) AS consegnate,
                           SUM(CASE WHEN UPPER(COALESCE(sc.outcome_category,''))='DELIVERED' AND {lavoro} THEN 1 ELSE 0 END) AS gestite_consegnate,
                           SUM(CASE WHEN UPPER(COALESCE(sc.outcome_category,''))='RETURN' THEN 1 ELSE 0 END) AS rientrate
                    {da} WHERE {where}""", valori
            ).fetchone()
            dati = {k: int(riga[k] or 0) for k in
                    ("entrate", "aperte", "gestite", "consegnate", "gestite_consegnate", "rientrate")}
            righe = self._righe(conn, lettura, da, where, valori)
        quando = f" {lettura.periodo.etichetta}" if lettura.periodo else " in tutto"
        if not dati["entrate"]:
            testo = f"Non risultano giacenze{quando}."
        else:
            entrate = _accordo(dati["entrate"], "Sono entrate", "E\u2019 entrata")
            gestite = _accordo(dati["gestite"], "sono state gestite", "e\u2019 stata gestita")
            consegnate = _accordo(dati["gestite_consegnate"],
                                  "sono poi state consegnate", "e\u2019 poi stata consegnata")
            rientrate = _accordo(dati["rientrate"], "sono rientrate", "e\u2019 rientrata")
            nome = _plurale(dati["entrate"], "spedizione", "spedizioni")
            testo = (
                f"{entrate} in giacenza {dati['entrate']} {nome}{quando}. "
                f"Di queste, {dati['gestite']} {gestite} dal team, "
                f"{dati['gestite_consegnate']} {consegnate}"
                f" e {dati['rientrate']} {rientrate} al mittente."
            )
            if dati["aperte"]:
                aperte = _accordo(dati["aperte"], "sono ancora aperte", "e\u2019 ancora aperta")
                testo += f" {dati['aperte']} {aperte}."
        tabella = {
            "titolo": "Percorso delle giacenze",
            "colonne": ["Passaggio", "Spedizioni"],
            "righe": [
                ["Entrate in giacenza", dati["entrate"]],
                ["Gestite dal team", dati["gestite"]],
                ["Gestite e poi consegnate", dati["gestite_consegnate"]],
                ["Consegnate (in tutto)", dati["consegnate"]],
                ["Rientrate al mittente", dati["rientrate"]],
                ["Ancora aperte", dati["aperte"]],
            ],
        }
        return {"risposta": testo, "numero": dati["gestite_consegnate"], "righe": righe,
                "totale_righe": dati["entrate"], "tabella": tabella, "dettagli": dati}

    # -- raggruppamenti ---------------------------------------------------
    def _raggruppa(self, lettura: Lettura) -> dict[str, Any]:
        da, where, valori, colonna_data = self._assembla(lettura)
        dimensione = lettura.raggruppa or "categoria"
        chiave, titolo = self._espressione_gruppo(lettura, dimensione, colonna_data)
        conteggio = ("COUNT(*)" if lettura.soggetto in {"AZIONI", "SVINCOLI"}
                     else f"COUNT(DISTINCT {'sc.id' if lettura.soggetto == 'GIACENZE' else 's.tracking_number'})")
        temporale = dimensione in {"giorno", "settimana", "mese"}
        ordine = "chiave ASC" if temporale else "quante DESC, chiave ASC"
        with self.db.connect() as conn:
            righe = conn.execute(
                f"SELECT {chiave} AS chiave, {conteggio} AS quante {da} WHERE {where}"
                f" GROUP BY {chiave} ORDER BY {ordine}", valori
            ).fetchall()
            elenco = self._righe(conn, lettura, da, where, valori)
        voci = [(str(r["chiave"] or ""), int(r["quante"] or 0)) for r in righe]
        if dimensione == "settimana":
            voci = _per_settimana(voci)
        voci = [(self._etichetta_gruppo(dimensione, k), q) for k, q in voci]
        # Tutti i gruppi, non solo i primi: il totale deve tornare con il
        # conteggio semplice della stessa domanda.
        totale = sum(q for _, q in voci)
        if not voci:
            testo = "Non ho trovato niente con questi filtri."
        else:
            primo = voci[0]
            testo = (f"{totale} in totale, divise per {titolo.lower()}."
                     if temporale else
                     f"{totale} in totale. In testa {primo[0]} con {primo[1]}.")
        return {
            "risposta": testo, "numero": totale, "righe": elenco, "totale_righe": totale,
            "tabella": {"titolo": titolo, "colonne": [titolo, "Quante"],
                        "righe": [[k, q] for k, q in voci]},
        }

    @staticmethod
    def _espressione_gruppo(lettura: Lettura, dimensione: str, colonna_data: str) -> tuple[str, str]:
        if dimensione == "provincia":
            return "COALESCE(s.province,'')", "Provincia"
        if dimensione == "citta":
            return "COALESCE(s.city,'')", "Citta\u2019"
        if dimensione == "operatore":
            campo = "a.operator_name" if lettura.soggetto == "AZIONI" else (
                "r.operator_name" if lettura.soggetto == "SVINCOLI" else "s.last_operator_name")
            return f"COALESCE({campo},'')", "Operatore"
        if dimensione == "stato":
            return "COALESCE(s.gls_status,'')", "Stato GLS"
        if dimensione == "mese":
            return f"substr({colonna_data},1,7)", "Mese"
        if dimensione in {"giorno", "settimana"}:
            return f"substr({colonna_data},1,10)", "Giorno" if dimensione == "giorno" else "Settimana"
        return "COALESCE(s.category,'')", "Categoria"

    @staticmethod
    def _etichetta_gruppo(dimensione: str, chiave: str) -> str:
        if not chiave:
            return "non indicato"
        if dimensione == "categoria":
            return ETICHETTE_CATEGORIA.get(chiave, chiave).capitalize()
        if dimensione == "giorno":
            try:
                return date.fromisoformat(chiave).strftime("%d/%m/%Y")
            except ValueError:
                return chiave
        if dimensione == "mese":
            try:
                anno, mese = chiave.split("-")
                nome = [n for n, v in MESI.items() if v == int(mese)][0]
                return f"{nome} {anno}"
            except Exception:
                return chiave
        return chiave

    # -- dettaglio di una spedizione --------------------------------------
    def _dettaglio(self, lettura: Lettura) -> dict[str, Any]:
        dove, valori = _filtri_spedizione(lettura)
        where = " AND ".join(f"({d})" for d in dove) if dove else "1=0"
        colonne = ", ".join(f"s.{c}" for c in COLONNE_RIGA)
        with self.db.connect() as conn:
            righe = [dict(r) for r in conn.execute(
                f"SELECT {colonne}, s.gls_note, s.last_operator_action FROM shipments s"
                f" WHERE {where} ORDER BY COALESCE(s.gls_event_at,s.last_seen_at) DESC LIMIT 20",
                valori
            ).fetchall()]
        if not righe:
            quale = lettura.tracking or f"#{lettura.ordine}"
            return {"risposta": f"Non trovo nessuna spedizione per {quale}.",
                    "numero": 0, "righe": [], "totale_righe": 0}
        for voce in righe:
            voce["closed"] = bool(voce.get("closed"))
            voce["is_cod"] = bool(voce.get("is_cod"))
        prima = righe[0]
        pezzi = [f"{prima['tracking_number']} · ordine {prima.get('order_name') or '—'}"]
        if prima.get("customer_name"):
            pezzi.append(str(prima["customer_name"]))
        testa = " · ".join(pezzi)
        stato = (prima.get("gls_status") or "").strip() or "nessuno stato GLS registrato"
        quando = _data_breve(prima.get("gls_event_at"))
        testo = f"{testa}. Ultimo stato GLS{quando}: «{stato}»."
        if prima.get("last_operator_action"):
            chi = prima.get("last_operator_name") or "un operatore"
            testo += f" Ultima azione interna: {prima['last_operator_action']} ({chi})."
        if prima.get("closed"):
            testo += " La pratica e\u2019 chiusa."
        if len(righe) > 1:
            testo += f" Ci sono {len(righe)} spedizioni su questo ordine."
        return {"risposta": testo, "numero": len(righe), "righe": righe, "totale_righe": len(righe)}


def _per_settimana(voci: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Accorpa i giorni in settimane, etichettate con il lunedi'."""
    somme: dict[str, int] = {}
    for chiave, quante in voci:
        try:
            giorno = date.fromisoformat(chiave)
        except ValueError:
            continue
        lunedi = giorno - timedelta(days=giorno.weekday())
        somme[lunedi.isoformat()] = somme.get(lunedi.isoformat(), 0) + quante
    return [(f"settimana del {date.fromisoformat(k).strftime('%d/%m')}", v)
            for k, v in sorted(somme.items())]


def _data_breve(valore: str | None) -> str:
    if not valore:
        return ""
    try:
        momento = datetime.fromisoformat(str(valore).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return " del " + momento.astimezone(ROME).strftime("%d/%m/%Y alle %H:%M")


def _plurale(numero: int, singolare: str, plurale: str) -> str:
    return singolare if numero == 1 else plurale


def _accordo(numero: int, plurale: str, singolare: str) -> str:
    """Verbo al singolare quando il numero e' uno: «1 e' stata consegnata»."""
    return singolare if numero == 1 else plurale


def _qualificatori(lettura: Lettura) -> str:
    pezzi: list[str] = []
    if lettura.gruppo:
        pezzi.append(ETICHETTE_GRUPPO.get(lettura.gruppo, ""))
    for categoria in lettura.categorie:
        pezzi.append(ETICHETTE_CATEGORIA.get(categoria, categoria.lower()))
    if lettura.gravita:
        pezzi.append(f"con {ETICHETTE_GRAVITA.get(lettura.gravita, lettura.gravita)}")
    if lettura.solo_cod:
        pezzi.append("in contrassegno")
    if lettura.provincia:
        pezzi.append(f"in provincia di {lettura.provincia}")
    if lettura.citta:
        pezzi.append(f"a {lettura.citta}")
    if lettura.stato_gls:
        pezzi.append(f"con stato «{lettura.stato_gls}»")
    if lettura.testo_libero:
        pezzi.append(f"che citano «{lettura.testo_libero}»")
    if lettura.operatore and lettura.soggetto == "SPEDIZIONI":
        pezzi.append(f"lavorate da {lettura.operatore}")
    if lettura.gestite == "si":
        pezzi.append("gia\u2019 gestite dal team")
    elif lettura.gestite == "no":
        pezzi.append("non ancora gestite")
    return " ".join(p for p in pezzi if p)


def _frase(lettura: Lettura, numero: int, extra: dict[str, Any]) -> str:
    """La cornice attorno al numero. Il numero arriva sempre dal database."""
    quando = lettura.periodo.etichetta if lettura.periodo else ""
    coda = _qualificatori(lettura)

    if lettura.soggetto == "AZIONI":
        chi = lettura.operatore or "Il team"
        periodo = f" {quando}" if quando else ""
        if not numero:
            return f"{chi} non ha registrato nessuna azione{periodo}."
        pratiche = int(extra.get("pratiche") or 0)
        return (f"{chi}{periodo} ha registrato {numero} "
                f"{_plurale(numero, 'azione', 'azioni')} su {pratiche} "
                f"{_plurale(pratiche, 'spedizione', 'spedizioni')}.")

    if lettura.soggetto == "SVINCOLI":
        periodo = f" {quando}" if quando else ""
        if not numero:
            return f"Non risultano svincoli inviati a GLS{periodo}."
        riusciti = int(extra.get("riusciti") or 0)
        respinti = numero - riusciti
        testo = (f"{periodo.strip().capitalize() + ' sono' if periodo else 'Sono'} stati inviati "
                 f"{numero} {_plurale(numero, 'svincolo', 'svincoli')} a GLS")
        return testo + (f": {riusciti} accettati, {respinti} respinti." if respinti else
                        f", tutti accettati." if numero else ".")

    if lettura.soggetto == "GIACENZE":
        if not numero:
            return f"Non risultano giacenze{' ' + quando if quando else ''} {coda}".strip() + "."
        if quando:
            return (f"{quando.capitalize()} sono entrate in giacenza {numero} "
                    f"{_plurale(numero, 'spedizione', 'spedizioni')} {coda}").strip() + "."
        return (f"Adesso ci sono {numero} {_plurale(numero, 'spedizione', 'spedizioni')} "
                f"in giacenza {coda}").strip() + "."

    nome = _plurale(numero, "spedizione", "spedizioni")
    if not numero:
        return f"Non risultano spedizioni {coda} {quando}".strip() + "."
    if lettura.categorie == ["DELIVERED"] and quando:
        return f"{quando.capitalize()} sono state consegnate {numero} {nome}.".replace("  ", " ")
    if lettura.categorie == ["RETURN"] and quando:
        return f"{quando.capitalize()} sono rientrate {numero} {nome}.".replace("  ", " ")
    if quando:
        return f"{quando.capitalize()} risultano {numero} {nome} {coda}".strip() + "."
    return f"Ci sono {numero} {nome} {coda}".strip() + "."


def lettura_incerta(lettura: Lettura) -> bool:
    """Dice se la lettura a regole e' troppo debole per fidarsene.

    Due casi: sono rimaste fuori parole che potrebbero cambiare il senso della
    domanda, oppure dalla frase non e' stato estratto nessun filtro e si
    risponderebbe con un totale generico che nessuno ha chiesto. Fuori da
    questi due casi le regole bastano, e una domanda che costa zero e'
    preferibile a una che costa.
    """
    if lettura.non_capito:
        return True
    if lettura.intento == "dettaglio":
        return False
    significativo = any((
        lettura.categorie, lettura.gruppo, lettura.gravita, lettura.operatore,
        lettura.provincia, lettura.citta, lettura.stato_gls, lettura.testo_libero,
        lettura.solo_cod, lettura.gestite, lettura.raggruppa, lettura.percorso,
        lettura.periodo is not None, lettura.adesso,
        lettura.soggetto != "SPEDIZIONI",
    ))
    return not significativo


SUGGERIMENTI = (
    "Quante sono in giacenza adesso?",
    "Quante consegnate oggi?",
    "Quante sono andate in giacenza questo mese, poi gestite e consegnate?",
    "Quante ne ha gestite Daniela oggi?",
    "Quante da verificare adesso?",
    "Rientri del mese scorso per provincia",
)
