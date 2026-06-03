"""
Phase 3 worker — per-user polling loop that emits real-time warnings and
mistake-notebook prompts.

═══════════════════════════════════════════════════════════════════════════
SCALING BOUNDARY (the one place this design is deliberately MVP-shaped):

  This is a single-process loop that iterates connected users and polls each
  in turn. It is intentionally simple and correct for launch. The warning /
  notebook / assessment LOGIC is fully decoupled from the polling mechanism:
  `process_snapshot()` takes an already-fetched snapshot and pure dependencies,
  so moving to a queue/scheduler-based dispatch (one job per user, workers
  pulling from a queue) means rewriting only `run_forever()` — not a single
  line of the warning logic, gating, or message building.

  Two things must change together when we scale out:
    1. state.violation_store / state.pending_tags → Redis-backed (see state.py).
    2. The exchange RateLimiter (see note below) → per-key/per-IP scoping.

RATE LIMITER SCOPING (confirmed):
  exchange.BinanceFuturesClient._shared_limiter is a CLASS attribute — it is
  GLOBAL across every user/instance in this process. For the single-IP MVP
  that's actually what we want: Binance limits are per-IP, and all users'
  calls leave from one server IP, so a shared bucket protects that IP's budget.
  BUT it means one busy user's polling consumes tokens that briefly delay
  another's (fair-ish, since acquire() blocks rather than drops). When we scale
  to multiple egress IPs / per-key budgets, the limiter must move from a shared
  class attribute to a per-(IP or key) instance — keyed in a registry — so one
  user can't throttle another. Flagged here so it isn't a surprise.
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

from app.config import get_settings
from app.core.jarvis_assess import assess
from app.core.jarvis_score import AccountContext, BehaviorSignals
from app.core.risk_math import compute_risk
from app.core.rule_engine import ViolationStore
from app.services.exchange import AccountSnapshot, ExchangeError, make_exchange_client
from app.services.stoploss_tracker import StopLossObservation, StopLossTracker
from app.telegram import notebook, warnings
from app.telegram.notifier import Notifier
from app.telegram.state import pending_tags, violation_store

log = logging.getLogger("jarvis.worker")

# Behavior signals (loss streak, re-entry speed, size-vs-usual) come from the
# trader's own persisted history via behavior.TradeBehaviorProvider. The seam is
# `(user_id, live_effective_leverage) -> BehaviorSignals`: history-derived parts
# are cached/refreshed by the provider, the live leverage is the one input that
# only the current position can supply. `neutral_behavior` is the fallback
# (used in tests / before any history loads) — the safety-critical DAMAGE
# GUARDRAIL works fully from position math regardless of behavior.
BehaviorProvider = Callable[[int, float], BehaviorSignals]


def neutral_behavior(_user_id: int, _live_effective_leverage: float) -> BehaviorSignals:
    return BehaviorSignals(recent_losses_streak=0, minutes_since_last_loss=999,
                           trades_last_30min=0, position_size_vs_avg=1.0)


@dataclass
class _SeenPosition:
    qty: Decimal
    entry_time: datetime
    last_unrealized_pct: float
    had_stop_loss: bool


@dataclass
class WorkerContext:
    notifier: Notifier
    sl_tracker: StopLossTracker
    vstore: ViolationStore = violation_store
    behavior_for: BehaviorProvider = neutral_behavior
    # Optional cached provider; when set, poll_user keeps its per-user cache
    # fresh (on an interval, not per-poll) and behavior_for reads from it.
    behavior_provider: object | None = None
    # per-(user,symbol) last-seen state, for close detection + notebook prompts
    _seen: dict[tuple[int, str], _SeenPosition] = field(default_factory=dict)


def _account_context(snapshot: AccountSnapshot) -> AccountContext:
    """Build the AccountContext jarvis_assess needs from a live snapshot."""
    total_liq_loss = 0.0
    long_notional = 0.0
    short_notional = 0.0
    for p in snapshot.positions:
        m = compute_risk(p, snapshot.equity)
        total_liq_loss += m.liq_loss_pct_of_equity
        if str(p.side).endswith("LONG"):
            long_notional += m.notional_usd
        else:
            short_notional += m.notional_usd
    total = long_notional + short_notional
    correlated = (max(long_notional, short_notional) / total * 100) if total else 0.0
    return AccountContext(
        equity=snapshot.equity,
        total_liq_loss_pct=round(total_liq_loss, 1),
        correlated_exposure_pct=round(correlated, 1),
    )


async def process_snapshot(
    ctx: WorkerContext, user_id: int, snapshot: AccountSnapshot,
    *, lang: Optional[str] = None,
) -> None:
    """Pure-ish handler: assess each open position, gate+send warnings, track
    stop-loss observations, and emit notebook prompts on close.

    Decoupled from polling so it can be driven by any dispatch mechanism.
    """
    now = datetime.now(timezone.utc)
    acct = _account_context(snapshot)
    live_symbols: set[str] = set()

    for pos in snapshot.positions:
        symbol = pos.symbol
        live_symbols.add(symbol)
        m = compute_risk(pos, snapshot.equity)

        # 1) record a stop-loss observation for this snapshot
        ctx.sl_tracker.observe(StopLossObservation(
            user_id=user_id, symbol=symbol, observed_at=now,
            has_stop_loss=m.has_stop_loss, liq_distance_pct=m.liq_distance_pct,
            position_qty=pos.quantity,
        ))

        # 2) assess → warnings, hysteresis-gated, then send (text only).
        #    Behavior is derived from the user's own history (cached); the live
        #    effective leverage is the bet-size input only this position knows.
        behavior = ctx.behavior_for(user_id, m.effective_leverage)
        a = assess(m, acct, behavior, atr_pct=None)
        msgs = warnings.build_warnings(a, lang=lang)
        for w in warnings.gate_warnings(ctx.vstore, user_id, symbol, msgs):
            chat = await _chat_id(user_id)
            if chat is not None:
                await ctx.notifier.send_text(chat, w.text)

        # 3) remember last-seen state for close detection
        unrl_pct = float(pos.unrealized_pnl / snapshot.equity * 100) if snapshot.equity else 0.0
        ctx._seen[(user_id, symbol)] = _SeenPosition(
            qty=pos.quantity,
            entry_time=ctx._seen.get((user_id, symbol),
                                     _SeenPosition(pos.quantity, now, 0.0, m.has_stop_loss)).entry_time,
            last_unrealized_pct=round(unrl_pct, 2),
            had_stop_loss=m.has_stop_loss,
        )

    # 4) detect closed positions → notebook prompt + clear warning hysteresis
    for (uid, sym), seen in list(ctx._seen.items()):
        if uid != user_id or sym in live_symbols:
            continue
        await _on_close(ctx, user_id, sym, seen, now, lang=lang)
        del ctx._seen[(uid, sym)]

    # 5) finalize stop-loss verdicts for vanished positions
    ctx.sl_tracker.reconcile(user_id, live_symbols)


async def _on_close(
    ctx: WorkerContext, user_id: int, symbol: str, seen: _SeenPosition,
    now: datetime, *, lang: Optional[str],
) -> None:
    warnings.clear_warnings(ctx.vstore, user_id, symbol)
    # The fill-accurate realized P&L lives in fills; the last observed
    # unrealized % is a close-enough proxy for the prompt headline (the user
    # tags WHY, not the exact number, which the web shows precisely).
    tag = notebook.PendingTag(
        user_id=user_id, trade_id=f"{symbol}-{seen.entry_time.isoformat()}",
        symbol=symbol, side="", pnl_pct=seen.last_unrealized_pct,
        closed_at=now, followed_stop_loss=seen.had_stop_loss,
    )
    code = pending_tags.put(tag)
    text, rows = notebook.build_prompt(tag, code, lang=lang)
    chat = await _chat_id(user_id)
    if chat is not None:
        await ctx.notifier.send_text(chat, text, buttons=rows)


async def _chat_id(user_id: int) -> Optional[int]:
    from app.db.base import get_sessionmaker
    from app.db import models
    from sqlalchemy import select
    async with get_sessionmaker()() as s:
        u = (await s.execute(
            select(models.User).where(models.User.id == user_id)
        )).scalar_one_or_none()
        return u.telegram_chat_id if u else None


async def _trading_style(user_id: int):
    """User's declared trading style, defaulting to SWING if unset/unknown."""
    from app.core.baseline import TradingStyle
    from app.db.base import get_sessionmaker
    from app.db import models
    from sqlalchemy import select
    async with get_sessionmaker()() as s:
        u = (await s.execute(
            select(models.User).where(models.User.id == user_id)
        )).scalar_one_or_none()
    try:
        return TradingStyle(u.trading_style) if u and u.trading_style else TradingStyle.SWING
    except ValueError:
        return TradingStyle.SWING


