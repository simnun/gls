"""Entrypoint per Vercel.

Il runtime Python di Vercel serve una classe `handler` derivata da
BaseHTTPRequestHandler: e' esattamente la forma gia' usata dal monitor, quindi
l'applicazione viene riusata senza duplicare la logica delle rotte.

`vercel.json` reindirizza qui ogni percorso, cosi anche i file statici passano
dal controllo delle credenziali.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import AppHandler  # noqa: E402

handler = AppHandler
