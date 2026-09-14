# Mettere online il monitor (Vercel + Supabase)

Il monitor nasce come applicazione locale su Mac: server Python in ascolto su
`127.0.0.1` e database SQLite dentro la cartella `data/`. Per renderlo
raggiungibile da internet e indipendente dal computer servono due cose:

- **Supabase (Postgres)** al posto del file SQLite, perche' su un hosting
  serverless il disco non sopravvive tra una richiesta e l'altra;
- **Vercel** per servire l'applicazione.

Il codice supporta entrambe le modalita'. Senza `DATABASE_URL` resta esattamente
il programma locale di prima, con SQLite e senza dipendenze esterne.

---

## 1. Creare il database su Supabase

1. Su [supabase.com](https://supabase.com) crea un nuovo progetto e scegli una
   password per il database (annotala: serve subito dopo).
2. Scegli una regione europea, per esempio *Frankfurt*: i dati dei clienti
   restano nell'Unione Europea.
3. Apri **Connect** in alto e copia la stringa **Transaction pooler**. Ha questa
   forma:

   ```
   postgresql://postgres.abcdefghijklm:LA-TUA-PASSWORD@aws-0-eu-central-1.pooler.supabase.com:6543/postgres
   ```

   Sostituisci `[YOUR-PASSWORD]` con la password scelta al punto 1.

> Usa la stringa del **pooler** (porta `6543`), non quella diretta (`5432`):
> un'applicazione serverless apre e chiude molte connessioni brevi e il pooler
> e' fatto per questo. Il codice disattiva automaticamente i prepared statement,
> che il pooler in transaction mode non supporta.

Le tabelle non vanno create a mano: vengono generate al primo avvio.

---

## 2. Pubblicare su Vercel

1. Su [vercel.com](https://vercel.com) scegli **Add New → Project** e importa
   questo repository.
2. Lascia **Application Preset** su *Python*: l'applicazione è esposta come
   WSGI (`app` in `app.py`) ed è la forma che quel runtime si aspetta. Non
   servono build command, output directory né rewrite: con questo preset Vercel
   instrada da sé ogni richiesta all'applicazione.
3. Prima di premere **Deploy**, apri **Environment Variables** e inserisci i
   valori dell'elenco qui sotto.
4. Premi **Deploy**.

### Variabili d'ambiente

Sono le stesse del file `.env` locale, piu' quelle del deploy. **Non** committare
mai il `.env`: su Vercel i valori vivono solo nelle impostazioni del progetto.

| Variabile | Valore | Obbligatoria |
|---|---|---|
| `DATABASE_URL` | la stringa Transaction pooler di Supabase | sì |
| `DASHBOARD_USERS` | elenco operatori generato da `genera_utenti.py` | sì |
| `CRON_SECRET` | stringa casuale, protegge la sincronizzazione pianificata | sì |
| `SHOPIFY_SHOP` | sottodominio del negozio, senza `.myshopify.com` | sì |
| `SHOPIFY_CLIENT_ID` | client ID dell'app Shopify | sì |
| `SHOPIFY_CLIENT_SECRET` | client secret dell'app Shopify | sì |
| `SHOPIFY_API_VERSION` | es. `2026-07` | no |
| `SHOPIFY_LOOKBACK_DAYS` | giorni di ordini da guardare, es. `21` | no |
| `GLS_SITE` | sede/sigla GLS | sì |
| `GLS_CUSTOMER_CODE` | codice cliente GLS | sì |
| `GLS_CONTRACT_CODE` | codice contratto GLS | sì |
| `GLS_PASSWORD` | password del web service GLS | sì |
| `MOCK_MODE` | `false` | no |
| `SYNC_MAX_TRACKING` | quante spedizioni aggiornare per esecuzione, es. `120` | consigliata |
| `SESSION_SECRET` | stringa casuale che firma i cookie di sessione | consigliata |
| `SESSION_DAYS` | durata dell'accesso in giorni, `0` = senza scadenza | no |
| `SESSION_BIND_IP` | `true` lega la sessione alla rete di accesso | no |
| `DATABASE_SCHEMA` | schema dedicato, se il progetto Supabase è condiviso | no |

`DASHBOARD_USERS` non è facoltativa online: se manca, il monitor risponde `503`
a ogni richiesta e non mostra nulla. È voluto — il database contiene nomi,
telefoni e indirizzi dei clienti.

Per generare `CRON_SECRET` e `SESSION_SECRET`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 2 bis. Gli operatori

Ogni operatore entra con la propria email. Le password non vengono mai salvate
in chiaro: `genera_utenti.py` produce un hash scrypt, dal quale non si risale
alla password.

```bash
python3 genera_utenti.py
```

Lo script chiede email, nome e password di ciascun operatore e stampa il valore
da incollare in `DASHBOARD_USERS`. In un solo comando:

```bash
python3 genera_utenti.py "nome@zuiki.it:Nome:LaPassword"
```

### Come funziona l'accesso

- Si entra dalla pagina `/login`, non dal popup del browser: così si può anche uscire.
- Il nome dell'operatore non va più digitato a mano. Ogni nota, svincolo e
  cambio di stato viene attribuito a chi ha fatto l'accesso.
- La sessione resta valida **30 giorni sullo stesso browser e dalla stessa
  rete**: dall'ufficio non vengono richieste le credenziali ogni volta.
- Da una rete diversa (casa, telefono in 4G) la stessa sessione non vale e viene
  chiesto di nuovo l'accesso. È una protezione: un cookie rubato non apre la
  dashboard altrove. Se gli operatori devono entrare da più reti, imposta
  `SESSION_BIND_IP=false`.

### Cambiare una password

Rilancia `genera_utenti.py` con tutti gli operatori e sostituisci il valore di
`DASHBOARD_USERS` su Vercel. Al primo deploy successivo le vecchie sessioni
decadono e le nuove credenziali sono attive.

> Se tutti gli operatori condividono la stessa password, il registro attribuisce
> sì l'azione a chi ha fatto l'accesso, ma chiunque conosca quella password può
> entrare come chiunque altro. Per una responsabilità reale conviene dare a
> ciascuno una password diversa: si cambia solo il valore passato allo script.

---

## 3. Portare online lo storico gia' esistente

Le note degli operatori, le giacenze e gli svincoli registrati finora stanno nel
file `data/monitor.sqlite3` sul Mac. Si importano una volta sola, **dal Mac**:

```bash
cd /percorso/della/cartella/GLS_Exception_Monitor
pip3 install "psycopg[binary]"
DATABASE_URL="postgresql://postgres.xxx:PASSWORD@aws-0-eu-central-1.pooler.supabase.com:6543/postgres" \
  python3 migrate_to_postgres.py
```

Lo script legge il database locale e copia spedizioni, eventi GLS, azioni
operatore, giacenze, svincoli, incongruenze e classificazioni personalizzate.
È ripetibile: rilanciarlo non crea duplicati, aggiunge solo cio' che manca.

Per importare un file che sta altrove, passane il percorso:

```bash
DATABASE_URL="..." python3 migrate_to_postgres.py ~/Desktop/vecchio/data/monitor.sqlite3
```

---

## 4. Sincronizzazione automatica

Online non c'è un processo sempre acceso: la sincronizzazione periodica la
richiama un cron esterno sull'indirizzo `/api/cron/sync`, autenticato con
`CRON_SECRET`.

### Vercel Cron (già configurato)

`vercel.json` contiene una pianificazione giornaliera alle 06:00 UTC. **Sul
piano Hobby di Vercel il cron può girare al massimo una volta al giorno**: per
un aggiornamento ogni pochi minuti serve il piano Pro (e allora basta cambiare
`schedule` in `*/10 * * * *`).

### Cloudflare Worker (alternativa gratuita ogni 10 minuti)

Dato che usi già Cloudflare, questa è la via più semplice per avere una
sincronizzazione frequente senza passare a Vercel Pro. Crea un Worker con un
Cron Trigger `*/10 * * * *` e questo codice:

```js
export default {
  async scheduled(event, env, ctx) {
    await fetch('https://IL-TUO-PROGETTO.vercel.app/api/cron/sync', {
      headers: { Authorization: `Bearer ${env.CRON_SECRET}` },
    });
  },
};
```

Imposta `CRON_SECRET` tra i secret del Worker, con lo stesso valore usato su
Vercel.

### Aggiornamento manuale

Il pulsante **Aggiorna** nella dashboard funziona sempre, in qualsiasi piano.
Online la sincronizzazione viene eseguita subito e la risposta arriva a lavoro
finito.

---

## 5. Limite di tempo per richiesta

Una funzione Vercel ha un tempo massimo di esecuzione (60 secondi sul piano
Hobby, fino a 300 sul Pro). Si imposta in **Project Settings → Functions →
Function Max Duration**. Interrogare GLS per centinaia di spedizioni può
superarlo.

Per questo esiste `SYNC_MAX_TRACKING`: limita quante spedizioni vengono
aggiornate a ogni esecuzione. Le altre non vengono perse, passano al giro
successivo — la coda è ordinata per ultima visita, quindi a rotazione tocca a
tutte, e le spedizioni nuove hanno sempre la precedenza. Il messaggio di
sincronizzazione indica quante sono state rinviate.

Un valore di partenza ragionevole è `120`. Se vedi sincronizzazioni interrotte,
abbassalo.

---

## 6. Sicurezza

- L'accesso è protetto da autenticazione HTTP Basic su **tutte** le pagine,
  inclusi i file statici. Vercel serve il sito solo in HTTPS, quindi le
  credenziali non viaggiano in chiaro.
- Le credenziali sono confrontate a tempo costante.
- Il file `.env` non è nel repository e non viene caricato su Vercel
  (`.gitignore` e `.vercelignore`).
- Se un segreto è finito in una chat, in uno screenshot o in un ticket,
  ruotalo: client secret Shopify dal pannello app, password GLS dal
  servizio clienti, password Supabase dalle impostazioni del progetto.
- Considera di attivare l'autenticazione a due fattori sugli account Vercel e
  Supabase: da lì si raggiungono i dati dei clienti.

---

## 7. Uso locale sul Mac

Non cambia nulla. Senza `DATABASE_URL` il programma continua a usare SQLite e a
partire con `run.command`, senza installare nulla.

Se invece vuoi che anche il Mac lavori sullo stesso database online, aggiungi al
`.env` locale:

```
DATABASE_URL=postgresql://postgres.xxx:PASSWORD@aws-0-eu-central-1.pooler.supabase.com:6543/postgres
```

e installa il driver con `pip3 install "psycopg[binary]"`. Così gli operatori
vedono lo stesso storico sia dal browser online sia dal Mac.

---

## 8. Verificare che tutto funzioni

Dopo il deploy:

1. Apri `https://IL-TUO-PROGETTO.vercel.app` — devi essere portato alla pagina
   di accesso.
2. Entra con la tua email e premi **Aggiorna**: parte la prima sincronizzazione.
   In alto a destra deve comparire il tuo nome e il pulsante **Esci**.
3. Controlla che compaiano le spedizioni e, se hai fatto l'import, lo storico.
4. Prova il cron a mano:

   ```bash
   curl -i -H "Authorization: Bearer IL-TUO-CRON-SECRET" \
     https://IL-TUO-PROGETTO.vercel.app/api/cron/sync
   ```

   Deve rispondere `200`. Senza header deve rispondere `401`.

---

## 9. Se qualcosa non funziona

Apri `https://IL-TUO-PROGETTO.vercel.app/api/status`. Risponde anche quando la
configurazione è incompleta e dice cosa manca, senza mostrare alcun segreto:

```json
{
  "ok": true,
  "python": "3.12.x",
  "tzdata": true,
  "storage": "postgres",
  "database": "raggiungibile",
  "configurato": {
    "database_url": true, "operatori": 3, "shopify": true,
    "gls_tracking": true, "cron_secret": true, "mock_mode": false
  }
}
```

| Cosa vedi | Cosa significa |
|---|---|
| `"database": "errore"` con `database_errore` | La `DATABASE_URL` è sbagliata o Supabase non risponde. Il messaggio dice quale delle due: host non risolto, autenticazione fallita, connessione rifiutata. |
| `DATABASE_URL non impostata` | Manca la variabile. Online è obbligatoria: il disco di Vercel non conserva nulla tra una richiesta e l'altra. |
| `"avviso"` su `DASHBOARD_USERS` | Nessun operatore configurato: il monitor non mostra dati finché non ne aggiungi. |
| `"tzdata": false` | Manca il pacchetto `tzdata`: gli orari slittano a UTC. Verifica che `requirements.txt` sia stato installato. |
| `"operatori": 0` con la variabile impostata | Il JSON di `DASHBOARD_USERS` non è valido. Rigeneralo con `genera_utenti.py`. |
| `FUNCTION_INVOCATION_FAILED` | Errore prima ancora che l'applicazione parta: quasi sempre una dipendenza non installata. Guarda il log del build su Vercel. |

Un errore di autenticazione Postgres significa quasi sempre che la `@` della
password non è stata codificata come `%40` nella stringa di connessione.

---

## Alternativa: un contenitore invece di Vercel

Se in futuro il limite di tempo per richiesta dovesse dare fastidio, lo stesso
codice gira come processo sempre acceso su qualsiasi hosting a container
(Fly.io, Render, Railway). In quel caso la porta arriva dalla variabile `PORT`,
l'applicazione si mette in ascolto su `0.0.0.0` da sola e lo scheduler interno
torna attivo: niente cron esterno, e nessun limite di durata.
