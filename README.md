# GLS Exception Monitor v2.9 - Zuiki

Monitor operativo locale per le sole spedizioni GLS presenti negli ordini Shopify. Tracking GLS, giacenze, svincoli API, note, esiti finali e incongruenze sono conservati nello stesso database.




## Novita v2.9 - tracking GLS senza eventi gestibile come pratica

- Un tracking GLS che resta senza eventi oltre la finestra fisiologica non viene piu presentato come errore tecnico generico: diventa una **pratica operativa associata al relativo ordine Shopify**.
- La riga conserva ordine, cliente, telefono, fulfillment e link Shopify anche quando GLS non ha mai pubblicato una scansione.
- Il caso entra in **DA VERIFICARE** e dal pannello **Gestisci** l'operatore puo lasciarlo aperto oppure impostarlo su **CHIUSA** con una nota.
- Se viene chiuso manualmente, la stessa identica assenza di eventi alle sincronizzazioni successive non lo riapre; un futuro nuovo evento GLS puo invece far evolvere normalmente la spedizione.
- Eventuali record `NO_EVENTS` lasciati in `sync_errors` da versioni precedenti sono esclusi dalla diagnostica tecnica: il banner giallo resta riservato a errori reali di rete/API/parser.

## Novita v2.8 - tracking creati ma non ancora ritirati

La v2.8 distingue finalmente un vero errore tecnico da una spedizione appena creata che GLS non ha ancora preso in carico.

- Se Shopify ha gia un tracking GLS ma GLS non pubblica ancora eventi **nel giorno di creazione del fulfillment**, la spedizione viene classificata **In attesa di presa in carico GLS**: non e un errore e non compare nel banner tecnico.
- La stessa tolleranza resta valida fino alle **12:00 del primo giorno lavorativo successivo**. Sabato e domenica vengono saltati automaticamente.
- Se oltre quella soglia GLS continua a non pubblicare alcun evento, la spedizione passa in **DA VERIFICARE** con categoria `NO_GLS_EVENTS`: e un problema operativo da controllare (es. etichetta/indirizzo non valido o spedizione non correttamente acquisita).
- Gli errori tecnici reali restano separati: timeout, HTTP 429/5xx, DNS/rete, blocchi o parser.
- La sincronizzazione mostra separatamente: **aggiornate**, **in attesa ritiro**, **senza eventi da verificare**, **errori tecnici**.
- Vengono salvati anche `fulfillment_created_at` e `fulfillment_updated_at` di Shopify, cosi la logica usa la data reale di creazione della spedizione e non soltanto la data ordine.

Questa modifica evita il falso allarme visto sui tracking creati e chiusi in bolla ma non ancora fisicamente ritirati da GLS.

## Novita v2.7 - chiusura operatore coerente con le liste

- Se un operatore imposta una pratica su **CHIUSA**, tutte le incongruenze gia note vengono marcate risolte e la spedizione sparisce subito da **DA VERIFICARE** e **INCONGRUENZE**.
- La chiusura manuale resta valida finche GLS non produce una **nuova revisione reale del tracking**. Una semplice risincronizzazione dello stesso stato non riapre la pratica.
- Se dopo la chiusura arriva un nuovo stato GLS problematico, la pratica puo riaprirsi automaticamente in **DA VERIFICARE**.
- Gli esiti finali puliti vengono chiusi automaticamente: `DELIVERED` va in **Consegnati**, `RETURN` va in **Rientrati**. Entrambi sono tab finali/chiusi.
- Se un esito finale genera una vera incongruenza (per esempio riconsegna richiesta ma GLS rientra al mittente), resta in **INCONGRUENZE** finche l'operatore non la marca risolta. Dopo la risoluzione resta soltanto nel corretto tab finale.
- Al primo avvio vengono ripulite anche le incongruenze storiche ancora attive su pratiche che nelle versioni precedenti erano gia state marcate **CHIUSA**.

## Novita v2.6 - sincronizzazione incrementale e diagnostica errori

