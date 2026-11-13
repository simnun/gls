"""Le spedizioni filtrate a schermo, come foglio Excel.

Ogni colonna e' un dato che il monitor conosce della spedizione, scritto
come lo legge chi lavora: "Chiusa" e non RESOLVED, "In giacenza" e non
STORAGE, date in ora italiana. Importi e date restano numeri e date, cosi'
in Excel si ordinano, si filtrano e si sommano.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from .timezones import ROME

PRIORITA = {"CRITICAL": "Massima", "WARNING": "Alta", "WATCH": "Osservazione",
            "INFO": "Informativa", "NORMAL": "Normale"}
PRATICA = {"NEW": "Nuova", "IN_PROGRESS": "In lavorazione", "WAITING_CUSTOMER": "In lavorazione",
           "WAITING_GLS": "In lavorazione", "RESOLVED": "Chiusa", "IGNORED": "Chiusa"}
# Le stesse parole della pagina, al singolare: una riga e' una spedizione.
SITUAZIONE = {
    "STORAGE": "In giacenza in sede GLS", "ADDRESS_ERROR": "Indirizzo errato", "ABSENT": "Destinatario assente",
    "PICKUP_AT_DEPOT": "Da ritirare in sede GLS", "REFUSED": "Rifiutata dal destinatario",
    "COD_ISSUE": "Problema col contrassegno", "DAMAGE_OR_LOSS": "Danneggiata o smarrita",
    "DELIVERY_FAILURE": "Consegna non riuscita", "DELIVERY_RETRY": "Nuovo tentativo di consegna",
    "RECIPIENT_CLOSED": "Destinatario chiuso", "ACTION_REQUIRED": "Serve un intervento",
    "OUT_FOR_DELIVERY": "In consegna oggi", "IN_TRANSIT": "In viaggio", "SCHEDULED": "Consegna programmata",
    "LINEHAUL_DELAY": "In ritardo", "CORRESPONDENT": "Presso un corrispondente", "SERVICE_AREA": "Zona disagiata",
    "LABEL_CREATED": "Affidata a GLS", "DELIVERED": "Consegnata", "RETURN": "Rientrata al mittente",
    "UNCLASSIFIED": "Stato GLS nuovo", "NO_GLS_EVENTS": "Nessun evento GLS", "PENDING_PICKUP": "In attesa di ritiro",
    "PENDING_CHECK": "Da controllare", "ORDER_CANCELLED": "Ordine annullato",
    "REPLACED_CARRIER": "Passata a un altro corriere",
}


def _quando(valore: Any) -> datetime | str:
    """Data e ora in ora italiana, senza fuso: Excel non ne ha."""
    testo = str(valore or "").strip()
    if not testo:
        return ""
    try:
        momento = datetime.fromisoformat(testo.replace("Z", "+00:00"))
    except ValueError:
        return testo
    if momento.tzinfo is not None:
        momento = momento.astimezone(ROME)
    return momento.replace(tzinfo=None, microsecond=0)


def _pagamento(valore: Any) -> str:
    if isinstance(valore, str):
        try:
            valore = json.loads(valore or "[]")
        except ValueError:
            return valore
    return ", ".join(str(x) for x in (valore or []) if x)


def _importo(valore: Any) -> float | str:
    try:
        return round(float(valore), 2)
    except (TypeError, ValueError):
        return ""


# (intestazione, come ricavare il valore da una spedizione)
COLONNE: list[tuple[str, Callable[[dict[str, Any]], Any]]] = [
    ("Ordine", lambda s: s.get("order_name") or ""),
    ("Data ordine", lambda s: _quando(s.get("order_created_at"))),
    ("Tracking GLS", lambda s: s.get("tracking_number") or ""),
    ("Cliente", lambda s: s.get("customer_name") or ""),
    ("Email", lambda s: s.get("customer_email") or ""),
    ("Telefono", lambda s: s.get("customer_phone") or ""),
    ("Citta", lambda s: s.get("city") or ""),
    ("Provincia", lambda s: s.get("province") or ""),
    ("Importo", lambda s: _importo(s.get("total_amount"))),
    ("Valuta", lambda s: s.get("currency") or ""),
    ("Pagamento", lambda s: _pagamento(s.get("payment_gateways"))),
    ("Contrassegno", lambda s: bool(s.get("is_cod"))),
    ("Stato pagamento Shopify", lambda s: s.get("shopify_financial_status") or ""),
    ("Stato evasione Shopify", lambda s: s.get("shopify_fulfillment_status") or ""),
    ("Ordine annullato il", lambda s: _quando(s.get("order_cancelled_at"))),
    ("Spedita il", lambda s: _quando(s.get("fulfillment_created_at"))),
    ("Priorita", lambda s: PRIORITA.get(s.get("effective_severity") or s.get("severity") or "", s.get("severity") or "")),
    ("Situazione", lambda s: SITUAZIONE.get(s.get("category") or "", s.get("category") or "")),
    ("Stato GLS", lambda s: s.get("gls_status") or ""),
    ("Nota GLS", lambda s: s.get("gls_note") or ""),
    ("Codice GLS", lambda s: s.get("gls_code") or ""),
    ("Ultimo evento GLS", lambda s: _quando(s.get("gls_event_at"))),
    ("Luogo ultimo evento", lambda s: s.get("gls_location") or ""),
    ("Sede GLS", lambda s: s.get("gls_destination_depot") or ""),
    ("Citta sede GLS", lambda s: s.get("gls_destination_city") or ""),
    ("Telefono sede GLS", lambda s: s.get("gls_destination_phone") or ""),
    ("Motivo", lambda s: s.get("reason") or ""),
    ("Azione consigliata", lambda s: s.get("recommended_action") or ""),
    ("Pratica", lambda s: "Chiusa" if s.get("closed") else PRATICA.get(s.get("workflow_status") or "", s.get("workflow_status") or "")),
    ("Nota pratica", lambda s: s.get("operator_note") or ""),
    ("Ultima azione", lambda s: s.get("last_operator_action") or ""),
    ("Operatore", lambda s: s.get("last_operator_name") or ""),
    ("Data ultima azione", lambda s: _quando(s.get("last_operator_action_at"))),
    ("Giacenze", lambda s: int(s.get("giacenze") or 0)),
    ("Svincoli inviati", lambda s: int(s.get("svincoli") or 0)),
    ("Ultimo controllo GLS", lambda s: _quando(s.get("gls_checked_at"))),
    ("Monitorata dal", lambda s: _quando(s.get("first_seen_at"))),
    ("Conclusa", lambda s: bool(s.get("closed"))),
]


def righe_spedizioni(
    spedizioni: list[dict[str, Any]],
    collegamenti: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> tuple[list[str], list[list[Any]]]:
    """Intestazioni e righe del foglio, nell'ordine ricevuto."""
    intestazioni = [nome for nome, _ in COLONNE]
    if collegamenti:
        intestazioni += ["Link tracking GLS", "Link ordine Shopify"]
    righe = []
    for spedizione in spedizioni:
        riga = [estrai(spedizione) for _, estrai in COLONNE]
        if collegamenti:
            link = collegamenti(spedizione) or {}
            riga += [link.get("gls") or "", link.get("shopify") or ""]
        righe.append(riga)
    return intestazioni, righe
