"""
Real-time warning messages (TELEGRAM_SPEC §1.1) — HIGHEST priority.

Pure text, NO chart: a person mid-trade on their phone needs a clear sentence,
not a graph. Two sub-types, which can fire together:
  (a) impulse coaching  — from JarvisAssessment.coaching
  (b) damage guardrail  — from JarvisAssessment.guardrail

Both are grounded in the user's OWN data + compound math, because that grounding
is produced upstream by jarvis_assess (e.g. "+52% to recover", "your win rate
26%"). This module only adds the localized framing (header/footer) and the
anti-spam gate — it does NOT invent numbers.

Anti-spam: hysteresis via rule_engine's ViolationStore. A given (user, symbol,
kind) warning fires ONCE when it goes active and stays silent until the worker
clears it on position close / resolution.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from app.core.jarvis_assess import JarvisAssessment
from app.core.rule_engine import ViolationStore
from app.i18n import t


class WarningKind(str, Enum):
    COACHING = "coaching"
    GUARDRAIL = "guardrail"


# Re-alert suppression window. Long enough that an ongoing violation doesn't
# re-ping; the worker also clears explicitly on position close.
_WARNING_TTL_SEC = 6 * 3600


@dataclass
class WarningMessage:
    kind: WarningKind
    text: str


def build_warnings(
    assessment: JarvisAssessment, *, lang: Optional[str] = None
) -> list[WarningMessage]:
    """Build the warning(s) implied by an assessment. Pure — no I/O, no gating.

    The grounded sentence (user's own win-rate / compound recovery math) comes
    straight from the assessment; we wrap it with localized header + footer.
    """
    out: list[WarningMessage] = []

    g = assessment.guardrail
    if g.triggered:
        out.append(WarningMessage(
            WarningKind.GUARDRAIL,
            f"{t('telegram.warning.guardrail.header', lang=lang)}\n\n"
            f"{g.message}\n\n"
            f"{t('telegram.warning.footer', lang=lang)}",
        ))

    c = assessment.coaching
    if c.triggered:
        out.append(WarningMessage(
            WarningKind.COACHING,
            f"{t('telegram.warning.coaching.header', lang=lang)}\n\n"
            f"{c.message}\n\n"
            f"{t('telegram.warning.footer', lang=lang)}",
        ))

    return out


def _dedup_key(user_id: int, symbol: str, kind: WarningKind) -> str:
    return f"{user_id}:{symbol}:warn_{kind.value}"


def gate_warnings(
    store: ViolationStore,
    user_id: int,
    symbol: str,
    warnings: list[WarningMessage],
) -> list[WarningMessage]:
    """Apply hysteresis: return only warnings not already active, marking the
    ones we let through as active so they won't re-fire while ongoing."""
    fresh: list[WarningMessage] = []
    for w in warnings:
        key = _dedup_key(user_id, symbol, w.kind)
        if store.is_active(key):
            continue
        store.mark_active(key, _WARNING_TTL_SEC)
        fresh.append(w)
    return fresh


def clear_warnings(store: ViolationStore, user_id: int, symbol: str) -> None:
    """On position close/resolution, clear all warning kinds for this symbol so
    a future re-entry can warn again."""
    for kind in WarningKind:
        store.clear(_dedup_key(user_id, symbol, kind))