- Dopo il primo popolamento il monitor **non interroga piu GLS per le spedizioni finali** (`DELIVERED` e `RETURN`). Restano visibili nei tab storici, ma vengono escluse dalle chiamate di tracking.
- Continua invece ad aggiornare tutte le spedizioni aperte, comprese quelle piu vecchie della finestra Shopify gia memorizzate nel database.
- La ricerca Shopify usa `updated_at`, cosi intercetta anche fulfillment/tracking aggiunti oggi a ordini creati in precedenza.
- Ogni errore tecnico di tracking viene ora salvato con tracking coinvolto, categoria, messaggio reale, numero di tentativi e suggerimento operativo.
- Il banner giallo mostra un riepilogo apribile **Capisci il problema e come risolverlo**.
- Il fallback pubblico GLS e protetto da limite di concorrenza e retry con backoff per timeout, 429 e 5xx.
- Se l'endpoint XML legacy non risolve via DNS, viene temporaneamente messo in pausa invece di ripetere lo stesso errore per ogni spedizione.

## Novita v2.5

### Code operative piu semplici

- **DA VERIFICARE** riunisce le precedenti code urgente/da verificare.
- **IN LAVORAZIONE** contiene le pratiche prese in carico dal team.
- **CONSEGNATI** (verde) contiene soltanto le spedizioni con esito finale consegnato al cliente.
- **RIENTRATI** contiene gli esiti di rientro/restituzione al mittente e non viene confuso con una consegna al cliente.
- **INCONGRUENZE** contiene i casi in cui tracking GLS e istruzioni operative non sono logicamente coerenti.
- **STORICO GIACENZE** conserva gli episodi di giacenza anche dopo la loro chiusura.

Quando un operatore aggiorna una pratica puo lasciarla **IN LAVORAZIONE** oppure **CHIUSA**. Un nuovo evento GLS anomalo o una nuova incongruenza puo riaprire automaticamente la pratica in **DA VERIFICARE**.

### Tabelle e UI

- Tutte le colonne informative delle tabelle sono ordinabili crescente/decrescente cliccando sull'intestazione.
- Corretto l'allineamento tra intestazioni e celle: la barra colore di priorita non genera piu una cella fantasma.
- Tracking GLS piu grande nel pannello Gestisci.
- Pulsante **Contatta cliente** apre Spoki cercando dinamicamente il numero del cliente.
- Accanto a **Ordine Shopify** e disponibile **Profilo Shopify**.
- **Tracking GLS · Incognito** apre la ricerca spedizione ufficiale GLS in una nuova finestra Chrome in incognito su macOS. L'apertura incognito e effettuata dal backend locale, non da semplice JavaScript.

### Registro unico: GLS + team

Il dettaglio spedizione mostra un'unica timeline cronologica che unisce:

- eventi GLS;
- messaggi/chiamate cliente;
- contatti con GLS;
- note operatore;
- svincoli;
- cambi di stato pratica.

Le azioni rapide sono state aggiornate in:

- **SVINCOLO Ritenta consegna**
- **SVINCOLO Ritorno al mittente**

Lo storico resta in SQLite e non viene cancellato dai successivi aggiornamenti GLS.

### Incongruenze automatiche

Le incongruenze hanno sempre **priorita massima** e riportano la pratica in DA VERIFICARE. La v2.5 controlla, tra gli altri, questi casi:

- tracking in giacenza senza episodio di giacenza coerente nel monitor;
- tentativo di svincolo via API non accettato come giacenza disponibile/gestibile;
- svincolo per ritentare la consegna seguito invece da rientro al mittente;
- richiesta di rientro al mittente seguita invece da consegna al cliente;
- svincolo accettato da GLS ma ancora fermo in giacenza senza nuovi eventi;
- data di riconsegna richiesta superata senza movimento coerente;
- indirizzo errato / destinatario assente / rifiuto / problema COD / altro blocco operativo fermo da almeno 24 ore senza evolvere in giacenza o in un esito finale.

