from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

from .timezones import ROME  # noqa: E402


def errore_dichiarato(raw: str) -> str:
    """Restituisce il testo di <DescrizioneErrore> se GLS ha rifiutato la chiamata."""
    if not raw:
        return ""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        trovato = re.search(r"<DescrizioneErrore>(.*?)</DescrizioneErrore>", raw, flags=re.I | re.S)
        return html.unescape(trovato.group(1)).strip() if trovato else ""
    for node in root.iter():
        if strip_ns(node.tag).lower() == "descrizioneerrore":
            return (node.text or "").strip()
    return ""


def spiega_rifiuto(messaggio: str) -> str:
    """Aggiunge al messaggio di GLS l'indicazione di cosa controllare."""
    testo = (messaggio or "").strip()
    if "non abilitata" in testo.lower():
        return (f"{testo} GLS ha respinto le credenziali del web service: la password "
                "del web service e' distinta da quella del portale e va riabilitata "
                "dalla filiale GLS.")
    return testo

class GLSError(RuntimeError):
    pass


def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def child_text(node: ET.Element, name: str) -> str:
    lname = name.lower()
    for child in list(node):
        if strip_ns(child.tag).lower() == lname:
            return (child.text or "").strip()
    return ""


def numero_senza_sede(tracking_number: str, sede: str = "") -> str:
    """Toglie dal numero di spedizione la sigla della sede di partenza."""
    numero = str(tracking_number or "").strip()
    sigla = str(sede or "").strip().upper()
    if sigla and numero.upper().startswith(sigla) and numero[len(sigla):].isdigit():
        return numero[len(sigla):]
    return re.sub(r"^[A-Za-z]+(?=\d)", "", numero)


def ordina_cronologicamente(elenco: list[dict[str, Any]]) -> None:
    """Ordina dal piu' vecchio al piu' recente.

    GLS data gli eventi al minuto, quindi due scansioni dello stesso
    minuto ("in consegna" e "consegnata") arrivano indistinguibili. Ma li
    pubblica dal piu' recente al piu' vecchio: a parita' di orario vale
    quell'ordine, altrimenti l'ultimo stato sarebbe deciso dal caso.
    """
    def chiave(coppia):
        posizione, evento = coppia
        testo = evento.get("event_at") or ""
        try:
            momento = datetime.fromisoformat(testo)
        except Exception:
            momento = datetime.min.replace(tzinfo=ROME)
        return (momento, -posizione)

    ordinati = [e for _, e in sorted(enumerate(elenco), key=chiave)]
    elenco[:] = ordinati


