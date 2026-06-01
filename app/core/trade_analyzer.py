"""
Trade Analyzer — situation-aware win-rate breakdown + auto insights.

Slices:
  by_loss_streak   — performance while on a losing streak vs fresh
  by_size          — small / medium / large position (vs personal median)
  by_stop_loss     — trades with SL vs without (from real-time observations)
  by_leverage      — low / mid / high leverage buckets
  insights         — auto-extracted weakness strings
"""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional
import statistics


@dataclass
class Trade:
    """Minimal trade record for analysis."""
    symbol: str
    entry_time: object          # datetime
    exit_time: object           # datetime
    pnl_pct: Decimal            # % of account equity (signed)
    leverage: Decimal
    notional: Decimal           # entry_price × qty
    has_stop_loss: bool = False
    win: bool = False


@dataclass
class SliceStats:
    label: str
    n: int
    win_rate: float   # 0-100
    avg_pnl: float    # % signed


def _stats(label: str, trades: List[Trade]) -> SliceStats:
    if not trades:
        return SliceStats(label, 0, 0.0, 0.0)
    wins = sum(1 for t in trades if t.win)
    wr = wins / len(trades) * 100
    avg = float(sum(t.pnl_pct for t in trades)) / len(trades)
    return SliceStats(label, len(trades), round(wr, 1), round(avg, 2))


@dataclass
class OverallStats:
    n: int
    win_rate: float
    avg_pnl: float
    total_pnl: float


class TradeAnalyzer:
    def __init__(self, trades: List[Trade]):
        # Sort by entry time
        self._trades: List[Trade] = sorted(trades, key=lambda t: t.entry_time)

    # ── Overall ───────────────────────────────────────────────────────────────

    def overall(self) -> OverallStats:
        t = self._trades
        if not t:
            return OverallStats(0, 0.0, 0.0, 0.0)
        wins = sum(1 for x in t if x.win)
        wr = wins / len(t) * 100
        avg = float(sum(x.pnl_pct for x in t)) / len(t)
        total = float(sum(x.pnl_pct for x in t))
        return OverallStats(len(t), round(wr, 1), round(avg, 2), round(total, 2))

    # ── Slices ────────────────────────────────────────────────────────────────

    def by_loss_streak(self) -> List[SliceStats]:
        """Split by consecutive loss count before entry (0, 1, 2+)."""
        buckets: dict[str, List[Trade]] = {"연패 없음": [], "1연패 후": [], "2+연패 후": []}
        streak = 0
        for trade in self._trades:
            label = "연패 없음" if streak == 0 else ("1연패 후" if streak == 1 else "2+연패 후")
            buckets[label].append(trade)
            streak = streak + 1 if not trade.win else 0
        return [_stats(k, v) for k, v in buckets.items()]

    def by_size(self) -> List[SliceStats]:
        """Small / medium / large relative to median notional."""
        notionals = [float(t.notional) for t in self._trades]
        if not notionals:
            return []
        med = statistics.median(notionals)
        small, mid, large = [], [], []
        for t in self._trades:
            n = float(t.notional)
            if n < med * 0.7:
                small.append(t)
            elif n > med * 1.5:
                large.append(t)
            else:
                mid.append(t)
        return [
            _stats("소형 비중", small),
            _stats("중형 비중", mid),
            _stats("대형 비중", large),
        ]

    def by_stop_loss(self) -> List[SliceStats]:
        with_sl = [t for t in self._trades if t.has_stop_loss]
        without_sl = [t for t in self._trades if not t.has_stop_loss]
        return [
            _stats("손절 있음", with_sl),
            _stats("손절 없음", without_sl),
        ]

    def by_leverage(self) -> List[SliceStats]:
        low = [t for t in self._trades if t.leverage <= Decimal("5")]
        mid = [t for t in self._trades if Decimal("5") < t.leverage <= Decimal("15")]
        high = [t for t in self._trades if t.leverage > Decimal("15")]
        return [
            _stats("저레버 (≤5x)", low),
            _stats("중레버 (6-15x)", mid),
            _stats("고레버 (>15x)", high),
        ]

    # ── Insights ──────────────────────────────────────────────────────────────

    def insights(self) -> List[str]:
        msgs: List[str] = []

        sl_stats = self.by_stop_loss()
        if len(sl_stats) == 2 and sl_stats[0].n >= 3 and sl_stats[1].n >= 3:
            diff = sl_stats[0].win_rate - sl_stats[1].win_rate
            if diff >= 15:
                msgs.append(
                    f"손절 있을 때 승률 {sl_stats[0].win_rate:.0f}% vs 없을 때 {sl_stats[1].win_rate:.0f}% "
                    f"— 손절이 {diff:.0f}%p 더 좋습니다."
                )

        streak_stats = self.by_loss_streak()
        streak_map = {s.label: s for s in streak_stats}
        fresh = streak_map.get("연패 없음")
        after2 = streak_map.get("2+연패 후")
        if fresh and after2 and fresh.n >= 3 and after2.n >= 3:
            if fresh.win_rate - after2.win_rate >= 15:
                msgs.append(
                    f"2연패 이후 승률 {after2.win_rate:.0f}% — 평소({fresh.win_rate:.0f}%)보다 "
                    f"{fresh.win_rate - after2.win_rate:.0f}%p 낮습니다. 복수 매매 주의."
                )

        size_stats = self.by_size()
        size_map = {s.label: s for s in size_stats}
        sm = size_map.get("소형 비중")
        lg = size_map.get("대형 비중")
        if sm and lg and sm.n >= 3 and lg.n >= 3:
            if sm.win_rate - lg.win_rate >= 15:
                msgs.append(
                    f"대형 비중 승률 {lg.win_rate:.0f}% vs 소형 {sm.win_rate:.0f}% "
                    f"— 클수록 성과가 나빠집니다."
                )

        lev_stats = self.by_leverage()
        lev_map = {s.label: s for s in lev_stats}
        low_lev = lev_map.get("저레버 (≤5x)")
        high_lev = lev_map.get("고레버 (>15x)")
        if low_lev and high_lev and low_lev.n >= 3 and high_lev.n >= 3:
            if low_lev.win_rate - high_lev.win_rate >= 15:
                msgs.append(
                    f"고레버 승률 {high_lev.win_rate:.0f}% — 저레버({low_lev.win_rate:.0f}%)보다 낮습니다."
                )

        if not msgs:
            msgs.append("아직 뚜렷한 약점 패턴이 없습니다. 데이터가 쌓일수록 정밀해집니다.")

        return msgs