La scheda **INCONGRUENZE** contiene il pulsante **Scarica Excel per GLS**. Il file `.xlsx` riporta ordine, tracking, cliente, problema, stato GLS, ultima attivita del team, operatore e suggerimento operativo.

> Limite GLS: nella documentazione pubblica usata dal progetto non risulta un endpoint separato che restituisca una lista ufficiale delle "giacenze gestibili". Per questo il monitor rileva la mancata coerenza dal tracking e registra come anomalia massima anche un eventuale rifiuto di `ReleaseShipmentStock` che indica giacenza non presente/non gestibile.

### Gestione giacenza via API GLS

La gestione continua a usare `ReleaseShipmentStock`. Per ogni invio vengono memorizzati operatore, timestamp, istruzione, parametri, nota e risposta GLS.

- **Preavviso telefonico** e selezionato di default.
- Il telefono destinatario e precompilato con il numero Shopify in formato nazionale, rimuovendo `+39`, `0039` o il `39` internazionale quando riconoscibile.
- Le operazioni distruttive richiedono una conferma aggiuntiva.

## Accesso online

Il monitor puo' girare anche su internet, indipendente dal Mac, usando
**Supabase** come database e **Vercel** per servire l'applicazione. La procedura
completa e' in **[DEPLOY.md](DEPLOY.md)**.

In sintesi:

- senza `DATABASE_URL` il programma resta quello locale di sempre: SQLite,
  `run.command`, nessuna dipendenza esterna;
- con `DATABASE_URL` valorizzata usa Postgres/Supabase e la stessa memoria
  condivisa e' raggiungibile da qualsiasi postazione;
- `migrate_to_postgres.py` porta online lo storico gia' presente sul Mac
  (note, giacenze, svincoli, incongruenze), senza duplicare nulla;
- online l'accesso richiede sempre utente e password, altrimenti il monitor
  non mostra nulla;
- la sincronizzazione periodica viene richiamata da un cron esterno su
  `/api/cron/sync`, protetto da `CRON_SECRET`.

## Migrazione dati precedenti

Al primo avvio `run.command` cerca automaticamente un `data/monitor.sqlite3` valido nelle precedenti cartelle `GLS_Exception_Monitor*` presenti accanto alla v2.9 e ne importa lo storico.

Vengono mantenuti note, workflow, azioni operatori, eventi GLS, giacenze, svincoli e classificazioni personalizzate. Se la vecchia cartella e altrove usa `IMPORTA_DATI_PRECEDENTI.command`.

## Avvio su Mac

1. Ferma la vecchia versione con `Control+C` nel Terminale.
2. Decomprimi la cartella v2.9 preferibilmente accanto alla versione precedente.
3. Fai doppio clic su `run.command`.
4. Se macOS blocca il file: tasto destro > **Apri** > **Apri**.
5. Il browser si apre su `http://127.0.0.1:8787`.
6. Lascia aperto Terminale mentre il monitor e in uso.

Per l'apertura automatica del tracking in incognito e richiesto **Google Chrome su macOS**.

## Piu operatori

La memoria condivisa risiede in `data/monitor.sqlite3`. Per vedere in tempo reale lo stesso storico, tutti gli operatori devono usare la stessa istanza del monitor su un unico PC/NAS/server aziendale. Copie locali separate producono database separati.

## Sicurezza

La build REAL contiene il file `.env` con configurazione riservata. Non condividere cartella o ZIP. `.env` e i database sono esclusi da Git tramite `.gitignore`.

## Test

La v2.8 include test per classificazione, solo GLS, memoria operatore, giacenze, `ReleaseShipmentStock`, rientri/consegne, incongruenze e generazione Excel.

```bash
for f in tests/test_*.py; do PYTHONPATH=. python3 "$f"; done
```

I test del backend Postgres girano solo se indichi un database di prova, altrimenti
vengono saltati:

```bash
TEST_DATABASE_URL="postgresql://utente:password@host:5432/db" \
  PYTHONPATH=. python3 tests/test_postgres_backend.py
```
