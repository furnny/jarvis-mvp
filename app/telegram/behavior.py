"""
Real behavior signals from the trader's OWN persisted history.

This is the core differentiator — impulse detection — so it must not stay
dormant behind the neutral default. It derives BehaviorSignals (loss-streak,
re-entry speed, over-trading, size-vs-usual) from persisted closed trades +
the baseline, using ONLY existing modules (baseline.py / trade_analyzer.py):
no separate analysis path.

Cost discipline: the heavy part (DB load + baseline build) is cached per user
and refreshed on an interval (BEHAVIOR_REFRESH_SEC), NOT recomputed every 30s
poll. The cheap, time-sensitive parts (minutes-since-loss, trades-in-last-30min,
size-vs-usual for the live position) are recomputed from the cache on each call
so they stay current between refreshes without touching the DB.

Mapping to jarvis_score.BehaviorSignals:
  recent_losses_streak   ← trailing consecutive losing closed trades
  minutes_since_last_loss← now − last losing trade's exit_time
  trades_last_30min      ← closed trades whose exit falls in the last 30 min
  position_size_vs_avg   ← live effective leverage ÷ baseline median leverage
                            (notional isn't persisted; effective leverage is the
                             account-relative bet-size signal baseline supports,
                             and is exactly what evaluate_against_baseline uses)
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from app.config import get_settings
from app.core.baseline import TradingStyle, build_baseline
from app.core.jarvis_score import BehaviorSignals
from app.core.trade_analyzer import Trade

# How long a cached per-user behavior baseline stays fresh before reload.
BEHAVIOR_REFRESH_SEC = 600  # 10 min — well above the 30s poll cadence

# How many recent closed trades to base the behavioral reference on.
_RECENT_WINDOW = 50


@dataclass
class _Cached:
    built_at: float
    last_loss_exit: Optional[datetime]      # for minutes_since_last_loss
    loss_streak: int                        # trailing consecutive losses
    recent_exits: list[datetime]            # for trades_last_30min
    reference_leverage: float               # baseline median leverage (>0 or 1.0)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _trailing_loss_streak(trades_by_recent: list[Trade]) -> int:
    streak = 0
    for t in trades_by_recent:
        if t.pnl_pct <= 0:
            streak += 1
        else:
            break
    return streak


def build_cache(trades: list[Trade], style: TradingStyle) -> _Cached:
    """Pure: turn persisted closed trades into the cached behavioral reference."""
    ordered = sorted(trades, key=lambda t: _as_utc(t.exit_time))
    recent = ordered[-_RECENT_WINDOW:]
    by_recent = list(reversed(recent))  # most-recent first

    last_loss_exit = next((_as_utc(t.exit_time) for t in by_recent if t.pnl_pct <= 0), None)
    streak = _trailing_loss_streak(by_recent)

    baseline = build_baseline(recent, style, "behavior") if recent else None
    ref_lev = baseline.median_leverage if baseline and baseline.median_leverage > 0 else 1.0

    return _Cached(
        built_at=time.monotonic(),
        last_loss_exit=last_loss_exit,
        loss_streak=streak,
        recent_exits=[_as_utc(t.exit_time) for t in recent],
        reference_leverage=ref_lev,
    )


def signals_from_cache(
    cache: _Cached, *, live_effective_leverage: float, now: Optional[datetime] = None
) -> BehaviorSignals:
    """Pure: combine the cached reference with the live position to produce the
    time-sensitive BehaviorSignals (no DB access)."""
    now = now or _now()

    if cache.last_loss_exit is not None:
        mins = max(0.0, (now - cache.last_loss_exit).total_seconds() / 60)
    else:
        mins = 999.0

    cutoff = now.timestamp() - 30 * 60
    trades_30 = sum(1 for e in cache.recent_exits if e.timestamp() >= cutoff)

    size_vs_avg = (live_effective_leverage / cache.reference_leverage
                   if cache.reference_leverage > 0 else 1.0)

    return BehaviorSignals(
        recent_losses_streak=cache.loss_streak,
        minutes_since_last_loss=round(mins, 1),
        trades_last_30min=trades_30,
        position_size_vs_avg=round(size_vs_avg, 2),
    )


class TradeBehaviorProvider:
    """Per-user cached behavior provider sourced from persisted trades.

    Usage from the worker:
        await provider.ensure_fresh(user_id, style)      # cheap unless stale
        sig = provider.signals(user_id, live_effective_leverage)

    SCALING NOTE: the cache is process-local, like the other worker state. When
    the worker scales out, back this with the same Redis store the hysteresis /
    pending-tag state moves to — the build/derive functions above stay pure.
    """

    def __init__(self, *, refresh_sec: int = BEHAVIOR_REFRESH_SEC):
        self._refresh_sec = refresh_sec
        self._cache: dict[int, _Cached] = {}

    def _is_stale(self, user_id: int) -> bool:
        c = self._cache.get(user_id)
        return c is None or (time.monotonic() - c.built_at) >= self._refresh_sec

    async def ensure_fresh(self, user_id: int, style: TradingStyle) -> None:
        if not self._is_stale(user_id):
            return
        from app.db.base import get_sessionmaker
        from app.db.stores import PostgresTradeStore
        trades = await PostgresTradeStore(get_sessionmaker()).get_trades(user_id)
        self._cache[user_id] = build_cache(trades, style)

    def signals(self, user_id: int, live_effective_leverage: float) -> BehaviorSignals:
        cache = self._cache.get(user_id)
        if cache is None:
            # No history loaded yet → neutral (withhold behavioral judgment).
            return BehaviorSignals(0, 999.0, 0, 1.0)
        return signals_from_cache(cache, live_effective_leverage=live_effective_leverage)


if __name__ == "__main__":
    # self-test: synthetic closed trades → expected behavioral signals
    from datetime import timedelta

    base = _now()
    def mk(mins_ago, pnl, lev):
        t = base - timedelta(minutes=mins_ago)
        return Trade("BTCUSDT", "LONG", t - timedelta(minutes=5), t,
                     pnl_pct=pnl, leverage=lev, size_vs_avg=1.0, had_stop_loss=True)

    # most recent three are losses; last loss closed 4 min ago; usual lev ~5x
    trades = [mk(120, 2.0, 5), mk(60, 1.0, 5), mk(20, -1.5, 5),
              mk(10, -2.0, 5), mk(4, -1.0, 5)]
    cache = build_cache(trades, TradingStyle.SWING)
    sig = signals_from_cache(cache, live_effective_leverage=14.0, now=base)
    print("loss streak:", sig.recent_losses_streak, "(expect 3)")
    print("mins since loss:", sig.minutes_since_last_loss, "(expect ~4)")
    print("trades last 30m:", sig.trades_last_30min, "(expect 3)")
    print("size vs avg:", sig.position_size_vs_avg, "(expect ~2.8 = 14/5)")
    assert sig.recent_losses_streak == 3
    assert 3.5 <= sig.minutes_since_last_loss <= 4.5
    assert sig.trades_last_30min == 3
    assert abs(sig.position_size_vs_avg - 2.8) < 0.01
    print("✅ behavior provider self-test passed")
