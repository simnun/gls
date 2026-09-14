# Sicurezza

- Non salvare mai Client Secret Shopify o password GLS dentro HTML/JavaScript.
- Le credenziali vanno nel file `.env`, che e escluso da Git.
- Per impostazione predefinita il server ascolta solo su `127.0.0.1`.
- Se lo pubblichi in LAN/VPN, configura `DASHBOARD_USER` e `DASHBOARD_PASSWORD`.
- Il database locale contiene dati operativi e potenzialmente dati cliente: proteggi il computer e i backup.
- Se un segreto viene mostrato in chat, screenshot, ticket o repository, ruotalo prima dell'uso in produzione.

## Deploy online

- Online l'autenticazione non e' facoltativa: senza `DASHBOARD_USER` e
  `DASHBOARD_PASSWORD` il monitor risponde `503` e non mostra alcun dato.
- Il controllo credenziali copre anche i file statici, non solo le API.
- Le credenziali sono confrontate a tempo costante (`hmac.compare_digest`).
- La sincronizzazione pianificata e' su `/api/cron/sync` ed e' protetta da
  `CRON_SECRET`, separato dalle credenziali della dashboard.
- Le variabili d'ambiente vivono nelle impostazioni della piattaforma: il file
  `.env` non viene ne' committato (`.gitignore`) ne' caricato (`.vercelignore`).
- Il database online contiene nomi, telefoni e indirizzi dei clienti: proteggi
  gli account Vercel e Supabase con l'autenticazione a due fattori.

## Accesso operatori

- Le password sono conservate solo come hash scrypt con sale casuale: dal valore
  di `DASHBOARD_USERS` non si risale alle password.
- Il confronto della password e' a tempo costante. Un'email inesistente impiega
  lo stesso tempo di una esistente, per non rivelare quali indirizzi sono validi.
- Il cookie di sessione e' firmato con HMAC-SHA256, marcato `HttpOnly`,
  `SameSite=Lax` e `Secure` in HTTPS: non e' leggibile da JavaScript.
- La sessione e' legata alla rete da cui e' stato fatto l'accesso
  (`SESSION_BIND_IP`): un cookie copiato altrove non apre la dashboard.
- Il messaggio di errore dell'accesso non distingue mai tra email sconosciuta e
  password sbagliata.
- Il monitor non limita da solo i tentativi di accesso ripetuti. Se lo esponi su
  un dominio pubblico, valuta una regola di rate limiting su Vercel o Cloudflare
  sulla rotta `/api/login`.
