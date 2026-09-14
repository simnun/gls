#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ ! -f .env ]; then
  echo "Configurazione mancante. Avvio setup..."
  python3 setup.py || exit 1
fi
python3 migrate_previous.py
( sleep 1; open "http://127.0.0.1:8787" >/dev/null 2>&1 ) &
python3 app.py
