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
