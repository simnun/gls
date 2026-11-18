"""Il file delle spedizioni che manda il service del corriere, letto dal monitor.

Ogni riga del file e' una spedizione fatturata. Il monitor la riconosce dal
numero di spedizione, dice com'e' andata (consegnata, in giacenza, resa...)
e segnala quello che in contabilita' va guardato: la stessa spedizione
fatturata due volte, il contrassegno addebitato su un ordine che non lo era,
la spedizione di un ordine annullato.

Le colonne del file non hanno una posizione fissa: si riconoscono dal nome,
cosi' un service che aggiunge o sposta una colonna non rompe niente.
"""
from __future__ import annotations

import csv
import io
import os
import re
import unicodedata
from datetime import date, datetime
from typing import Any

# Esiti, nell'ordine in cui compaiono nel riepilogo, con il colore della riga.
ESITI = {
    "consegnata": {"nome": "Consegnata", "colore": "FFC6EFCE"},
    "giacenza": {"nome": "Giacenza", "colore": "FFFFEB9C"},
    "resa": {"nome": "Resa al mittente", "colore": "FFFFC7CE"},
    "problema": {"nome": "Con un problema in corso", "colore": "FFF8CBAD"},
    "viaggio": {"nome": "In viaggio", "colore": "FFDDEBF7"},
    "annullato": {"nome": "Ordine annullato", "colore": "FFE4DFEC"},
    "sconosciuta": {"nome": "Non trovata", "colore": "FFEDEDED"},
}
PROBLEMI = {"ADDRESS_ERROR", "ABSENT", "REFUSED", "COD_ISSUE", "DELIVERY_FAILURE", "DAMAGE_OR_LOSS",
            "RECIPIENT_CLOSED", "PICKUP_AT_DEPOT", "ACTION_REQUIRED", "DELIVERY_RETRY", "UNCLASSIFIED"}
# Il monitor segue gli ordini di un negozio Shopify; le spedizioni dell'altro
# negozio, che viaggiano con lo stesso contratto GLS, si tracciano a parte.
NEGOZIO = os.environ.get("NEGOZIO_NOME", "Zuiki").strip() or "Zuiki"
ALTRO_NEGOZIO = os.environ.get("ALTRO_NEGOZIO_NOME", "LoveSkin").strip() or "LoveSkin"

COLONNE_AGGIUNTE = ["Esito monitor", "Negozio", "Dettaglio", "Ordine Shopify", "Stato GLS", "Ultimo evento GLS",
                    "Giacenze", "Pagamento ordine", "Controlli"]


class FileNonLeggibile(ValueError):
    """Il file non e' nel formato atteso: il messaggio va mostrato cosi' com'e'."""


