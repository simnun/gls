#!/usr/bin/env python3
"""Genera il valore della variabile d'ambiente DASHBOARD_USERS.

Le password non vengono mai salvate in chiaro: di ognuna resta solo un hash
scrypt, inutilizzabile per risalire alla password originale.

Uso interattivo (consigliato, la password non resta nella cronologia dei comandi):

    python3 genera_utenti.py

Uso diretto, un operatore per argomento nella forma `email:Nome:password`:

    python3 genera_utenti.py "simone@zuiki.it:Simone:LaPassword"
"""
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core.auth import hash_password  # noqa: E402


def build(username: str, name: str, password: str) -> dict[str, str]:
    username = username.strip().lower()
    if not username or "@" not in username:
        raise ValueError(f"Email non valida: {username!r}")
    if not password:
        raise ValueError(f"Password mancante per {username}")
    return {
        "username": username,
        "name": name.strip() or username,
        "password_hash": hash_password(password),
    }


def from_arguments(arguments: list[str]) -> list[dict[str, str]]:
    users = []
    for raw in arguments:
        parts = raw.split(":", 2)
        if len(parts) != 3:
            raise ValueError(f"Formato atteso email:Nome:password, ricevuto {raw!r}")
        users.append(build(*parts))
    return users


def interactive() -> list[dict[str, str]]:
    print("\nOperatori della dashboard GLS Monitor.")
    print("Invio su un'email vuota per terminare.\n")
    users = []
    while True:
        username = input("Email operatore: ").strip()
        if not username:
            break
        name = input("  Nome visualizzato: ").strip()
        password = getpass.getpass("  Password: ")
        conferma = getpass.getpass("  Ripeti password: ")
        if password != conferma:
            print("  Le password non coincidono, operatore saltato.\n")
            continue
        try:
            users.append(build(username, name, password))
        except ValueError as exc:
            print(f"  {exc}\n")
            continue
        print(f"  {username} aggiunto.\n")
    return users


def main() -> int:
    try:
        users = from_arguments(sys.argv[1:]) if len(sys.argv) > 1 else interactive()
    except ValueError as exc:
        print(f"Errore: {exc}")
        return 2

    if not users:
        print("Nessun operatore creato.")
        return 1

    valore = json.dumps(users, ensure_ascii=False, separators=(",", ":"))
    print("\n" + "=" * 70)
    print("DASHBOARD_USERS — incolla questo valore nelle variabili d'ambiente")
    print("=" * 70)
    print(valore)
    print("=" * 70)
    print(f"\n{len(users)} operatori: " + ", ".join(u["username"] for u in users))
    print("\nNon serve conservare questo valore altrove: contiene solo hash.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
