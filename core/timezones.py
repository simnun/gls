"""Fuso orario italiano, tollerante alle immagini senza database dei fusi.

Le immagini usate dagli hosting serverless spesso non includono i dati IANA
delle timezone: `ZoneInfo("Europe/Rome")` solleverebbe un'eccezione durante
l'import, prima ancora che l'applicazione possa rispondere.

Il pacchetto `tzdata` fra le dipendenze rende il caso normale sempre corretto;
questo modulo evita comunque che un ambiente incompleto renda il monitor
irraggiungibile.
"""
from __future__ import annotations

import sys
from datetime import timezone, tzinfo

ROME_KEY = "Europe/Rome"


def load_rome() -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(ROME_KEY)
    except Exception as exc:  # pragma: no cover - dipende dall'ambiente
        # Meglio un orario in UTC che un'applicazione che non parte. La
        # differenza sposta di un'ora le soglie orarie: va segnalata.
        sys.stderr.write(
            f"ATTENZIONE: fuso orario {ROME_KEY} non disponibile ({exc}). "
            "Si usa UTC: installa il pacchetto 'tzdata'.\n"
        )
        return timezone.utc


ROME: tzinfo = load_rome()


def tzdata_available() -> bool:
    """Vero se il fuso italiano e' stato caricato davvero."""
    return ROME is not timezone.utc
