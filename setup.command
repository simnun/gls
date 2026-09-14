#!/bin/bash
cd "$(dirname "$0")" || exit 1
python3 setup.py
printf "\nPremi Invio per chiudere..."
read -r _
