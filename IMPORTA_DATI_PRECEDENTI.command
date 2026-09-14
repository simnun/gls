#!/bin/bash
cd "$(dirname "$0")" || exit 1
echo "Trascina qui la cartella della versione precedente e premi Invio:"
read -r SRC
SRC="${SRC#\'}"; SRC="${SRC%\'}"; SRC="${SRC#\"}"; SRC="${SRC%\"}"
python3 migrate_previous.py "$SRC"
echo
read -n 1 -s -r -p "Premi un tasto per chiudere..."
