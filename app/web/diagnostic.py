"""
Landing self-diagnostic — 5 questions, no data needed (DEV_SPEC §4 step 1).

Maps answers onto two axes — technique and discipline — then classifies into a
tentative type. ALL user-facing copy comes from i18n.t(); this module only
holds question ids + scoring, never literal sentences.

Tentative only: the real diagnosis comes after connecting data.
"""
from __future__ import annotations
from dataclasses import dataclass

from app.i18n import t

# Each question targets an axis. `reverse=True` means a high answer is *bad*
# for that axis (e.g. "I chase losses" → high = worse discipline).
QUESTIONS = [
    {"id": "has_edge",      "axis": "technique",  "reverse": False},
    {"id": "plan_exit",     "axis": "technique",  "reverse": False},
    {"id": "cut_losses",    "axis": "discipline", "reverse": False},
    {"id": "revenge",       "axis": "discipline", "reverse": True},
    {"id": "size_control",  "axis": "discipline", "reverse": False},
]

# 3-point scale → numeric weight.
SCALE = {"often": 1.0, "sometimes": 0.5, "rarely": 0.0}

# Above this (0..1) an axis counts as "present".
THRESHOLD = 0.5


@dataclass
class DiagnosticResult:
    type_key: str          # gambling | mind_deficient | technique_deficient | balanced
    type_label: str
    headline: str
    technique_score: float
    discipline_score: float


def questions_payload(lang: str | None = None) -> dict:
    """Localized questions + scale options for the landing page."""
    return {
        "intro": t("diagnostic.intro", lang=lang),
        "scale": [
            {"value": k, "label": t(f"diagnostic.scale.{k}", lang=lang)}
            for k in ("often", "sometimes", "rarely")
        ],
        "questions": [
            {"id": q["id"], "text": t(f"diagnostic.q.{q['id']}", lang=lang)}
            for q in QUESTIONS
        ],
    }


def _axis_score(answers: dict[str, str], axis: str) -> float:
    items = [q for q in QUESTIONS if q["axis"] == axis]
    if not items:
        return 0.0
    total = 0.0
    for q in items:
        raw = SCALE.get(answers.get(q["id"], "rarely"), 0.0)
        total += (1.0 - raw) if q["reverse"] else raw
    return total / len(items)


def classify(answers: dict[str, str], lang: str | None = None) -> DiagnosticResult:
    tech = _axis_score(answers, "technique")
    disc = _axis_score(answers, "discipline")
    has_tech = tech >= THRESHOLD
    has_disc = disc >= THRESHOLD

    if has_tech and has_disc:
        key = "balanced"
    elif has_tech and not has_disc:
        key = "mind_deficient"        # has technique, lacks discipline
    elif not has_tech and has_disc:
        key = "technique_deficient"   # disciplined, lacks an edge
    else:
        key = "gambling"

    return DiagnosticResult(
        type_key=key,
        type_label=t(f"diagnostic.type.{key}", lang=lang),
        headline=t(f"diagnostic.headline.{key}", lang=lang),
        technique_score=round(tech, 2),
        discipline_score=round(disc, 2),
    )
