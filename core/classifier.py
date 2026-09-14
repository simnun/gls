from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")
SEVERITY_RANK = {"NORMAL": 1, "INFO": 2, "WATCH": 3, "WARNING": 4, "CRITICAL": 5}


def normalize_text(value: str | None) -> str:
    value = (value or "").strip().lower()
    value = value.replace("’", "'").replace("`", "'")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("'", "")
    value = re.sub(r"\s+", " ", value)
    return value


def parse_event_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ROME)
        return dt.astimezone(ROME)
    except Exception:
        pass
    for fmt in (
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=ROME)
        except ValueError:
            continue
    return None


@dataclass
class Classification:
    severity: str
    category: str
    reason: str
    recommended_action: str
    matched_rule: str | None = None


class Classifier:
    def __init__(self, rules_path: Path, db):
        payload = json.loads(rules_path.read_text(encoding="utf-8"))
        self.rules = payload.get("rules", [])
        self.db = db

    def classify(
        self,
        *,
        code: str | None,
        state: str | None,
        note: str | None,
        event_at: str | None,
        is_cod: bool = False,
    ) -> Classification:
        code = (code or "").strip()
        if code:
            override = self.db.get_override(code)
            if override:
                return Classification(
                    severity=override["severity"],
                    category=override["category"],
                    reason=f"Codice GLS {code} classificato manualmente.",
                    recommended_action=override.get("recommended_action") or "",
                    matched_rule="manual_override",
                )

        text = normalize_text(" ".join(x for x in [state or "", note or ""] if x))
        event_dt = parse_event_datetime(event_at)
        now = datetime.now(ROME)
        age_hours = None
        if event_dt:
            age_hours = max(0.0, (now - event_dt).total_seconds() / 3600)

        best: Classification | None = None
        best_rank = 0

        for rule in self.rules:
            patterns = [normalize_text(p) for p in rule.get("patterns", [])]
            excludes = [normalize_text(p) for p in rule.get("exclude_patterns", [])]
            if patterns and not any(p and p in text for p in patterns):
                continue
            if any(p and p in text for p in excludes):
                continue

            severity = rule.get("severity", "WATCH")
            if age_hours is not None:
                second_after = rule.get("second_escalate_after_hours")
                if second_after is not None and age_hours >= float(second_after):
                    severity = rule.get("second_escalate_to", severity)
                else:
                    after = rule.get("escalate_after_hours")
                    if after is not None and age_hours >= float(after):
                        severity = rule.get("escalate_to", severity)

            candidate = Classification(
                severity=severity,
                category=rule.get("category", "UNCLASSIFIED"),
                reason=self._reason(rule, age_hours),
                recommended_action=rule.get("action", ""),
                matched_rule=rule.get("id"),
            )
            rank = SEVERITY_RANK.get(candidate.severity, 3)
            if rank > best_rank:
                best = candidate
                best_rank = rank

        if best is None:
            best = Classification(
                severity="WATCH",
                category="UNCLASSIFIED",
                reason=(
                    f"Nuovo codice GLS {code} da classificare."
                    if code
                    else "Stato GLS non riconosciuto dalle regole correnti."
                ),
                recommended_action="Verificare una volta e classificare il codice/stato per le prossime occorrenze.",
                matched_rule=None,
            )

        if is_cod and best.category in {"ABSENT", "REFUSED", "ADDRESS_ERROR", "DELIVERY_FAILURE", "STORAGE"}:
            if SEVERITY_RANK.get(best.severity, 3) < SEVERITY_RANK["CRITICAL"]:
                best.severity = "CRITICAL"
            best.reason += " Ordine in contrassegno: priorita aumentata."

        return best

    @staticmethod
    def _reason(rule: dict[str, Any], age_hours: float | None) -> str:
        label = rule.get("id", "regola")
        if age_hours is None:
            return f"Corrisponde alla regola '{label}'."
        return f"Corrisponde alla regola '{label}' da circa {age_hours:.1f} ore."
