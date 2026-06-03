"""
Phase 3 Telegram tests — the acceptance criteria from TELEGRAM_SPEC.md.

No live bot/exchange: a FakeNotifier records sends, the warning hysteresis uses
the in-memory ViolationStore, and journal persistence runs on the sqlite app_db.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.core.jarvis_assess import assess
from app.core.jarvis_score import AccountContext, BehaviorSignals
from app.core.risk_math import PositionInput, Side, compute_risk, D
from app.core.rule_engine import InMemoryViolationStore
from app.telegram import notebook, warnings
from app.telegram.notifier import FakeNotifier


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── 1) /start <code> links chat_id (reuses consume_telegram_link_code) ───────

def test_start_links_chat(app_db):
    from app.telegram.handlers import handle_start
    from app.web.services import (
        consume_magic_link_token, create_magic_link_token,
        create_telegram_link_code, user_id_for_chat,
    )

    async def go():
        # make a user + a telegram link code
        raw = await create_magic_link_token("tg-user@example.com")
        uid = await consume_magic_link_token(raw)
        code = await create_telegram_link_code(uid)

        notifier = FakeNotifier()
        await handle_start(notifier, chat_id=4242, payload=code)

        # chat_id now bound to the user, and a welcome was sent
        assert await user_id_for_chat(4242) == uid
        assert notifier.texts and "/start" not in notifier.texts[-1].text
    _run(go())


def test_start_invalid_code(app_db):
    from app.telegram.handlers import handle_start

    async def go():
        notifier = FakeNotifier()
        await handle_start(notifier, chat_id=999, payload="not-a-real-code")
        assert notifier.texts  # responded with an invalid message
    _run(go())


# ── 2) warning is grounded in the user's OWN data + compound math ────────────

def _impulse_assessment():
    eq = D("10000")
    # no stop-loss, big size, after a loss streak. qty 12 @ 3000, liq 2700 →
    # liq loss = 300*12 = 3600 = 36% of equity → trips the 30% guardrail, and
    # the 2.8x size after a loss streak trips coaching.
    pos = PositionInput("ETHUSDT", Side.LONG, D("12"), D("3000"), D("3000"),
                        D("10"), D("2700"), D("0"), None)
    m = compute_risk(pos, eq)
    return assess(
        m, AccountContext(eq, 40, 70),
        BehaviorSignals(recent_losses_streak=3, minutes_since_last_loss=4,
                        trades_last_30min=5, position_size_vs_avg=2.8),
        atr_pct=5.0,
    )


def test_warning_grounded_and_text_only():
    a = _impulse_assessment()
    msgs = warnings.build_warnings(a, lang="ko")
    assert msgs, "expected at least one warning"
    by_kind = {m.kind: m.text for m in msgs}
    # coaching grounded in the user's own size multiple (not generic)
    assert warnings.WarningKind.COACHING in by_kind
    assert "2.8" in by_kind[warnings.WarningKind.COACHING]
    # guardrail grounded in the user's own computed liquidation loss (36%)
    assert warnings.WarningKind.GUARDRAIL in by_kind
    assert "36%" in by_kind[warnings.WarningKind.GUARDRAIL]


# ── 3) hysteresis: identical assessment doesn't re-fire (anti-spam) ──────────

def test_hysteresis_suppresses_second_identical_warning():
    store = InMemoryViolationStore()
    a = _impulse_assessment()
    first = warnings.gate_warnings(store, 1, "ETHUSDT", warnings.build_warnings(a))
    second = warnings.gate_warnings(store, 1, "ETHUSDT", warnings.build_warnings(a))
    assert first, "first evaluation should emit"
    assert second == [], "second identical evaluation must be suppressed"

    # after the position closes, the same warning may fire again
    warnings.clear_warnings(store, 1, "ETHUSDT")
    third = warnings.gate_warnings(store, 1, "ETHUSDT", warnings.build_warnings(a))
    assert third, "warning should re-arm after close"


# ── 4) notebook tap writes a JournalEntry with the right enums ───────────────

def test_notebook_tap_persists_journal_entry(app_db):
    from app.telegram import state
    from app.telegram.handlers import handle_callback
    from app.web.services import (
        consume_magic_link_token, create_magic_link_token,
        consume_telegram_link_code, create_telegram_link_code,
    )
    from app.db.persistence import get_journal_entry

    async def go():
        raw = await create_magic_link_token("nb@example.com")
        uid = await consume_magic_link_token(raw)
        code = await create_telegram_link_code(uid)
        await consume_telegram_link_code(code, chat_id=7777)

        # worker would have queued a pending tag on close; simulate it
        tag = notebook.PendingTag(
            user_id=uid, trade_id="ETHUSDT-2026", symbol="ETHUSDT", side="LONG",
            pnl_pct=-4.2, closed_at=datetime.now(timezone.utc),
            followed_stop_loss=False,
        )
        pcode = state.pending_tags.put(tag)

        notifier = FakeNotifier()
        # tap "revenge" rationale, then "revenge" emotion
        r_idx = notebook.RATIONALE_ORDER.index(
            __import__("app.core.journal", fromlist=["BaseRationale"]).BaseRationale.REVENGE)
        await handle_callback(notifier, 7777, f"jr:{pcode}:{r_idx}")
        e_idx = notebook.EMOTION_ORDER.index(
            __import__("app.core.journal", fromlist=["EmotionalState"]).EmotionalState.REVENGE)
        await handle_callback(notifier, 7777, f"je:{pcode}:{e_idx}")

        entry = await get_journal_entry(uid, "ETHUSDT-2026")
        assert entry is not None
        assert entry.rationale == "복수매매"
        assert entry.emotion is not None and entry.emotion.value == "분노/복수"
        # system-known fields auto-filled, never asked
        assert entry.followed_stop_loss is False
        assert notifier.texts  # confirmations sent
    _run(go())


# ── 5) daily job builds from viz_contract and produces a chart + caption ─────

def test_daily_builds_chart_and_conclusion():
    from app.telegram import daily as daily_mod

    # minimal viz_contract-shaped inputs
    situation = {"overall": {"n": 5, "win_rate": 40.0, "total_pnl": -6.1}}
    timeseries = {"equity": [100, 99, 97, 98, 94, 93]}

    class _Stat:
        def __init__(self, n, total): self.n = n; self.total_pnl = total
    split = {"disciplined": _Stat(2, 1.7), "impulsive": _Stat(3, -7.8)}

    png, caption = daily_mod.build_daily(
        situation, timeseries, split, date_label="2026-01-14", lang="en")
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"  # real PNG bytes
    assert "-7.8%" in caption and "1.7%" in caption  # grounded conclusion


def test_daily_no_trades_is_text_only():
    from app.telegram import daily as daily_mod
    png, caption = daily_mod.build_daily(
        {"overall": {"n": 0}}, {"equity": []}, {}, date_label="2026-01-15", lang="en")
    assert png is None and caption
