from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any

from .classifier import Classifier
from .db import Database
from .gls import GLSClient
from .shopify import ShopifyClient


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

from .timezones import ROME  # noqa: E402


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def no_events_pickup_state(shipment: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Classifica un tracking GLS senza eventi come attesa fisiologica o anomalia operativa.

    Regola Zuiki:
    - il giorno di creazione del fulfillment: attesa fisiologica di ritiro/presa in carico;
    - fino alle 12:00 del primo giorno lavorativo successivo: ancora fisiologico;
    - oltre tale soglia: va in DA VERIFICARE, perche GLS avrebbe dovuto pubblicare almeno un evento.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(ROME)
    created = _parse_iso(shipment.get("fulfillment_created_at") or shipment.get("order_created_at"))
    if created is None:
        return {
            "pending": False,
            "created_at": None,
            "deadline": None,
            "reason": "Data di creazione spedizione non disponibile",
        }
    created_local = created.astimezone(ROME)
    next_day = created_local.date() + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    deadline = datetime.combine(next_day, dt_time(hour=12, minute=0), tzinfo=ROME)
    pending = now <= deadline
    return {
        "pending": pending,
        "created_at": created_local.isoformat(timespec="minutes"),
        "deadline": deadline.isoformat(timespec="minutes"),
        "reason": (
            "Tracking appena creato: GLS non ha ancora pubblicato eventi e il collo puo essere ancora in magazzino/in attesa di ritiro."
            if pending else
            "GLS non ha pubblicato alcun evento entro la finestra fisiologica di presa in carico."
        ),
    }


def select_sync_batch(
    targets: list[dict[str, Any]], limit: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Sceglie quante spedizioni interrogare in questa esecuzione.

    Dove la richiesta ha un tempo massimo (deploy serverless) non si possono
    interrogare tutte le spedizioni in un colpo solo. L'ordine e' per ultima
    interrogazione GLS crescente: prima chi non e' mai stato controllato
    (`gls_checked_at` vuoto), poi chi lo e' stato meno di recente. Cosi su piu'
    esecuzioni il turno tocca a tutte, invece di ripassare sempre le stesse.

    Restituisce il lotto da elaborare e l'elenco delle spedizioni rinviate, che
    va comunque registrato per non perderle.
    """
    if limit <= 0 or len(targets) <= limit:
        return targets, []
    ordered = sorted(targets, key=lambda s: str(s.get("gls_checked_at") or ""))
    return ordered[:limit], ordered[limit:]


def technical_error_info(message: str) -> dict[str, Any]:
    raw = str(message or "")
    lower = raw.lower()
    primary = lower.split("fallback pubblico:", 1)[1] if "fallback pubblico:" in lower else lower
    attempts = 1
    m = re.search(r"dopo\s+(\d+)\s+tentativo", lower)
    if m:
        try:
            attempts = max(1, int(m.group(1)))
        except Exception:
            attempts = 1

    if any(x in primary for x in ["http 429", "too many requests"]):
        code = "RATE_LIMIT"
        title = "GLS sta limitando le richieste"
        hint = "Il monitor riduce automaticamente la concorrenza e riprova con attesa progressiva. Se persiste, attendere alcuni minuti e rilanciare la sincronizzazione."
    elif any(x in primary for x in ["timed out", "timeout"]):
        code = "TIMEOUT"
        title = "GLS non ha risposto in tempo"
        hint = "Il monitor riprova automaticamente. Se l'errore persiste, verificare la connessione internet e riprovare piu tardi."
    elif any(x in primary for x in ["http 500", "http 502", "http 503", "http 504"]):
        code = "GLS_TEMPORARY"
        title = "Servizio GLS temporaneamente non disponibile"
        hint = "Errore lato GLS. Il monitor effettua retry automatici; se persiste non serve modificare la pratica, basta riprovare la sincronizzazione."
    elif "http 403" in primary:
        code = "GLS_BLOCKED"
        title = "Accesso al tracking pubblico GLS temporaneamente bloccato"
        hint = "Il fallback pubblico puo bloccare raffiche di richieste. La v2.6 limita le chiamate contemporanee; riprovare dopo qualche minuto se il blocco persiste."
    elif any(x in primary for x in ["non ha restituito eventi", "non ha restituito dati", "spedizione valida"]):
        code = "NO_EVENTS"
        title = "GLS non restituisce eventi per questo tracking"
        hint = "Puo accadere per tracking molto vecchi, non ancora pubblicati o non piu disponibili. Le spedizioni gia chiuse vengono ora escluse dalle sincronizzazioni successive."
    elif any(x in primary for x in ["pagina pubblica gls non interpretabile", "xml tracking gls non valido"]):
        code = "PARSER"
        title = "Formato della risposta GLS non riconosciuto"
        hint = "GLS potrebbe aver modificato la pagina o il formato XML. Il dettaglio dell'errore resta registrato per poter aggiornare il parser senza perdere lo stato precedente."
    elif any(x in primary for x in ["nodename nor servname", "name or service not known", "temporary failure in name resolution", "errno 8", "getaddrinfo failed"]):
        code = "DNS"
        title = "Endpoint GLS non raggiungibile via DNS"
        hint = "Il monitor disattiva temporaneamente l'endpoint XML non raggiungibile e usa il fallback pubblico. Se falliscono entrambi, verificare DNS/rete del Mac."
    elif any(x in primary for x in ["errore rete gls", "connection reset", "remote end closed"]):
        code = "NETWORK"
        title = "Errore di rete verso GLS"
        hint = "Il monitor riprova gli errori transitori. Se persiste, verificare rete/VPN/firewall e rilanciare la sincronizzazione."
    else:
        code = "UNKNOWN"
        title = "Errore tecnico GLS non classificato"
        hint = "Aprire i dettagli dell'errore: il monitor conserva il messaggio reale e il tracking coinvolto. Lo stato spedizione precedente resta invariato."
    return {
        "code": code, "title": title, "hint": hint, "attempts": attempts, "message": raw,
    }


class SyncEngine:
    def __init__(self, config, db: Database):
        self.config = config
        self.db = db
        self.shopify = ShopifyClient(config)
        self.gls = GLSClient(config)
        self.classifier = Classifier(config.rules_path, db)
        self.lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._progress_lock = threading.Lock()
        self._progress: dict[str, Any] = {
            "running": False,
            "phase": "IDLE",
            "label": "Pronto",
            "processed": 0,
            "total": 0,
            "percent": 0,
            "success": 0,
            "errors": 0,
            "eta_seconds": None,
            "current_tracking": None,
            "pending_pickup": 0,
            "no_event_attention": 0,
            "started_at": None,
            "updated_at": utcnow(),
        }

    @property
    def running(self) -> bool:
        return self.lock.locked()

    def get_progress(self) -> dict[str, Any]:
        with self._progress_lock:
            return dict(self._progress)

    def _set_progress(self, **updates: Any) -> None:
        with self._progress_lock:
            self._progress.update(updates)
            self._progress["updated_at"] = utcnow()

    def _begin_progress(self) -> None:
        self._set_progress(
            running=True,
            phase="STARTING",
            label="Avvio sincronizzazione…",
            processed=0,
            total=0,
            percent=0,
            success=0,
            errors=0,
            eta_seconds=None,
            current_tracking=None,
            pending_pickup=0,
            no_event_attention=0,
            started_at=utcnow(),
        )

    def start_background_scheduler(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._scheduler, name="sync-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _scheduler(self) -> None:
        self._stop.wait(2)
        while not self._stop.is_set():
            try:
                if self.config.mock_mode or (self.config.shopify_configured and self.config.gls_tracking_configured):
                    self.run_sync()
            except Exception:
                pass
            self._stop.wait(self.config.sync_interval_minutes * 60)

    def run_sync_async(self) -> bool:
        if self.running:
            return False
        thread = threading.Thread(target=self.run_sync, name="manual-sync", daemon=True)
        thread.start()
        return True

    def run_sync(self) -> dict[str, Any]:
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "message": "Sincronizzazione gia in corso"}

        self._begin_progress()
        sync_started_monotonic = time.monotonic()
        # Una sincronizzazione interrotta a meta' resterebbe RUNNING per sempre.
        self.db.close_stale_syncs()
        sync_id = self.db.start_sync()
        shopify_orders = 0
        discovered_tracking = 0
        tracking_numbers = 0
        skipped_closed = 0
        carried_open = 0
        gls_success = 0
        gls_errors = 0
        pending_pickup = 0
        no_event_attention = 0

        try:
            if self.config.mock_mode:
                self._set_progress(phase="DEMO", label="Caricamento dati demo…")
                stats = self._sync_mock()
                self.db.finish_sync(sync_id, "OK", discovered_tracking=stats["tracking_numbers"], **stats, message="Modalita demo")
                self._set_progress(
                    running=False, phase="DONE", label="Sincronizzazione completata",
                    processed=stats["tracking_numbers"], total=stats["tracking_numbers"], percent=100,
                    success=stats["gls_success"], errors=0, eta_seconds=0, current_tracking=None,
                )
                return {"ok": True, **stats, "mock": True}

            if not self.config.shopify_configured:
                raise RuntimeError("Shopify non configurato: completa il file .env")
            if not self.config.gls_tracking_configured:
                raise RuntimeError("GLS Track & Trace non configurato: completa il file .env")

            self._set_progress(phase="SHOPIFY", label="Cerco nuove spedizioni GLS su Shopify…")
            orders = self.shopify.list_recent_orders(self._shopify_since())
            shopify_orders = len(orders)

            self._set_progress(phase="FILTERING", label=f"Preparo l'aggiornamento tra {shopify_orders} ordini Shopify…")
            fresh_shipments = self.shopify.extract_gls_shipments(orders)
            discovered_tracking = len(fresh_shipments)
            existing = self.db.tracking_state_map()

            # Aggiornamento incrementale:
            # - tracking nuovo -> sempre interrogato
            # - tracking gia noto ma non finale -> interrogato
            # - tracking finale (consegnato al cliente / rientrato al mittente) -> saltato
            # - tracking aperto piu vecchio del lookback Shopify -> continua a essere interrogato dal DB locale
            targets: list[dict[str, Any]] = []
            target_numbers: set[str] = set()
            fresh_numbers: set[str] = set()
            for shipment in fresh_shipments:
                tracking = str(shipment.get("tracking_number") or "")
                if not tracking:
                    continue
                fresh_numbers.add(tracking)
                state = existing.get(tracking)
                if state and bool(state.get("closed")):
                    skipped_closed += 1
                    continue
                targets.append(shipment)
                target_numbers.add(tracking)

            for stored in self.db.list_open_shipments_for_sync():
                tracking = str(stored.get("tracking_number") or "")
                if not tracking or tracking in target_numbers:
                    continue
                # Se non compare piu nella finestra Shopify ma non ha ancora un esito finale,
                # rimane attivo e continua a essere monitorato.
                targets.append(stored)
                target_numbers.add(tracking)
                if tracking not in fresh_numbers:
                    carried_open += 1

            targets, rimandate = select_sync_batch(targets, getattr(self.config, "sync_max_tracking", 0))
            budget = getattr(self.config, "sync_time_budget_seconds", 0)
            # Registrate subito: una spedizione rinviata e mai salvata sparirebbe,
            # perche' la ricerca incrementale su Shopify non la ritroverebbe.
            self.db.register_pending_shipments(rimandate)
            deferred = len(rimandate)
            tracking_numbers = len(targets)

            if tracking_numbers == 0:
                msg = f"Nessuna spedizione aperta da aggiornare · {skipped_closed} finali escluse"
                self.db.finish_sync(
                    sync_id, "OK", shopify_orders=shopify_orders, discovered_tracking=discovered_tracking,
                    tracking_numbers=0, skipped_closed=skipped_closed, carried_open=carried_open,
                    gls_success=0, gls_errors=0, pending_pickup=0, no_event_attention=0, message=msg,
                )
                self._set_progress(
                    running=False, phase="DONE", label=msg, processed=0, total=0, percent=100,
                    success=0, errors=0, eta_seconds=0, current_tracking=None,
                )
                return {
                    "ok": True, "shopify_orders": shopify_orders, "discovered_tracking": discovered_tracking,
                    "tracking_numbers": 0, "skipped_closed": skipped_closed, "carried_open": carried_open,
                    "gls_success": 0, "gls_errors": 0,
                }

            suffix = f" · {skipped_closed} finali escluse" if skipped_closed else ""
            self._set_progress(
                phase="GLS", label=f"Aggiorno 0 di {tracking_numbers} spedizioni aperte{suffix}",
                total=tracking_numbers, processed=0, percent=0,
            )

            workers = min(self.config.sync_workers, tracking_numbers)
            started_gls = time.monotonic()
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="gls-track") as pool:
                future_map = {
                    pool.submit(self.gls.track, shipment["tracking_number"]): shipment
                    for shipment in targets
                }

                processed = 0
                for future in as_completed(future_map):
                    # Oltre il budget si chiude ordinatamente: una sincronizzazione
                    # interrotta d'autorita' lascerebbe la pratica aperta per sempre
                    # e nessun conteggio salvato.
                    if budget and (time.monotonic() - sync_started_monotonic) > budget:
                        annullate = [
                            spedizione for rimasto, spedizione in future_map.items()
                            if rimasto.cancel()
                        ]
                        self.db.register_pending_shipments(annullate)
                        deferred += len(annullate)
                        break
                    shipment = future_map[future]
                    self._set_progress(current_tracking=shipment.get("tracking_number"))
                    try:
                        tracked = future.result()
                        self._save_tracking(shipment, tracked)
                        gls_success += 1
                    except Exception as exc:
                        info = technical_error_info(str(exc))
                        if info["code"] == "NO_EVENTS":
                            outcome = self._save_no_events(shipment)
                            if outcome == "PENDING_PICKUP":
                                pending_pickup += 1
                            else:
                                no_event_attention += 1
                        else:
                            gls_errors += 1
                            self._save_gls_error(sync_id, shipment, str(exc))

                    processed += 1
                    elapsed = max(0.01, time.monotonic() - started_gls)
                    rate = processed / elapsed
                    remaining = max(0, tracking_numbers - processed)
                    eta = int(round(remaining / rate)) if rate > 0 else None
                    percent = int(round(processed * 100 / tracking_numbers))
                    self._set_progress(
                        phase="GLS",
                        label=f"Aggiorno {processed} di {tracking_numbers} spedizioni aperte{suffix}",
                        processed=processed, total=tracking_numbers, percent=min(100, percent),
                        success=gls_success, errors=gls_errors, pending_pickup=pending_pickup,
                        no_event_attention=no_event_attention, eta_seconds=eta,
                    )

            status = "OK" if gls_errors == 0 else ("PARTIAL" if (gls_success or pending_pickup or no_event_attention) else "ERROR")
            msg = f"{gls_success} aggiornate · {pending_pickup} in attesa ritiro · {no_event_attention} senza eventi da verificare · {gls_errors} errori tecnici · {skipped_closed} finali escluse"
            if carried_open:
                msg += f" · {carried_open} aperte storiche mantenute"
            if deferred:
                msg += f" · {deferred} rinviate al prossimo giro"
            if budget and (time.monotonic() - sync_started_monotonic) > budget:
                msg += " (tempo massimo raggiunto)"
            self.db.finish_sync(
                sync_id, status, shopify_orders=shopify_orders, discovered_tracking=discovered_tracking,
                tracking_numbers=tracking_numbers, skipped_closed=skipped_closed, carried_open=carried_open,
                gls_success=gls_success, gls_errors=gls_errors, pending_pickup=pending_pickup,
                no_event_attention=no_event_attention, message=msg,
            )
            duration = int(round(time.monotonic() - sync_started_monotonic))
            self._set_progress(
                running=False, phase="DONE", label=f"Sincronizzazione completata in {duration}s",
                processed=tracking_numbers, total=tracking_numbers, percent=100, success=gls_success,
                errors=gls_errors, pending_pickup=pending_pickup, no_event_attention=no_event_attention,
                eta_seconds=0, current_tracking=None,
            )
            return {
                "ok": True, "shopify_orders": shopify_orders, "discovered_tracking": discovered_tracking,
                "tracking_numbers": tracking_numbers, "skipped_closed": skipped_closed, "carried_open": carried_open,
                "gls_success": gls_success, "gls_errors": gls_errors,
                "pending_pickup": pending_pickup, "no_event_attention": no_event_attention,
                "deferred": deferred,
            }
        except Exception as exc:
            self.db.finish_sync(
                sync_id, "ERROR", shopify_orders=shopify_orders, discovered_tracking=discovered_tracking,
                tracking_numbers=tracking_numbers, skipped_closed=skipped_closed, carried_open=carried_open,
                gls_success=gls_success, gls_errors=gls_errors, pending_pickup=pending_pickup,
                no_event_attention=no_event_attention, message=str(exc),
            )
            self._set_progress(
                running=False, phase="ERROR", label=f"Sincronizzazione interrotta: {exc}",
                success=gls_success, errors=gls_errors, eta_seconds=None, current_tracking=None,
            )
            return {"ok": False, "message": str(exc)}
        finally:
            self.lock.release()

    def _shopify_since(self) -> str | None:
        """Da quando ricercare su Shopify.

        Dalla seconda sincronizzazione in poi basta cio' che e' cambiato
        dall'ultima, con un margine di sovrapposizione che assorbe ritardi di
        indicizzazione e orologi non allineati. None significa finestra intera:
        e' il caso del primo popolamento.
        """
        # E' solo un'ottimizzazione: qualunque intoppo qui deve ricadere sulla
        # finestra intera, che resta corretta, non far fallire l'intera
        # sincronizzazione.
        try:
            if getattr(self.config, "shopify_full_scan", False):
                return None
            ultima = self.db.last_successful_sync_at()
            if not ultima:
                return None
            momento = _parse_iso(ultima)
            if momento is None:
                return None
            margine = max(0, getattr(self.config, "shopify_overlap_minutes", 60))
            inizio = momento - timedelta(minutes=margine)
            # Oltre la finestra configurata si torna alla ricerca completa:
            # significa che il monitor e' rimasto fermo a lungo.
            giorni = getattr(self.config, "shopify_lookback_days", 21)
            limite = datetime.now(timezone.utc) - timedelta(days=giorni)
            if inizio <= limite:
                return None
            return inizio.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return None

    def _save_tracking(self, shipment: dict[str, Any], tracked: dict[str, Any]) -> None:
        # gls_checked_at viene impostato da upsert_shipment: e' il campo che
        # determina il turno nella coda delle prossime esecuzioni.
        current = tracked.get("current_event") or {}
        classification = self.classifier.classify(
            code=current.get("code") or tracked.get("code"),
            state=current.get("state") or tracked.get("status"),
            note=current.get("note") or tracked.get("note"),
            event_at=current.get("event_at"),
            is_cod=bool(shipment.get("is_cod")),
        )
        status_text = current.get("state") or tracked.get("status") or ""
        note_text = current.get("note") or tracked.get("note") or ""
        closed = classification.category in {"DELIVERED", "RETURN"}
        row = {
            **shipment,
            "gls_status": status_text,
            "gls_note": note_text,
            "gls_code": current.get("code") or tracked.get("code") or "",
            "gls_location": current.get("location") or "",
            "gls_event_at": current.get("event_at"),
            "gls_destination_depot": tracked.get("destination_depot") or "",
            "gls_destination_city": tracked.get("destination_depot_city") or "",
            "gls_destination_phone": tracked.get("destination_depot_phone") or "",
            "severity": classification.severity,
            "category": classification.category,
            "reason": classification.reason,
            "recommended_action": classification.recommended_action,
            "closed": closed,
            "source": "shopify+gls",
        }
        self.db.upsert_shipment(row)

        new_event_added = False
        for event in tracked.get("events") or []:
            e_class = self.classifier.classify(
                code=event.get("code"),
                state=event.get("state"),
                note=event.get("note"),
                event_at=event.get("event_at"),
                is_cod=bool(shipment.get("is_cod")),
            )
            inserted = self.db.insert_event(
                {
                    "tracking_number": shipment["tracking_number"],
                    "event_hash": self.gls.event_hash(shipment["tracking_number"], event),
                    "event_at": event.get("event_at"),
                    "code": event.get("code"),
                    "state": event.get("state"),
                    "note": event.get("note"),
                    "location": event.get("location"),
                    "severity": e_class.severity,
                    "category": e_class.category,
                    "reason": e_class.reason,
                    "raw": event,
                }
            )
            new_event_added = new_event_added or inserted

        self.db.reconcile_stock_cases(shipment["tracking_number"])
        issues = self.db.reconcile_inconsistencies(shipment["tracking_number"])

        # Stati finali puliti vengono archiviati automaticamente nei tab dedicati.
        # Se l'esito finale genera un'incongruenza logica (es. rientro non richiesto),
        # la pratica resta DA VERIFICARE finche un operatore non la chiude esplicitamente.
        if classification.category in {"DELIVERED", "RETURN"}:
            self.db.update_workflow(
                shipment["tracking_number"],
                "NEW" if issues else "RESOLVED",
                None,
                "Sistema",
            )
        elif new_event_added and (classification.severity in {"WARNING", "CRITICAL"} or issues):
            self.db.update_workflow(shipment["tracking_number"], "NEW", None, "Sistema")

    def _save_no_events(self, shipment: dict[str, Any]) -> str:
        # Un tracking senza eventi resta una vera pratica Shopify: ordine, cliente e
        # fulfillment devono rimanere associati anche se GLS non ha mai pubblicato
        # una scansione. Se stiamo lavorando su una spedizione storica, completiamo
        # eventuali campi mancanti con quanto gia' presente nel DB.
        tracking = str(shipment.get("tracking_number") or "")
        existing = self.db.get_shipment(tracking)
        merged = dict(existing or {})
        for key, value in shipment.items():
            if value not in (None, "", [], {}):
                merged[key] = value
        merged["tracking_number"] = tracking
        state = no_events_pickup_state(merged)
        if state["pending"]:
            row = {
                **merged,
                "gls_status": "In attesa di presa in carico GLS",
                "gls_note": "Tracking creato; GLS non ha ancora pubblicato eventi. Probabile attesa di ritiro presso il magazzino.",
                "gls_code": "",
                "gls_location": "",
                "gls_event_at": None,
                "severity": "INFO",
                "category": "PENDING_PICKUP",
                "reason": state["reason"],
                "recommended_action": "Nessuna azione: attendere la prima scansione GLS.",
                "closed": False,
                "source": "shopify+gls",
            }
            self.db.upsert_shipment(row)
            return "PENDING_PICKUP"

        row = {
            **merged,
            "gls_status": "Tracking GLS senza eventi · verifica manuale",
            "gls_note": "GLS non ha pubblicato eventi per questo tracking oltre la finestra fisiologica. La pratica resta associata all'ordine Shopify e puo essere lasciata aperta oppure chiusa manualmente dall'operatore.",
            "gls_code": "",
            "gls_location": "",
            "gls_event_at": None,
            "severity": "WARNING",
            "category": "NO_GLS_EVENTS",
            "reason": state["reason"],
            "recommended_action": "Verificare il caso. Se e' un'anomalia nota/non recuperabile puoi impostare la pratica su CHIUSA; altrimenti lasciala aperta in DA VERIFICARE.",
            "closed": False,
            "source": "shopify+gls",
        }
        before_status = (existing or {}).get("gls_status")
        self.db.upsert_shipment(row)
        # La prima volta che diventa un caso operativo, entra in DA VERIFICARE.
        # Se l'operatore lo ha gia' chiuso e GLS continua a restituire esattamente
        # la stessa assenza di eventi, non lo riapriamo ad ogni sincronizzazione.
        if before_status != row["gls_status"]:
            self.db.update_workflow(tracking, "NEW", None, "Sistema")
        return "NO_EVENT_ATTENTION"

    def _save_gls_error(self, sync_id: int, shipment: dict[str, Any], message: str) -> None:
        # Un errore tecnico NON modifica lo stato logistico precedente. Viene pero'
        # registrato separatamente con causa e suggerimento, cosi l'operatore capisce
        # cosa e' successo senza trasformarlo in una falsa anomalia di spedizione.
        info = technical_error_info(message)
        self.db.record_sync_error(
            sync_id, str(shipment.get("tracking_number") or ""),
            error_code=info["code"], error_title=info["title"],
            error_message=info["message"], resolution_hint=info["hint"], attempts=info["attempts"],
        )

    def _sync_mock(self) -> dict[str, int]:
        path = self.config.mock_dir / "demo_shipments.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        total = len(payload)
        for idx, item in enumerate(payload, start=1):
            classification = self.classifier.classify(
                code=item.get("gls_code"),
                state=item.get("gls_status"),
                note=item.get("gls_note"),
                event_at=item.get("gls_event_at"),
                is_cod=bool(item.get("is_cod")),
            )
            item["severity"] = classification.severity
            item["category"] = classification.category
            item["reason"] = classification.reason
            item["recommended_action"] = classification.recommended_action
            item["closed"] = classification.category in {"DELIVERED", "RETURN"}
            item["source"] = "demo"
            self.db.upsert_shipment(item)
            event = {
                "event_at": item.get("gls_event_at"),
                "code": item.get("gls_code"),
                "state": item.get("gls_status"),
                "note": item.get("gls_note"),
                "location": item.get("gls_location"),
            }
            self.db.insert_event(
                {
                    "tracking_number": item["tracking_number"],
                    "event_hash": self.gls.event_hash(item["tracking_number"], event),
                    "event_at": event.get("event_at"),
                    "code": event.get("code"),
                    "state": event.get("state"),
                    "note": event.get("note"),
                    "location": event.get("location"),
                    "severity": classification.severity,
                    "category": classification.category,
                    "reason": classification.reason,
                    "raw": event,
                }
            )
            self.db.reconcile_stock_cases(item["tracking_number"])
            issues = self.db.reconcile_inconsistencies(item["tracking_number"])
            if classification.category in {"DELIVERED", "RETURN"}:
                self.db.update_workflow(
                    item["tracking_number"],
                    "NEW" if issues else "RESOLVED",
                    None,
                    "Sistema",
                )
            self._set_progress(
                phase="DEMO",
                label=f"Caricamento demo {idx} di {total}…",
                processed=idx,
                total=total,
                percent=int(round(idx * 100 / total)) if total else 100,
                success=idx,
            )
        return {
            "shopify_orders": total,
            "tracking_numbers": total,
            "gls_success": total,
            "gls_errors": 0,
        }
