#!/bin/bash
cd "$(dirname "$0")" || exit 1
python3 - <<'PY'
from core.config import get_config
from core.shopify import ShopifyClient
from core.gls import GLSClient

cfg = get_config()
print("\n=== GLS Exception Monitor - Diagnostica ===\n")
print("Shopify:", cfg.shopify_shop or "NON CONFIGURATO")
try:
    token = ShopifyClient(cfg).get_token()
    print("[OK] Autenticazione Shopify")
except Exception as exc:
    print("[ERRORE] Shopify:", exc)

try:
    result = GLSClient(cfg).diagnostics()
    if result.get("list_sped_ok"):
        print("[OK] Autenticazione GLS / ListSped")
    else:
        print("[ERRORE] GLS:", result.get("list_sped_error") or result)
except Exception as exc:
    print("[ERRORE] GLS:", exc)

print("\nPremi Invio per chiudere.")
input()
PY
