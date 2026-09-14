#!/usr/bin/env python3
from __future__ import annotations

import getpass
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"


def ask(label: str, current: str = "", secret: bool = False, required: bool = True) -> str:
    suffix = f" [{current}]" if current and not secret else ""
    while True:
        prompt = f"{label}{suffix}: "
        value = getpass.getpass(prompt) if secret else input(prompt)
        value = value.strip()
        if not value and current:
            return current
        if value or not required:
            return value
        print("Campo obbligatorio.")


def parse_existing() -> dict[str, str]:
    result = {}
    if not ENV.exists():
        return result
    for raw in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in raw and not raw.lstrip().startswith("#"):
            k, v = raw.split("=", 1)
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def escape(value: str) -> str:
    if not value:
        return ""
    if re.search(r"[\s#='\"]", value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def main() -> None:
    old = parse_existing()
    print("\nConfigurazione GLS Exception Monitor")
    print("Le password vengono salvate solo nel file .env locale e non nel codice.\n")

    shop = ask("Shopify shop (solo sottodominio, es. nomestore)", old.get("SHOPIFY_SHOP", ""))
    shop = shop.replace("https://", "").replace("http://", "").replace(".myshopify.com", "").strip("/")
    client_id = ask("Shopify Client ID", old.get("SHOPIFY_CLIENT_ID", ""))
    client_secret = ask("Shopify Client Secret", old.get("SHOPIFY_CLIENT_SECRET", ""), secret=True)
    gls_site = ask("GLS Sede/Sigla", old.get("GLS_SITE", ""))
    gls_customer = ask("GLS Codice Cliente", old.get("GLS_CUSTOMER_CODE", ""))
    gls_contract = ask("GLS Codice Contratto", old.get("GLS_CONTRACT_CODE", ""))
    gls_password = ask("GLS Password WebService", old.get("GLS_PASSWORD", ""), secret=True)

    dashboard_user = ask("Utente dashboard (opzionale, Invio per nessuno)", old.get("DASHBOARD_USER", ""), required=False)
    dashboard_password = ""
    if dashboard_user:
        dashboard_password = ask("Password dashboard", old.get("DASHBOARD_PASSWORD", ""), secret=True)

    values = {
        "SHOPIFY_SHOP": shop,
        "SHOPIFY_CLIENT_ID": client_id,
        "SHOPIFY_CLIENT_SECRET": client_secret,
        "SHOPIFY_API_VERSION": old.get("SHOPIFY_API_VERSION", "2026-07"),
        "SHOPIFY_LOOKBACK_DAYS": old.get("SHOPIFY_LOOKBACK_DAYS", "21"),
        "GLS_SITE": gls_site,
        "GLS_CUSTOMER_CODE": gls_customer,
        "GLS_CONTRACT_CODE": gls_contract,
        "GLS_PASSWORD": gls_password,
        "GLS_TRACK_ENDPOINT": old.get("GLS_TRACK_ENDPOINT", "https://wwwdr.gls-italy.com/XML/get_xml_track.php"),
        "GLS_LIST_ENDPOINT": old.get("GLS_LIST_ENDPOINT", "https://labelservice.gls-italy.com/ilswebservice.asmx/ListSped"),
        "GLS_RELEASE_ENDPOINT": old.get("GLS_RELEASE_ENDPOINT", "https://labelservice.gls-italy.com/ilswebservice.asmx/ReleaseShipmentStock"),
        "GLS_ACCEPT_UNLABELED_TRACKING": old.get("GLS_ACCEPT_UNLABELED_TRACKING", "true"),
        "AUTO_SYNC": old.get("AUTO_SYNC", "true"),
        "SYNC_INTERVAL_MINUTES": old.get("SYNC_INTERVAL_MINUTES", "10"),
        "REQUEST_TIMEOUT_SECONDS": old.get("REQUEST_TIMEOUT_SECONDS", "20"),
        "APP_HOST": old.get("APP_HOST", "127.0.0.1"),
        "APP_PORT": old.get("APP_PORT", "8787"),
        "DASHBOARD_USER": dashboard_user,
        "DASHBOARD_PASSWORD": dashboard_password,
        "MOCK_MODE": "false",
    }
    content = "# Creato da setup.py - NON condividere questo file\n" + "\n".join(f"{k}={escape(v)}" for k, v in values.items()) + "\n"
    ENV.write_text(content, encoding="utf-8")
    try:
        ENV.chmod(0o600)
    except Exception:
        pass
    print("\nConfigurazione salvata in .env.")
    print("Ora avvia run.command.\n")


if __name__ == "__main__":
    main()