def normalize_event_at(date_text: str, time_text: str) -> str | None:
    raw = " ".join(x.strip() for x in [date_text or "", time_text or ""] if x and x.strip())
    if not raw:
        return None
    for fmt in (
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d/%m/%Y",
        "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=ROME).isoformat(timespec="seconds")
        except ValueError:
            continue

    # Il tracking XML di GLS usa l'anno a due cifre e non riporta i minuti oltre
    # il sessantesimo: "17/09/26 06:63" e' le 07:03 del 17 settembre 2026.
    # Senza questa lettura ogni evento resterebbe testo grezzo, quindi senza
    # ordine cronologico e senza eta' su cui calcolare le scadenze.
    giorno = re.match(r"^\s*(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})", raw)
    if giorno:
        d, m, a = (int(giorno.group(i)) for i in (1, 2, 3))
        if a < 100:
            a += 2000
        try:
            momento = datetime(a, m, d, tzinfo=ROME)
        except ValueError:
            return raw
        orario = re.search(r"(\d{1,2}):(\d{1,3})(?::(\d{1,3}))?", raw[giorno.end():])
        if orario:
            minuti = int(orario.group(1)) * 60 + int(orario.group(2))
            secondi = int(orario.group(3) or 0)
            momento += timedelta(minutes=minuti, seconds=secondi)
        return momento.isoformat(timespec="seconds")
    return raw




class _GLSTableParser(HTMLParser):
    """Extracts shipment event rows from the public GLS Track & Trace page."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_tr = False
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self.in_tr = True
            self.current_row = []
        elif self.in_tr and tag in {"td", "th"}:
            self.in_cell = True
            self.current_cell = []
        elif self.in_cell and tag == "br":
            self.current_cell.append(" ")

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.in_tr and tag in {"td", "th"} and self.in_cell:
            text = re.sub(r"\s+", " ", "".join(self.current_cell)).strip()
            self.current_row.append(text)
            self.current_cell = []
            self.in_cell = False
        elif tag == "tr" and self.in_tr:
            if self.current_row:
                self.rows.append(self.current_row)
            self.current_row = []
            self.in_tr = False
            self.in_cell = False


class GLSClient:
    def __init__(self, config):
        self.config = config
        self._public_semaphore = threading.BoundedSemaphore(getattr(config, "gls_public_workers", 2))
        self._xml_disabled_until = 0.0
        self._xml_state_lock = threading.Lock()

    @staticmethod
    def _is_dns_error(message: str) -> bool:
        text = str(message or "").lower()
        return any(x in text for x in [
            "nodename nor servname", "name or service not known",
            "temporary failure in name resolution", "errno 8", "getaddrinfo failed",
        ])

    @staticmethod
    def _is_transient_error(message: str) -> bool:
        text = str(message or "").lower()
        if GLSClient._is_dns_error(text):
            return False
        return any(x in text for x in [
            "timed out", "timeout", "http 429", "too many requests",
            "http 500", "http 502", "http 503", "http 504",
            "connection reset", "remote end closed", "temporarily unavailable",
            "errore rete gls",
        ])

    def _call_with_retries(self, func):
        attempts = max(1, int(getattr(self.config, "gls_retry_attempts", 3)))
        last_exc = None
        for attempt in range(1, attempts + 1):
            try:
                return func(), attempt
            except Exception as exc:
                last_exc = exc
                if attempt >= attempts or not self._is_transient_error(str(exc)):
                    raise
                # Backoff breve: evita di martellare il Track & Trace pubblico GLS.
                time.sleep(min(4.0, 0.6 * (2 ** (attempt - 1))))
        raise last_exc  # pragma: no cover

    def _get_bytes(self, url: str) -> bytes:
        req = Request(
            url,
            headers={
                "User-Agent": "GLS-Exception-Monitor/1.0",
                "Accept": "application/xml,text/xml,*/*",
            },
            method="GET",
        )
        try:
            with urlopen(req, timeout=self.config.request_timeout_seconds) as response:
                return response.read()
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GLSError(f"GLS HTTP {exc.code}: {body[:600]}") from exc
        except URLError as exc:
            raise GLSError(f"Errore rete GLS: {exc}") from exc

    def _post_form_bytes(self, url: str, form: dict[str, str]) -> bytes:
        body = urlencode(form).encode("utf-8")
        req = Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "GLS-Exception-Monitor/1.0",
                "Accept": "application/xml,text/xml,*/*",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=self.config.request_timeout_seconds) as response:
                return response.read()
        except HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise GLSError(f"GLS HTTP {exc.code}: {body_text[:600]}") from exc
        except URLError as exc:
            raise GLSError(f"Errore rete GLS: {exc}") from exc

    def release_shipment_stock(self, tracking_number: str, data: dict[str, Any]) -> dict[str, Any]:
        """Invia a GLS le istruzioni di svincolo giacenza via LabelService."""
        if not self.config.gls_list_configured:
            raise GLSError("Credenziali GLS LabelService non configurate")

        release_type = str(data.get("release_type") or "").strip()
        labels = {
            "1": "Riconsegna allo stesso indirizzo",
            "2": "Consegna a un indirizzo diverso",
            "3": "Rientro al mittente",
            "4": "Distruzione",
            "7": "Ritiro del destinatario presso la sede GLS",
            "8": "Consegna parziale e rientro",
            "9": "Consegna parziale e distruzione",
        }
        if release_type not in labels:
            raise ValueError("Tipo di svincolo GLS non valido")

        delivery_date = str(data.get("delivery_date") or "").strip()
        if delivery_date:
            try:
                parsed = datetime.strptime(delivery_date, "%Y-%m-%d")
                delivery_date = parsed.strftime("%d/%m/%y")
            except ValueError:
                if not re.match(r"^\d{2}/\d{2}/\d{2}$", delivery_date):
                    raise ValueError("Data riconsegna non valida")

        if release_type in {"1", "2"}:
            if not delivery_date:
                raise ValueError("Per la riconsegna GLS indica la data di riconsegna")
            payer = str(data.get("expense_payer") or "").strip().lower()
            if payer not in {"sender", "recipient"}:
                raise ValueError("Indica a chi addebitare le spese di riconsegna: mittente o destinatario")
        else:
            payer = ""

        if data.get("phone_notice") and not str(data.get("recipient_phone") or "").strip():
            raise ValueError("Per il preavviso telefonico inserisci il telefono del destinatario")

        if release_type == "2":
            required = ["new_name", "new_address", "new_city", "new_zip", "new_province"]
            missing = [key for key in required if not str(data.get(key) or "").strip()]
            if missing:
                raise ValueError("Per il nuovo indirizzo compila destinatario, indirizzo, località, CAP e provincia")

        def x(value: Any) -> str:
            return html.escape(str(value or "").strip(), quote=False)

        def local_phone(value: Any) -> str:
            digits = re.sub(r"\D+", "", str(value or ""))
            if digits.startswith("0039"):
                digits = digits[4:]
            elif digits.startswith("39") and len(digits) > 10:
                digits = digits[2:]
            return digits

        customer_phone = local_phone(data.get("customer_phone"))
        recipient_phone = local_phone(data.get("recipient_phone"))

        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Info>\n'
            f'<SedeGls>{x(self.config.gls_site)}</SedeGls>\n'
            f'<CodiceClienteGls>{x(self.config.gls_customer_code)}</CodiceClienteGls>\n'
            f'<PasswordClienteGls>{x(self.config.gls_password)}</PasswordClienteGls>\n'
            '<ReleaseShipmentStock>\n'
            f'<CodiceContrattoGls>{x(self.config.gls_contract_code)}</CodiceContrattoGls>\n'
            f'<NumeroSpedizione>{x(tracking_number)}</NumeroSpedizione>\n'
            f'<TipoSvincolo>{x(release_type)}</TipoSvincolo>\n'
            f'<TelefonoCliente>{x(customer_phone)}</TelefonoCliente>\n'
            f'<ConPreavvisoTelefonico>{"S" if data.get("phone_notice") else ""}</ConPreavvisoTelefonico>\n'
            f'<TelefonoDestinatario>{x(recipient_phone)}</TelefonoDestinatario>\n'
            f'<AnnullandoCAssegno>{"S" if data.get("cancel_cod") else ""}</AnnullandoCAssegno>\n'
            f'<VariandoCAssegno>{"S" if data.get("change_cod") else ""}</VariandoCAssegno>\n'
            f'<NuovoImportoCAssegno>{x(data.get("new_cod_amount"))}</NuovoImportoCAssegno>\n'
            '<VariandoInPortoFranco></VariandoInPortoFranco>\n'
            '<VariandoInPortoAssegnato></VariandoInPortoAssegnato>\n'
            f'<SpeseAdMittente>{"S" if payer == "sender" else "N" if payer else ""}</SpeseAdMittente>\n'
            f'<SpeseAdDestinatario>{"S" if payer == "recipient" else "N" if payer else ""}</SpeseAdDestinatario>\n'
            f'<DataRiconsegna>{x(delivery_date)}</DataRiconsegna>\n'
            f'<Note>{x(data.get("note"))[:65]}</Note>\n'
            f'<CognomeNome>{x(data.get("new_name"))[:20]}</CognomeNome>\n'
            f'<NuovoIndirizzo>{x(data.get("new_address"))[:30]}</NuovoIndirizzo>\n'
            f'<NuovaLocalita>{x(data.get("new_city"))[:30]}</NuovaLocalita>\n'
            f'<NuovoZipcode>{x(data.get("new_zip"))[:5]}</NuovoZipcode>\n'
            f'<NuovaProvincia>{x(data.get("new_province"))[:3]}</NuovaProvincia>\n'
            '</ReleaseShipmentStock>\n'
            '</Info>'
        )

        raw = self._post_form_bytes(
            self.config.gls_release_endpoint,
            {"XMLInfoReleaseShipmentStock": xml},
        )
        raw_text = raw.decode("utf-8", errors="replace")
        inner = raw_text
        try:
            root = ET.fromstring(raw_text)
            if strip_ns(root.tag).lower() in {"string", "xml"} and (root.text or "").strip():
                inner = html.unescape(root.text or "")
        except ET.ParseError:
            pass

        result_text = ""
        success = False
        try:
            parsed = ET.fromstring(inner)
            for node in parsed.iter():
                tag = strip_ns(node.tag).lower()
                value = (node.text or "").strip()
                if tag == "svincolo" and value:
                    result_text = value
                    success = value.lower() == "ok"
                    break
                if tag == "descrizioneerrore" and value and not result_text:
                    result_text = spiega_rifiuto(value)
        except ET.ParseError:
            pass
        if not result_text:
            compact = re.sub(r"\s+", " ", html.unescape(inner)).strip()
            result_text = compact[:1000] or "Risposta GLS vuota"
            success = bool(re.search(r"<Svincolo>\s*Ok\s*</Svincolo>", inner, flags=re.I))

        return {
            "success": success,
            "result": result_text,
            "raw_response": inner,
            "release_type": release_type,
            "release_label": labels[release_type],
        }

    def track(self, tracking_number: str) -> dict[str, Any]:
        if not self.config.gls_tracking_configured:
            raise GLSError("GLS Track & Trace non configurato")
        params = {
            "locpartenza": self.config.gls_site,
            # Il numero va passato senza la sigla della sede di partenza:
            # "NI665031172" e' la spedizione 665031172 partita da NI, e con la
            # sigla attaccata GLS risponde "Spedizione non trovata".
            "numsped": numero_senza_sede(tracking_number, self.config.gls_site),
        }
        if self.config.gls_contract_code:
            params["CodCli"] = self.config.gls_contract_code
        else:
            params["Cli"] = self.config.gls_customer_code
        url = self.config.gls_track_endpoint + "?" + urlencode(params)

        xml_exc: Exception | None = None
        with self._xml_state_lock:
            xml_enabled = time.monotonic() >= self._xml_disabled_until
        if xml_enabled:
            try:
                raw = self._get_bytes(url)
                tracked = self.parse_tracking_xml(raw, tracking_number)
                tracked["tracking_source"] = "GLS XML"
                tracked["tracking_attempts"] = 1
                return tracked
            except Exception as exc:
                xml_exc = exc
                # Se l'host XML legacy non risolve via DNS, non ha senso riprovare per ogni
                # spedizione: usiamo il fallback pubblico per 10 minuti e poi ritestiamo.
                if self._is_dns_error(str(exc)):
                    with self._xml_state_lock:
                        self._xml_disabled_until = time.monotonic() + 600
        else:
            xml_exc = GLSError("endpoint XML temporaneamente escluso dopo errore DNS")

        # Il fallback pubblico e' piu sensibile a raffiche di richieste: limitiamo la
        # concorrenza e riproviamo solo gli errori transitori (timeout/429/5xx/rete).
        try:
            with self._public_semaphore:
                tracked, attempts = self._call_with_retries(lambda: self.track_public(tracking_number))
            tracked["tracking_source"] = "GLS public fallback"
            tracked["tracking_attempts"] = attempts
            tracked["xml_fallback_reason"] = str(xml_exc)[:500] if xml_exc else ""
            return tracked
        except Exception as public_exc:
            attempts = max(1, int(getattr(self.config, "gls_retry_attempts", 3))) if self._is_transient_error(str(public_exc)) else 1
            raise GLSError(
                f"Tracking GLS non disponibile dopo {attempts} tentativo/i. "
                f"XML: {xml_exc}; fallback pubblico: {public_exc}"
            ) from public_exc

    def track_public(self, tracking_number: str) -> dict[str, Any]:
        params = {
            "option": "com_gls",
            "view": "track_e_trace",
            "mode": "search",
            "numero_spedizione": tracking_number,
            "tipo_codice": "nazionale",
        }
        url = "https://www.gls-italy.com/?" + urlencode(params)
        raw = self._get_bytes(url)
        text = raw.decode("utf-8", errors="replace")
        parser = _GLSTableParser()
        parser.feed(text)
        events: list[dict[str, Any]] = []
        for row in parser.rows:
            if len(row) < 3:
                continue
            first = row[0].strip()
            m = re.match(r"^(\d{2}/\d{2}/\d{4})(?:\s+(\d{1,2}:\d{2})(?::(\d{2}))?)?$", first)
            if not m:
                continue
            date_text = m.group(1)
            time_text = m.group(2) or ""
            location = row[1].strip() if len(row) > 1 else ""
            state = row[2].strip() if len(row) > 2 else ""
            note = " | ".join(x.strip() for x in row[3:] if x.strip())
            if not state:
                continue
            events.append({
                "event_at": normalize_event_at(date_text, time_text),
                "date": date_text,
                "time": time_text,
                "location": location,
                "state": state,
                "note": note,
                "code": "",
            })
        if not events:
            plain = re.sub(r"<[^>]+>", " ", text)
            plain = html.unescape(re.sub(r"\s+", " ", plain)).strip()
            if "cerca la tua spedizione" in plain.lower():
                raise GLSError("GLS non ha restituito eventi per questo numero spedizione")
            raise GLSError("Pagina pubblica GLS non interpretabile")
        ordina_cronologicamente(events)
        current = events[-1]
        return {
            "tracking_number": tracking_number,
            "bda": "",
            "departure_date": events[0].get("date") or "",
            "departure_site": "",
            "recipient": "",
            "destination_address": "",
            "destination_postcode": "",
            "destination_city": "",
            "destination_depot": "",
            "destination_depot_city": current.get("location") or "",
            "destination_depot_phone": "",
            "status": current.get("state") or "",
            "delivery_date": current.get("date") or "",
            "note": current.get("note") or "",
            "code": "",
            "cod_amount": "",
            "events": events,
            "current_event": current,
        }

    @staticmethod
    def parse_tracking_xml(raw: bytes | str, tracking_number: str = "") -> dict[str, Any]:
        if isinstance(raw, str):
            raw_bytes = raw.encode("utf-8")
        else:
            raw_bytes = raw
        try:
            root = ET.fromstring(raw_bytes)
        except ET.ParseError as exc:
            preview = raw_bytes[:500].decode("latin-1", errors="replace")
            raise GLSError(f"XML tracking GLS non valido: {preview}") from exc

        shipment = None
        for node in root.iter():
            if strip_ns(node.tag).upper() == "SPEDIZIONE":
                shipment = node
                break
        if shipment is None:
            text = " ".join((node.text or "").strip() for node in root.iter() if (node.text or "").strip())
            if text:
                raise GLSError(f"GLS non ha restituito una spedizione valida: {text[:500]}")
            raise GLSError("GLS non ha restituito dati per la spedizione")

        top = {
            "tracking_number": child_text(shipment, "NumSped") or tracking_number,
            "bda": child_text(shipment, "Bda"),
            "departure_date": child_text(shipment, "DataPartenza"),
            "departure_site": child_text(shipment, "SedePartenza"),
            "recipient": child_text(shipment, "Destinatario"),
            "destination_address": child_text(shipment, "IndirizzoDestinazione"),
            "destination_postcode": child_text(shipment, "CapDestinatario"),
            "destination_city": child_text(shipment, "CittaDestinazione"),
            "destination_depot": child_text(shipment, "SedeDestinazione"),
            "destination_depot_city": child_text(shipment, "LocalitaSedeDestinazione"),
            "destination_depot_phone": child_text(shipment, "TelefonoSedeDestinazione"),
            "status": child_text(shipment, "StatoSpedizione"),
            "delivery_date": child_text(shipment, "DataConsegna"),
            "note": child_text(shipment, "Note"),
            "code": child_text(shipment, "Codice"),
            "cod_amount": child_text(shipment, "Contrassegno"),
        }

        events: list[dict[str, Any]] = []
        for tracking_node in shipment.iter():
            if strip_ns(tracking_node.tag).upper() != "TRACKING":
                continue
            children = list(tracking_node)
            current: dict[str, str] = {}
            for child in children:
                key = strip_ns(child.tag).lower()
                value = (child.text or "").strip()
                if key == "data" and current:
                    events.append(GLSClient._finalize_event(current))
                    current = {}
                if key in {"data", "ora", "luogo", "stato", "note", "codice"}:
                    current[key] = value
            if current:
                events.append(GLSClient._finalize_event(current))

        # Remove rows that are completely empty.
        events = [e for e in events if any(e.get(k) for k in ["state", "note", "code", "event_at", "location"])]

        # If the nested tracking does not expose the current state, add a synthetic current event.
        if not events and any(top.get(k) for k in ["status", "note", "code"]):
            events.append(
                {
                    "event_at": normalize_event_at(top.get("delivery_date", ""), ""),
                    "date": top.get("delivery_date", ""),
                    "time": "",
                    "location": top.get("destination_depot_city", ""),
                    "state": top.get("status", ""),
                    "note": top.get("note", ""),
                    "code": top.get("code", ""),
                }
            )

        ordina_cronologicamente(events)
        current_event = events[-1] if events else {
            "event_at": None,
            "location": top.get("destination_depot_city", ""),
            "state": top.get("status", ""),
            "note": top.get("note", ""),
            "code": top.get("code", ""),
        }

        # I campi di testata (StatoSpedizione, Note, Codice) sono un riassunto
        # grezzo e spesso vecchio: "Non consegnato" con la nota della giacenza di
        # ieri resta li' anche quando l'ultima scansione dice che oggi il collo
        # esce in consegna. Riempiono quindi solo i vuoti dell'ultimo evento, non
        # lo sovrascrivono, altrimenti la novita' andrebbe persa.
        # La nota di testata non viene mai riportata sull'ultimo evento: resta
        # ferma alla scansione precedente ("IN ATTESA DI ISTRUZIONI" della
        # giacenza di ieri) e riporterebbe la spedizione in giacenza anche dopo
        # che GLS l'ha rimessa in consegna.
        current_event = dict(current_event)
        for chiave, valore in (("state", top.get("status")), ("code", top.get("code"))):
            if valore and not (current_event.get(chiave) or "").strip():
                current_event[chiave] = valore

        return {**top, "events": events, "current_event": current_event}

    @staticmethod
    def _finalize_event(raw: dict[str, str]) -> dict[str, Any]:
        return {
            "event_at": normalize_event_at(raw.get("data", ""), raw.get("ora", "")),
            "date": raw.get("data", ""),
            "time": raw.get("ora", ""),
            "location": raw.get("luogo", ""),
            "state": raw.get("stato", ""),
            "note": raw.get("note", ""),
            "code": raw.get("codice", ""),
        }

    @staticmethod
    def event_hash(tracking_number: str, event: dict[str, Any]) -> str:
        payload = "|".join(
            str(event.get(k) or "")
            for k in ["event_at", "code", "state", "note", "location"]
        )
        return hashlib.sha256(f"{tracking_number}|{payload}".encode("utf-8")).hexdigest()

    def list_shipments_raw(self) -> str:
        if not self.config.gls_list_configured:
            raise GLSError("GLS ListSped non configurato")
        body = urlencode(
            {
                "SedeGls": self.config.gls_site,
                "CodiceClienteGls": self.config.gls_customer_code,
                "PasswordClienteGls": self.config.gls_password,
            }
        ).encode("utf-8")
        req = Request(
            self.config.gls_list_endpoint,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "GLS-Exception-Monitor/1.0"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=self.config.request_timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise GLSError(f"GLS ListSped HTTP {exc.code}: {body_text[:600]}") from exc
        except URLError as exc:
            raise GLSError(f"Errore rete GLS ListSped: {exc}") from exc

        text = raw.decode("utf-8", errors="replace")
        # ASMX may wrap the XML payload as escaped text in <string>.
        try:
            root = ET.fromstring(text)
            if strip_ns(root.tag).lower() in {"string", "xml"} and (root.text or "").strip():
                return html.unescape(root.text or "")
        except ET.ParseError:
            pass
        return text

    def diagnostics(self) -> dict[str, Any]:
        result = {"tracking_configured": self.config.gls_tracking_configured, "list_configured": self.config.gls_list_configured}
        if self.config.gls_list_configured:
            try:
                raw = self.list_shipments_raw()
                rifiuto = errore_dichiarato(raw)
                result["list_sped_ok"] = bool(raw.strip()) and not rifiuto
                result["list_sped_preview"] = re.sub(r"\s+", " ", raw)[:220]
                if rifiuto:
                    result["list_sped_error"] = spiega_rifiuto(rifiuto)
            except Exception as exc:
                result["list_sped_ok"] = False
                result["list_sped_error"] = str(exc)
        return result
