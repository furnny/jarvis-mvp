"""
Stop-Loss Tracker — real-time observations fill the gap that trade history misses.

Trade history only tells you what happened at open/close.
The worker snapshots mid-position to record whether a SL was actually set.
This module stores those observations and later enriches Trade records.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Set, Tuple

from app.core.trade_analyzer import Trade


# ── Observation ───────────────────────────────────────────────────────────────

@dataclass
class StopLossObservation:
    user_id: int
    symbol: str
    observed_at: datetime
    has_stop_loss: bool
    liq_distance_pct: float
    position_qty: Decimal


# ── Store interface ───────────────────────────────────────────────────────────

class SLStore:
    def add(self, obs: StopLossObservation) -> None: ...
    def get(self, user_id: int, symbol: str) -> List[StopLossObservation]: ...
    def clear(self, user_id: int, symbol: str) -> None: ...
    def all_symbols(self, user_id: int) -> Set[str]: ...


class InMemorySLStore(SLStore):
    def __init__(self):
        # key: (user_id, symbol) → list of observations
        self._data: Dict[Tuple[int, str], List[StopLossObservation]] = {}

    def add(self, obs: StopLossObservation) -> None:
        key = (obs.user_id, obs.symbol)
        self._data.setdefault(key, []).append(obs)

    def get(self, user_id: int, symbol: str) -> List[StopLossObservation]:
        return self._data.get((user_id, symbol), [])

    def clear(self, user_id: int, symbol: str) -> None:
        self._data.pop((user_id, symbol), None)

    def all_symbols(self, user_id: int) -> Set[str]:
        return {sym for (uid, sym) in self._data if uid == user_id}


# ── Tracker ───────────────────────────────────────────────────────────────────

class StopLossTracker:
    """Worker calls observe() each snapshot; reconcile() clears closed symbols."""

    def __init__(self, store: SLStore):
        self._store = store

    def observe(self, obs: StopLossObservation) -> None:
        self._store.add(obs)

    def reconcile(self, user_id: int, live_symbols: Set[str]) -> None:
        """Remove observations for symbols no longer open."""
        stale = self._store.all_symbols(user_id) - live_symbols
        for sym in stale:
            self._store.clear(user_id, sym)

    def had_stop_loss(self, user_id: int, symbol: str) -> bool:
        """
        Returns True if a majority of observations for this symbol had a SL set.
        Majority rule is robust to brief gaps in SL coverage.
        """
        obs = self._store.get(user_id, symbol)
        if not obs:
            return False
        with_sl = sum(1 for o in obs if o.has_stop_loss)
        return with_sl / len(obs) >= 0.5


# ── Enrichment ────────────────────────────────────────────────────────────────

def enrich_trades_with_sl(
    trades: List[Trade],
    user_id: int,
    store: SLStore,
) -> Tuple[List[Trade], int]:
    """
    Set Trade.has_stop_loss based on stored observations.
    Returns (enriched_trades, match_count).
    """
    matched = 0
    for trade in trades:
        obs = store.get(user_id, trade.symbol)
        # Match observations that overlap with trade's time window
        relevant = [
            o for o in obs
            if trade.entry_time <= o.observed_at <= trade.exit_time
        ]
        if relevant:
            with_sl = sum(1 for o in relevant if o.has_stop_loss)
            trade.has_stop_loss = with_sl / len(relevant) >= 0.5
            matched += 1
    return trades, matched
