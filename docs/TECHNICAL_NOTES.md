# Note tecniche v2.8

## Architettura

- Python standard library HTTP server.
- SQLite locale (`data/monitor.sqlite3`).
- Shopify Admin GraphQL per ordini, fulfillment, tracking e profilo cliente.
- GLS Track & Trace per eventi.
- GLS `ReleaseShipmentStock` per disposizioni di giacenza.
- UI HTML/CSS/JavaScript senza framework.

## Classificazione finale

`DELIVERED` significa consegnato al destinatario ed entra nel tab CONSEGNATI.
`RETURN` significa rientro/restituzione/consegna al mittente ed entra nel tab RIENTRATI. Le regole di DELIVERED escludono esplicitamente i testi che menzionano mittente/rientro/restituzione per ridurre i falsi positivi.

## Workflow interno

Nell'UI operativa vengono usati principalmente:

- `NEW` = DA VERIFICARE
- `IN_PROGRESS` = IN LAVORAZIONE
- `RESOLVED` = CHIUSA

Valori legacy come `WAITING_GLS` e `WAITING_CUSTOMER` restano leggibili per compatibilita con database precedenti e vengono visualizzati come IN LAVORAZIONE.

Una nuova anomalia GLS WARNING/CRITICAL o un'incongruenza attiva puo riportare una pratica da RESOLVED a NEW.

## Incongruenze

Tabella SQLite `inconsistencies` con chiave logica per tracking + issue. Gli issue risolti vengono conservati con `active=0` e `resolved_at`.

Regole principali:

- `STORAGE_WITHOUT_CASE`
- `STOCK_NOT_MANAGEABLE`
- `REDELIVERY_BECAME_RETURN`
- `RETURN_BECAME_DELIVERY`
- `RELEASE_NOT_ACKNOWLEDGED`
- `REDELIVERY_DATE_MISSED`
- `BLOCKED_NO_STOCK`

Le incongruenze attive forzano `effective_severity=CRITICAL` e riaprono il workflow se era chiuso.

## Export XLSX

`core/xlsx_export.py` crea un `.xlsx` OOXML direttamente con la standard library, senza dipendenze Python aggiuntive. Endpoint: `/api/inconsistencies/export.xlsx`.

## Tracking incognito

Endpoint locale `/api/open-incognito`: costruisce esclusivamente l'URL GLS ufficiale con il tracking ricevuto e, su macOS, esegue Chrome con `--incognito`. Non accetta un URL arbitrario dal browser.

## Telefono giacenza

`core/gls.py` normalizza il telefono destinatario rimuovendo `0039`, `+39` oppure `39` quando e chiaramente il prefisso internazionale, prima di costruire la richiesta GLS.

## Limite lista giacenze gestibili

Non e implementata una prelettura di una lista ufficiale GLS delle giacenze "gestibili", perche tale endpoint non e documentato nel set pubblico utilizzato dal progetto. Il sistema segnala invece:

1. giacenza nel tracking senza episodio coerente interno;
2. fallimento di `ReleaseShipmentStock` riconducibile a giacenza non presente/non gestibile;
3. mancata evoluzione successiva allo svincolo.


## Sincronizzazione incrementale v2.6

La fase Shopify rimane una discovery leggera, ma la fase costosa GLS lavora solo su:

1. tracking GLS nuovi;
2. tracking gia noti con `closed=0`;
3. tracking aperti storici presenti nel DB anche se non rientrano piu nella finestra Shopify.

Le righe con `closed=1` (`DELIVERED` o `RETURN`) vengono saltate e conteggiate in `sync_runs.skipped_closed`.
La discovery Shopify filtra gli ordini per `updated_at`, non piu solo per data creazione, per intercettare fulfillment tardivi.

## Errori tecnici tracking

Nuova tabella `sync_errors`. Ogni fallimento memorizza `tracking_number`, `error_code`, titolo, messaggio reale, hint di risoluzione e numero di tentativi. Le categorie includono `RATE_LIMIT`, `TIMEOUT`, `GLS_TEMPORARY`, `GLS_BLOCKED`, `NO_EVENTS`, `PARSER`, `DNS`, `NETWORK`, `UNKNOWN`.

Gli errori tecnici non sovrascrivono mai lo stato logistico precedente della spedizione.
Il fallback pubblico e serializzato con concorrenza configurabile (`GLS_PUBLIC_WORKERS`, default 2) e retry (`GLS_RETRY_ATTEMPTS`, default 3).


## Workflow v2.7: chiusura manuale e revisione tracking

Ogni spedizione mantiene `tracking_revision`. Quando cambia codice/stato/nota/timestamp dell'ultimo evento GLS, la revisione aumenta. Quando l'operatore salva **CHIUSA**, `manual_closed_revision` memorizza la revisione corrente e le incongruenze attive vengono risolte. Le successive sincronizzazioni dello stesso stato non riaprono la pratica. Solo una revisione GLS successiva puo generare nuove incongruenze e riportare la pratica in `NEW`.

Gli stati finali sono distinti: `DELIVERED` -> Consegnati, `RETURN` -> Rientrati. Entrambi hanno `closed=1` e non vengono piu interrogati nelle sincronizzazioni incrementali. Se il passaggio allo stato finale genera un'incongruenza, il workflow resta `NEW` finche un operatore non la risolve; dopo la risoluzione la spedizione resta nel proprio tab finale senza comparire nelle liste operative.


## v2.8 - NO_EVENTS non e sempre un errore

Quando il fallback GLS risponde senza eventi, il motore usa `fulfillment_created_at` Shopify:

- entro il giorno di creazione e fino alle 12:00 del primo giorno lavorativo successivo -> `PENDING_PICKUP`, severity `INFO`;
- oltre la soglia -> `NO_GLS_EVENTS`, severity `WARNING`, pratica in `DA VERIFICARE`;
- `NO_EVENTS` non incrementa piu `gls_errors`; i contatori dedicati sono `pending_pickup` e `no_event_attention`.

In questo modo il banner degli errori tecnici rappresenta soltanto problemi di rete/servizio/parser e non le spedizioni create ma non ancora scansionate fisicamente da GLS.


## v2.9 - NO_EVENTS come pratica Shopify chiudibile

`NO_GLS_EVENTS` e' una condizione operativa, non un errore tecnico. `_save_no_events()` preserva/integra i metadati Shopify della spedizione e crea una riga WARNING in DA VERIFICARE. L'operatore puo chiuderla tramite workflow `RESOLVED`; la stessa assenza di eventi non riapre la pratica. `latest_sync()` filtra gli eventuali vecchi `sync_errors.error_code='NO_EVENTS'` dal conteggio tecnico e dal banner diagnostico.