def _norma(testo: Any) -> str:
    testo = unicodedata.normalize("NFKD", str(testo or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", testo.lower()).strip()


def _cifre(valore: Any) -> str:
    if isinstance(valore, float) and valore.is_integer():
        valore = int(valore)
    return re.sub(r"\D", "", str(valore or ""))


def importo(valore: Any) -> float | None:
    """'€ 3,90', '3.90', 3.9, '1.234,50 €' -> numero; None se non e' un importo."""
    if isinstance(valore, bool) or valore is None:
        return None
    if isinstance(valore, (int, float)):
        return float(valore)
    testo = str(valore).replace("€", "").replace("EUR", "").replace("\xa0", "").replace(" ", "").strip()
    if not testo or not re.fullmatch(r"-?[\d.,]+", testo):
        return None
    if "," in testo:
        testo = testo.replace(".", "").replace(",", ".")
    elif testo.count(".") > 1:
        testo = testo.replace(".", "")
    try:
        return float(testo)
    except ValueError:
        return None


# ---------------------------------------------------------------- lettura


def leggi_file(nome: str, contenuto: bytes, avanzamento: Any = None) -> tuple[list[str], list[list[Any]]]:
    """Intestazioni e righe del primo foglio, da .xlsx, .xls o .csv.

    avanzamento(fatte, totale) viene chiamata mentre si leggono le righe.
    """
    avanzamento = avanzamento or (lambda fatte, totale: None)
    estensione = (nome or "").lower().rsplit(".", 1)[-1]
    if not contenuto:
        raise FileNonLeggibile("Il file è vuoto.")
    if estensione in {"xlsx", "xlsm"} or contenuto[:2] == b"PK":
        tabella = _leggi_xlsx(contenuto, avanzamento)
    elif estensione == "xls" or contenuto[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        tabella = _leggi_xls(contenuto, avanzamento)
    elif estensione in {"csv", "txt", ""}:
        tabella = _leggi_csv(contenuto, avanzamento)
    else:
        raise FileNonLeggibile("Formato non riconosciuto: carica il file in Excel (.xlsx o .xls) o in CSV.")
    return _separa_intestazione(tabella)


def _leggi_xlsx(contenuto: bytes, avanzamento: Any) -> list[list[Any]]:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover - dipende dall'installazione
        raise FileNonLeggibile("Lettura Excel non disponibile sul server.") from exc
    try:
        libro = openpyxl.load_workbook(io.BytesIO(contenuto), read_only=True, data_only=True)
    except Exception as exc:
        raise FileNonLeggibile("Il file Excel non si apre: prova a salvarlo di nuovo in .xlsx.") from exc
    try:
        foglio = libro.worksheets[0]
        totale = foglio.max_row or 0
        righe = []
        for riga in foglio.iter_rows(values_only=True):
            righe.append(list(riga))
            if len(righe) % 100 == 0:
                avanzamento(len(righe), max(totale, len(righe)))
        avanzamento(len(righe), len(righe))
        return righe
    finally:
        libro.close()


def _leggi_xls(contenuto: bytes, avanzamento: Any) -> list[list[Any]]:
    try:
        import xlrd
    except ImportError as exc:  # pragma: no cover
        raise FileNonLeggibile("Lettura dei vecchi file Excel non disponibile sul server.") from exc
    try:
        libro = xlrd.open_workbook(file_contents=contenuto)
    except Exception as exc:
        raise FileNonLeggibile("Il file Excel non si apre: prova a salvarlo in .xlsx.") from exc
    foglio = libro.sheet_by_index(0)
    righe = []
    for r in range(foglio.nrows):
        riga = []
        for c in range(foglio.ncols):
            cella = foglio.cell(r, c)
            if cella.ctype == xlrd.XL_CELL_DATE:
                try:
                    riga.append(xlrd.xldate_as_datetime(cella.value, libro.datemode))
                    continue
                except Exception:
                    pass
            riga.append(cella.value if cella.value != "" else None)
        righe.append(riga)
        if r % 100 == 0:
            avanzamento(r, foglio.nrows)
    avanzamento(foglio.nrows, foglio.nrows)
    return righe


def _leggi_csv(contenuto: bytes, avanzamento: Any) -> list[list[Any]]:
    for codifica in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            testo = contenuto.decode(codifica)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialetto = csv.Sniffer().sniff(testo[:5000], delimiters=";,\t|")
    except csv.Error:
        # Una sottoclasse, non csv.excel: cambiarne il separatore varrebbe
        # per tutto il programma.
        class dialetto(csv.excel):
            delimiter = ";" if testo.count(";") > testo.count(",") else ","
    righe = [[c if c != "" else None for c in riga] for riga in csv.reader(io.StringIO(testo), dialetto)]
    avanzamento(len(righe), len(righe))
    return righe


def _separa_intestazione(tabella: list[list[Any]]) -> tuple[list[str], list[list[Any]]]:
    """La riga delle intestazioni e' la prima che nomina la spedizione."""
    for indice, riga in enumerate(tabella[:40]):
        piene = sum(1 for c in riga if c not in (None, ""))
        if piene >= 3 and any(_e_colonna_spedizione(c) for c in riga):
            ultima = max(i for i, c in enumerate(riga) if c not in (None, "")) + 1
            intestazioni = [str(c).strip() if c not in (None, "") else f"Colonna {i + 1}"
                            for i, c in enumerate(riga[:ultima])]
            righe = []
            for dati in tabella[indice + 1:]:
                dati = list(dati[:ultima]) + [None] * (ultima - len(dati))
                if any(v not in (None, "") for v in dati):
                    righe.append(dati)
            return intestazioni, righe
    raise FileNonLeggibile("Non trovo la colonna con il numero di spedizione (per esempio \"Nr. Spedizione\").")


def _e_colonna_spedizione(intestazione: Any) -> bool:
    testo = _norma(intestazione)
    return bool(testo) and ("spedizion" in testo or "tracking" in testo or testo in {"nr", "numero"})


# ---------------------------------------------------------------- colonne


def riconosci_colonne(intestazioni: list[str], righe: list[list[Any]]) -> dict[str, Any]:
    """Quali colonne sono la spedizione, il servizio, il contrassegno, gli importi."""
    norme = [_norma(i) for i in intestazioni]
    spedizione = next(i for i, n in enumerate(norme) if _e_colonna_spedizione(intestazioni[i]))
    servizio = next((i for i, n in enumerate(norme) if "servizio" in n or "tipolog" in n), None)
    importi = []
    for i, nome in enumerate(norme):
        if i == spedizione or any(p in nome for p in ("cod", "id ", "data", "peso", "volum", "destinat", "client")):
            continue
        if nome in {"id", "id bda"}:
            continue
        valori = [r[i] for r in righe if r[i] not in (None, "")]
        if not valori:
            continue
        numeri = [importo(v) for v in valori]
        validi = [n for n in numeri if n is not None]
        con_euro = any("€" in str(v) for v in valori)
        decimali = any(isinstance(v, float) and not float(v).is_integer() for v in valori)
        if len(validi) >= 0.8 * len(valori) and (con_euro or decimali or any(
                p in nome for p in ("nolo", "contrassegn", "assicur", "sprinter", "fuel", "carbur", "diritt",
                                    "import", "costo", "spes", "supplement", "total", "tass"))):
            importi.append(i)
    contrassegno = next((i for i in importi if "contrassegn" in norme[i]), None)
    # Un valore assicurato o un contrassegno incassato non sono costi della spedizione.
    def e_costo(i: int) -> bool:
        # "Diritto assicurativo" e' un costo; "Valore assicurato" no.
        if "assicur" in norme[i] and any(p in norme[i] for p in ("valore", "importo assicurat")):
            return False
        if i == contrassegno:
            positivi = [importo(r[i]) or 0 for r in righe if (importo(r[i]) or 0) > 0]
            return not positivi or sum(1 for v in positivi if v <= 10) >= 0.9 * len(positivi)
        return True
    return {"spedizione": spedizione, "servizio": servizio, "importi": importi,
            "costi": [i for i in importi if e_costo(i)], "contrassegno": contrassegno}


MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
        "settembre", "ottobre", "novembre", "dicembre"]


def _data(valore: Any) -> date | None:
    if isinstance(valore, datetime):
        return valore.date()
    if isinstance(valore, date):
        return valore
    testo = str(valore or "").strip()[:10]
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(testo, formato).date()
        except ValueError:
            continue
    return None


def mese_del_file(intestazioni: list[str], righe: list[list[Any]]) -> str | None:
    """Il mese a cui si riferisce il file (AAAA-MM): quello piu' presente nelle date."""
    colonna = next((i for i, n in enumerate(intestazioni) if _norma(n).startswith("data")), None)
    if colonna is None:
        return None
    conteggio: dict[str, int] = {}
    for r in righe:
        giorno = _data(r[colonna])
        if giorno:
            chiave = f"{giorno.year:04d}-{giorno.month:02d}"
            conteggio[chiave] = conteggio.get(chiave, 0) + 1
    return max(conteggio, key=conteggio.get) if conteggio else None


def nome_mese(mese: str) -> str:
    """"2026-10" -> "Ottobre 2026"."""
    try:
        anno, numero = mese.split("-")
        return f"{MESI[int(numero) - 1].capitalize()} {anno}"
    except (ValueError, IndexError):
        return mese


# ---------------------------------------------------------------- analisi


def _esito(info: dict[str, Any] | None) -> tuple[str, str]:
    """Chiave dell'esito e una frase che lo spiega."""
    if not info:
        return "sconosciuta", "Non trovata né nel monitor né su GLS"
    categoria = str(info.get("category") or "").upper()
    giacenze = int(info.get("giacenze") or 0)
    if categoria == "RETURN":
        return "resa", "Rientrata al mittente" + (" dopo una giacenza" if giacenze else "")
    if int(info.get("giacenza_aperta") or 0) or categoria == "STORAGE":
        return "giacenza", "Ancora ferma in sede GLS"
    if categoria == "DELIVERED":
        # Verde anche dopo una giacenza: conta com'e' finita.
        return "consegnata", "Consegnata dopo una giacenza" if giacenze else "Consegnata"
    if str(info.get("order_cancelled_at") or "").strip():
        return "annullato", "Ordine annullato su Shopify"
    if categoria in PROBLEMI:
        return "problema", info.get("gls_status") or "Problema di consegna"
    return "viaggio", info.get("gls_status") or "In viaggio"


def negozio(info: dict[str, Any] | None) -> str:
    if not info:
        return ""
    return info.get("negozio") or NEGOZIO


def non_trovate(intestazioni: list[str], righe: list[list[Any]], monitor: dict[str, dict[str, Any]]) -> list[str]:
    """I numeri del file che il monitor non segue (le cifre, senza doppioni)."""
    i_sped = riconosci_colonne(intestazioni, righe)["spedizione"]
    note = set(monitor) | {k[-9:] for k in monitor if len(k) > 9}
    fuori = []
    for r in righe:
        cifre = _cifre(r[i_sped])
        if cifre and cifre not in note and cifre[-9:] not in note:
            fuori.append(cifre)
    return list(dict.fromkeys(fuori))


def analizza(intestazioni: list[str], righe: list[list[Any]],
             monitor: dict[str, dict[str, Any]], avanzamento: Any = None) -> dict[str, Any]:
    """Riga per riga: com'e' andata, colore, controlli; piu' il riepilogo."""
    avanzamento = avanzamento or (lambda fatte, totale: None)
    colonne = riconosci_colonne(intestazioni, righe)
    i_sped, i_serv, i_cod = colonne["spedizione"], colonne["servizio"], colonne["contrassegno"]
    per_cifre = {}
    for chiave, info in monitor.items():
        per_cifre.setdefault(chiave, info)
        if len(chiave) > 9:
            per_cifre.setdefault(chiave[-9:], info)

    def trova(valore: Any) -> dict[str, Any] | None:
        cifre = _cifre(valore)
        if not cifre:
            return None
        return per_cifre.get(cifre) or per_cifre.get(cifre[-9:])

    # La stessa spedizione con lo stesso servizio fatturata piu' volte.
    conteggio: dict[tuple[str, str], int] = {}
    for r in righe:
        chiave = (_cifre(r[i_sped]), _norma(r[i_serv]) if i_serv is not None else "")
        if chiave[0]:
            conteggio[chiave] = conteggio.get(chiave, 0) + 1

    analizzate = []
    riepilogo_esiti = {k: {"righe": 0, "costo": 0.0} for k in ESITI}
    totali_colonne = {intestazioni[i]: 0.0 for i in colonne["importi"]}
    controlli_totali: dict[str, int] = {}
    spedizioni = set()
    negozi: dict[str, dict[str, Any]] = {}
    for r in righe:
        info = trova(r[i_sped])
        chiave_esito, dettaglio = _esito(info)
        costo = round(sum(importo(r[i]) or 0.0 for i in colonne["costi"]), 2)
        for i in colonne["importi"]:
            totali_colonne[intestazioni[i]] += importo(r[i]) or 0.0
        controlli = []
        cifre = _cifre(r[i_sped])
        if cifre:
            spedizioni.add(cifre)
        volte = conteggio.get((cifre, _norma(r[i_serv]) if i_serv is not None else ""), 0)
        if volte > 1:
            controlli.append(f"Fatturata {volte} volte nel file")
        # Per l'altro negozio il pagamento si sa solo quando GLS lo dice.
        if info and i_cod is not None and info.get("is_cod") is not None:
            addebito = importo(r[i_cod]) or 0.0
            if addebito > 0 and not info.get("is_cod"):
                controlli.append("Contrassegno in fattura, ma l’ordine non è in contrassegno")
            elif addebito == 0 and info.get("is_cod"):
                controlli.append("Ordine in contrassegno, ma il contrassegno non è in fattura")
        if info and str(info.get("order_cancelled_at") or "").strip() and chiave_esito != "annullato":
            controlli.append("Ordine annullato su Shopify")
        for c in controlli:
            chiave_c = "Fatturata più volte nel file" if c.startswith("Fatturata ") else c
            controlli_totali[chiave_c] = controlli_totali.get(chiave_c, 0) + 1
        riepilogo_esiti[chiave_esito]["righe"] += 1
        riepilogo_esiti[chiave_esito]["costo"] += costo
        if info:
            n = negozi.setdefault(negozio(info), {"spedizioni": set(), "costo": 0.0})
            n["spedizioni"].add(cifre)
            n["costo"] += costo
        analizzate.append({
            "valori": r, "esito": chiave_esito, "dettaglio": dettaglio, "costo": costo,
            "controlli": controlli, "info": info,
        })
        if len(analizzate) % 100 == 0:
            avanzamento(len(analizzate), len(righe))
    return {
        "colonne": colonne,
        "righe": analizzate,
        "riepilogo": {
            "righe": len(righe),
            "spedizioni": len(spedizioni),
            "trovate": len({_cifre(a["valori"][i_sped]) for a in analizzate if a["info"]}),
            "costo_totale": round(sum(a["costo"] for a in analizzate), 2),
            "esiti": [{"chiave": k, "nome": ESITI[k]["nome"], "righe": v["righe"], "costo": round(v["costo"], 2)}
                      for k, v in riepilogo_esiti.items() if v["righe"]],
            "colonne_importi": [{"nome": n, "totale": round(t, 2)} for n, t in totali_colonne.items()],
            "colonne_costo": [intestazioni[i] for i in colonne["costi"]],
            "controlli": [{"nome": n, "righe": q} for n, q in sorted(controlli_totali.items(), key=lambda x: -x[1])],
            "negozi": [{"nome": nome, "spedizioni": len(v["spedizioni"]), "costo": round(v["costo"], 2)}
                       for nome, v in sorted(negozi.items(), key=lambda x: (x[0] != NEGOZIO, x[0]))],
        },
    }


def _quando(valore: Any) -> str:
    if isinstance(valore, datetime):
        return valore.strftime("%d/%m/%Y %H:%M") if (valore.hour or valore.minute) else valore.strftime("%d/%m/%Y")
    if isinstance(valore, date):
        return valore.strftime("%d/%m/%Y")
    return "" if valore is None else str(valore)


def colonne_aggiunte(riga: dict[str, Any], quando_gls: Any = None) -> list[Any]:
    """I valori delle colonne che il monitor aggiunge al file."""
    info = riga["info"] or {}
    giacenze = int(info.get("passaggi") or info.get("giacenze") or 0)
    pagamento = ""
    if info and info.get("is_cod") is not None:
        pagamento = "Contrassegno" if info.get("is_cod") else "Pagato online"
    return [
        ESITI[riga["esito"]]["nome"], negozio(info), riga["dettaglio"], info.get("order_name") or "",
        info.get("gls_status") or "", quando_gls if quando_gls is not None else (info.get("gls_event_at") or ""),
        (f"{giacenze} {'passaggio' if giacenze == 1 else 'passaggi'} in sede" if giacenze else ""),
        pagamento, "; ".join(riga["controlli"]),
    ]


def file_analizzato(intestazioni: list[str], analisi: dict[str, Any]) -> bytes:
    """Il file del service con le colonne del monitor e le righe colorate."""
    from .esporta import _quando as quando_italiano
    from .xlsx_export import build_xlsx

    righe, colori = [], []
    for a in analisi["righe"]:
        info = a["info"] or {}
        righe.append(list(a["valori"]) + colonne_aggiunte(a, quando_italiano(info.get("gls_event_at"))))
        colori.append(ESITI[a["esito"]]["colore"])
    return build_xlsx(list(intestazioni) + COLONNE_AGGIUNTE, righe, "Spedizioni analizzate", colori)


def per_lo_schermo(analisi: dict[str, Any], intestazioni: list[str]) -> dict[str, Any]:
    """Tutto in testo, pronto per la tabella della pagina."""
    importi = set(analisi["colonne"]["importi"])
    righe = []
    for a in analisi["righe"]:
        valori = []
        for i, v in enumerate(a["valori"]):
            if i in importi and importo(v) is not None:
                valori.append(f"{importo(v):.2f}".replace(".", ","))
            elif i == analisi["colonne"]["spedizione"]:
                valori.append(_cifre(v) or ("" if v is None else str(v)))
            else:
                valori.append(_quando(v))
        info = a["info"] or {}
        righe.append({
            "valori": valori, "esito": a["esito"], "dettaglio": a["dettaglio"], "costo": a["costo"],
            "controlli": a["controlli"], "negozio": negozio(info),
            # Il dettaglio si apre solo per le spedizioni che il monitor segue.
            "tracking": "" if info.get("negozio") else (info.get("tracking_number") or ""),
            "ordine": info.get("order_name") or "", "stato_gls": info.get("gls_status") or "",
            "giacenze": int(info.get("passaggi") or info.get("giacenze") or 0),
            "contrassegno": info.get("is_cod") if info and info.get("is_cod") is not None else None,
        })
    return {"intestazioni": intestazioni, "importi": sorted(importi), "righe": righe,
            "riepilogo": analisi["riepilogo"],
            "esiti": {k: {"nome": v["nome"], "colore": "#" + v["colore"][2:]} for k, v in ESITI.items()}}


# ---------------------------------------------------------------- lavori


class Lavori:
    """Analisi che girano in sottofondo, con la percentuale reale.

    La pagina chiede a che punto sono ogni mezzo secondo. Restano in memoria
    le ultime, per poter scaricare il file analizzato senza rifare tutto.
    """

    def __init__(self, tenuti: int = 4) -> None:
        import threading

        self._lavori: dict[str, dict[str, Any]] = {}
        self._ordine: list[str] = []
        self._lock = threading.Lock()
        self._tenuti = tenuti

    def avvia(self, funzione: Any, *argomenti: Any) -> str:
        import threading
        import time
        import uuid

        chiave = uuid.uuid4().hex[:12]
        with self._lock:
            self._lavori[chiave] = {"percentuale": 0.0, "fase": "In partenza", "iniziato": time.time(),
                                    "finito": False, "errore": None, "risultato": None, "analisi": None}
            self._ordine.append(chiave)
            while len(self._ordine) > self._tenuti:
                self._lavori.pop(self._ordine.pop(0), None)

        def esegui() -> None:
            try:
                funzione(chiave, *argomenti)
            except FileNonLeggibile as exc:
                self.aggiorna(chiave, finito=True, errore=str(exc))
            except Exception as exc:  # pragma: no cover - mostrato all'operatore
                self.aggiorna(chiave, finito=True, errore=f"{type(exc).__name__}: {exc}")

        threading.Thread(target=esegui, daemon=True).start()
        return chiave

    def aggiorna(self, chiave: str, **campi: Any) -> None:
        with self._lock:
            if chiave in self._lavori:
                self._lavori[chiave].update(campi)

    def stato(self, chiave: str) -> dict[str, Any] | None:
        import time

        with self._lock:
            lavoro = self._lavori.get(chiave)
            if not lavoro:
                return None
            trascorsi = time.time() - lavoro["iniziato"]
            percentuale = float(lavoro["percentuale"])
            # Tempo rimanente stimato dal ritmo misurato finora.
            rimanenti = None
            if 2 <= percentuale < 100:
                rimanenti = round(trascorsi / percentuale * (100 - percentuale))
            return {"percentuale": round(percentuale, 1), "fase": lavoro["fase"], "secondi_trascorsi": round(trascorsi),
                    "secondi_rimanenti": rimanenti, "finito": lavoro["finito"], "errore": lavoro["errore"],
                    "risultato": lavoro["risultato"] if lavoro["finito"] else None}

    def analisi(self, chiave: str) -> Any:
        with self._lock:
            lavoro = self._lavori.get(chiave)
            return lavoro.get("analisi") if lavoro else None


def riepilogo_breve(schermo: dict[str, Any]) -> dict[str, Any]:
    """Quello che serve alla riga del mese nell'elenco delle analisi."""
    r = schermo["riepilogo"]
    return {"righe": r["righe"], "spedizioni": r["spedizioni"], "trovate": r["trovate"],
            "costo_totale": r["costo_totale"], "esiti": r["esiti"], "negozi": r.get("negozi", []),
            "controlli": sum(1 for riga in schermo["righe"] if riga["controlli"])}