# ── polling mechanism (the part that changes when we scale out) ──────────────

async def poll_user(ctx: WorkerContext, user_id: int, *, lang: Optional[str] = None) -> None:
    """Fetch one user's snapshot and process it. Errors are isolated per user."""
    from app.db.persistence import load_credential
    creds = await load_credential(user_id)
    if creds is None:
        return

    # Keep the user's behavioral baseline warm (DB load only when stale — the
    # provider's interval guard makes this a no-op on most polls).
    if ctx.behavior_provider is not None:
        style = await _trading_style(user_id)
        await ctx.behavior_provider.ensure_fresh(user_id, style)

    api_key, api_secret = creds
    client = make_exchange_client(
        get_settings().EXCHANGE, api_key, api_secret,
        testnet=get_settings().BINANCE_TESTNET,
    )
    try:
        snapshot = await client.fetch_account_snapshot()
        await process_snapshot(ctx, user_id, snapshot, lang=lang)
    except ExchangeError as e:
        # one user's exchange error must not stop the loop
        log.warning("poll_user %s exchange error: %s (retryable=%s)",
                    user_id, e, e.retryable)
    finally:
        await client.close()


async def _connected_user_ids() -> list[int]:
    from app.db.base import get_sessionmaker
    from app.db import models
    from sqlalchemy import select
    async with get_sessionmaker()() as s:
        rows = (await s.execute(
            select(models.User.id).where(models.User.telegram_chat_id.isnot(None))
        )).scalars().all()
        return list(rows)


async def run_forever(ctx: WorkerContext) -> None:
    """MVP single-process loop. See SCALING BOUNDARY at the top of this module."""
    interval = get_settings().WORKER_POLL_INTERVAL_SEC
    log.info("worker loop starting (interval=%ss)", interval)
    while True:
        try:
            for uid in await _connected_user_ids():
                await poll_user(ctx, uid)
        except Exception:  # never let the loop die
            log.exception("worker cycle error")
        await asyncio.sleep(interval)
