"""Entrypoint per Vercel.

Il runtime Python di Vercel cerca, in cima al modulo, un'applicazione WSGI/ASGI
chiamata `app` oppure una classe `handler`. Qui sono esposte entrambe: l'unica
implementazione delle rotte resta quella di `app.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import AppHandler, app, application  # noqa: E402,F401


class handler(AppHandler):
    """Forma legacy del runtime: una classe di nome `handler`."""
