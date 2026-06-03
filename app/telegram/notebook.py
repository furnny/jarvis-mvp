"""
Mistake-notebook tagging (TELEGRAM_SPEC §1.2) — 2nd priority.

When a position closes, prompt the user with ONE tap: why did you enter, and
how did you feel? Two inline-button groups map to journal.py enums
(BaseRationale, EmotionalState). "Bad" rationales (impulse/revenge/FOMO) carry a
⚠️ so they're honest to tap, not hidden.

We do NOT ask what the system already knows — stop-loss adherence
(stoploss_tracker) and result (fills) are auto-filled onto the JournalEntry.
A free-text reply to the prompt is saved as JournalEntry.note.

callback_data budget: Telegram caps it at 64 bytes, so we never embed the
trade_id. Instead a short `code` references a PendingTag held by the worker
(see PendingTagStore) that carries the full journal context.
"""
from __future__ import annotations
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.core.journal import BaseRationale, EmotionalState, JournalEntry
from app.i18n import t
from app.telegram.notifier import Button

# Stable display order for the keyboards (disciplined first, then the honest
# "bad" ones flagged with ⚠️).
RATIONALE_ORDER: list[BaseRationale] = [
    BaseRationale.TECHNICAL, BaseRationale.SUPPORT_RESISTANCE,
    BaseRationale.TREND, BaseRationale.NEWS,
    BaseRationale.IMPULSE, BaseRationale.REVENGE, BaseRationale.FOMO,
]
EMOTION_ORDER: list[EmotionalState] = [
    EmotionalState.CALM, EmotionalState.CONFIDENT, EmotionalState.ANXIOUS,
    EmotionalState.REVENGE, EmotionalState.GREEDY, EmotionalState.BORED,
]


@dataclass
class PendingTag:
    """Journal context the worker holds between sending the prompt and the user
    tapping a button. Carries everything needed to write a JournalEntry."""
    user_id: int
    trade_id: str
    symbol: str
    side: str
    pnl_pct: float
    closed_at: datetime
    followed_stop_loss: Optional[bool] = None


class PendingTagStore:
    """In-memory map: code → PendingTag.

    NOTE (scaling boundary): in-memory is fine for the single-process MVP
    worker. When the worker moves to multi-process / queue dispatch, back this
    with Redis (same shape) so any process can resolve a callback.
    """

    def __init__(self) -> None:
        self._tags: dict[str, PendingTag] = {}

    def put(self, tag: PendingTag) -> str:
        code = secrets.token_urlsafe(6)
        self._tags[code] = tag
        return code

    def get(self, code: str) -> Optional[PendingTag]:
        return self._tags.get(code)

    def by_chat_recent(self, *, user_id: int) -> Optional[str]:
        """Most-recent pending code for a user (for free-text note replies)."""
        for code, tag in reversed(list(self._tags.items())):
            if tag.user_id == user_id:
                return code
        return None

    def drop(self, code: str) -> None:
        self._tags.pop(code, None)


def build_prompt(tag: PendingTag, code: str, *, lang: Optional[str] = None):
    """Return (text, button_rows) for the close-tagging prompt."""
    pnl = f"{tag.pnl_pct:+.1f}%"
    text = t("telegram.notebook.prompt", lang=lang,
             symbol=tag.symbol, side=tag.side, pnl=pnl)

    rows: list[list[Button]] = []
    # rationale group
    for i, r in enumerate(RATIONALE_ORDER):
        label = r.value if r.is_disciplined else f"⚠️ {r.value}"
        btn = Button(label, f"jr:{code}:{i}")
        if i % 3 == 0:
            rows.append([btn])
        else:
            rows[-1].append(btn)
    # emotion group (separate rows)
    emo_rows: list[list[Button]] = []
    for i, e in enumerate(EMOTION_ORDER):
        btn = Button(e.value, f"je:{code}:{i}")
        if i % 3 == 0:
            emo_rows.append([btn])
        else:
            emo_rows[-1].append(btn)
    rows.extend(emo_rows)
    return text, rows


@dataclass
class ParsedCallback:
    field: str       # "rationale" | "emotion"
    code: str
    value: str       # BaseRationale value or EmotionalState value


def parse_callback(data: str) -> Optional[ParsedCallback]:
    """Parse `jr:<code>:<idx>` / `je:<code>:<idx>` back to a domain value."""
    try:
        tag, code, idx_s = data.split(":")
        idx = int(idx_s)
    except (ValueError, AttributeError):
        return None
    if tag == "jr" and 0 <= idx < len(RATIONALE_ORDER):
        return ParsedCallback("rationale", code, RATIONALE_ORDER[idx].value)
    if tag == "je" and 0 <= idx < len(EMOTION_ORDER):
        return ParsedCallback("emotion", code, EMOTION_ORDER[idx].value)
    return None


def apply_to_entry(
    existing: Optional[JournalEntry], tag: PendingTag, parsed: ParsedCallback
) -> JournalEntry:
    """Fold a tap into a JournalEntry (idempotent upsert by trade_id).

    Rationale and emotion can arrive in either order / separately; we merge
    onto the same entry. System-known fields (followed_stop_loss, pnl) are
    auto-filled from the tag, never asked.
    """
    if existing is None:
        existing = JournalEntry(
            trade_id=tag.trade_id, symbol=tag.symbol,
            closed_at=tag.closed_at or datetime.now(timezone.utc),
            pnl_pct=tag.pnl_pct, rationale="", emotion=None,
            followed_stop_loss=tag.followed_stop_loss,
        )
    if parsed.field == "rationale":
        existing.rationale = parsed.value
    else:
        existing.emotion = EmotionalState(parsed.value)
    return existing


def confirmation(parsed: ParsedCallback, *, lang: Optional[str] = None) -> str:
    return t("telegram.notebook.saved", lang=lang, label=parsed.value)
