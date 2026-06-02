"""
Jarvis - 개인 베이스라인 (Personal Baseline)
================================================================

개인화의 심장: "남들 기준"이 아니라 "과거의 나" 대비로 판단.

두 가지 핵심:
  1. 구간 비교 (이번 달 vs 지난 달)
     → "나 나아지고 있나, 나빠지고 있나" = 변화의 방향 (저널의 핵심)
  2. 선언 스타일 대비 일탈 감지
     → 사용자가 "스윙"이라 했는데 5분 단타 하면 = 충동 신호

낚시 철학:
  목적은 생존. 기회는 온다. 그때까지 강가에 남아있기.
  베이스라인은 "내가 강가에서 멀어지고 있나"를 측정하는 자.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from statistics import mean, median
from typing import Optional

from app.core.trade_analyzer import Trade


class TradingStyle(str, Enum):
    """사용자가 직접 선언하는 스타일. 기대 보유시간이 다름."""
    SCALPER = "scalper"      # 초단타: 수분~수십분
    DAYTRADER = "daytrader"  # 당일: 수시간
    SWING = "swing"          # 스윙: 수일
    POSITION = "position"    # 포지션: 수주+

    @property
    def expected_hold_minutes(self) -> tuple[float, float]:
        """이 스타일의 정상 보유시간 범위 (분)"""
        return {
            "scalper": (1, 60),
            "daytrader": (30, 1440),
            "swing": (720, 14400),     # 0.5~10일
            "position": (4320, 999999), # 3일+
        }[self.value]


@dataclass
class Baseline:
    """한 구간(예: 한 달)의 '평소의 나' 스냅샷"""
    period_label: str
    n_trades: int
    win_rate: float
    avg_pnl_pct: float
    median_leverage: float
    median_size_vs_avg: float       # 이 구간 내 상대 비중 (항상 ~1.0 근처)
    median_hold_minutes: float
    sl_coverage: float              # 손절 건 거래 비율
    style_consistency: float        # 선언 스타일과 실제 보유시간 일치율 (0~1)


def build_baseline(trades: list[Trade], style: TradingStyle, label: str) -> Optional[Baseline]:
    if not trades:
        return None
    wins = sum(1 for t in trades if t.is_win)
    holds = [t.hold_minutes for t in trades]
    lo, hi = style.expected_hold_minutes
    in_style = sum(1 for h in holds if lo <= h <= hi)

    return Baseline(
        period_label=label,
        n_trades=len(trades),
        win_rate=round(wins / len(trades) * 100, 1),
        avg_pnl_pct=round(mean(t.pnl_pct for t in trades), 2),
        median_leverage=round(median(t.leverage for t in trades), 1),
        median_size_vs_avg=round(median(t.size_vs_avg for t in trades), 2),
        median_hold_minutes=round(median(holds), 1),
        sl_coverage=round(sum(1 for t in trades if t.had_stop_loss) / len(trades), 2),
        style_consistency=round(in_style / len(trades), 2),
    )


# ----------------------------------------------------------------
# 구간 비교 — "나 나아지고 있나?"
# ----------------------------------------------------------------
@dataclass
class PeriodComparison:
    metric: str
    previous: float
    current: float
    direction: str   # "improving" / "worsening" / "stable"
    note: str


def compare_periods(prev: Baseline, curr: Baseline) -> list[PeriodComparison]:
    """지난 구간 vs 이번 구간. 좋아지는 방향인지 판정."""
    out = []

    def cmp(metric, p, c, higher_better, fmt="{:.1f}", unit="", thresh=0.05):
        if p == 0:
            change = 0
        else:
            change = (c - p) / abs(p)
        if abs(change) < thresh:
            direction = "stable"
        elif (change > 0) == higher_better:
            direction = "improving"
        else:
            direction = "worsening"
        note = f"{fmt.format(p)}{unit} → {fmt.format(c)}{unit}"
        return PeriodComparison(metric, p, c, direction, note)

    out.append(cmp("승률", prev.win_rate, curr.win_rate, higher_better=True, unit="%"))
    out.append(cmp("평균손익", prev.avg_pnl_pct, curr.avg_pnl_pct, higher_better=True, fmt="{:+.2f}", unit="%"))
    out.append(cmp("손절 커버리지", prev.sl_coverage, curr.sl_coverage, higher_better=True, fmt="{:.0%}".format if False else "{:.2f}"))
    # 레버리지는 '낮을수록 생존에 유리' — 생존 철학
    out.append(cmp("중앙 레버리지", prev.median_leverage, curr.median_leverage, higher_better=False, unit="x"))
    out.append(cmp("스타일 일관성", prev.style_consistency, curr.style_consistency, higher_better=True, fmt="{:.2f}"))
    return out


# ----------------------------------------------------------------
# 선언 스타일 대비 일탈 감지
# ----------------------------------------------------------------
@dataclass
class StyleDrift:
    drifted: bool
    declared: str
    actual_median_hold: float
    consistency: float
    message: str


def detect_style_drift(curr: Baseline, style: TradingStyle) -> StyleDrift:
    """선언한 스타일과 실제 거래가 어긋나는가 = 충동/일탈 신호"""
    drifted = curr.style_consistency < 0.6
    lo, hi = style.expected_hold_minutes

    if drifted:
        if curr.median_hold_minutes < lo:
            msg = (f"'{style.value}' 스타일로 설정했지만 최근 거래의 절반 이상이 "
                   f"예상보다 짧습니다 (중앙 {curr.median_hold_minutes:.0f}분). "
                   f"조급하게 들어가고 있을 수 있습니다.")
        else:
            msg = (f"'{style.value}' 스타일 대비 보유가 길어지고 있습니다 "
                   f"(중앙 {curr.median_hold_minutes:.0f}분). 손절을 미루고 있진 않나요?")
    else:
        msg = f"선언한 '{style.value}' 스타일과 실제 거래가 일치합니다 (일관성 {curr.style_consistency:.0%})."

    return StyleDrift(drifted, style.value, curr.median_hold_minutes,
                      curr.style_consistency, msg)


# ----------------------------------------------------------------
# 개인 기준 일탈 평가 — "당신 기준으로 비정상"
# ----------------------------------------------------------------
def evaluate_against_baseline(trade: Trade, baseline: Baseline) -> list[str]:
    """
    새 거래를 '이 사람의 베이스라인' 대비로 평가.
    고정 기준이 아니라 본인 평소 대비 일탈을 짚음.
    """
    flags = []
    # 레버리지가 평소의 1.5배 이상
    if baseline.median_leverage > 0 and trade.leverage > baseline.median_leverage * 1.5:
        flags.append(f"평소 {baseline.median_leverage}x인데 이번엔 {trade.leverage}x "
                     f"— 당신 기준으로 과한 레버리지입니다.")
    # 비중이 평소의 2배 이상
    if trade.size_vs_avg > baseline.median_size_vs_avg * 2:
        flags.append(f"평소보다 {trade.size_vs_avg/max(baseline.median_size_vs_avg,0.1):.1f}배 큰 비중입니다.")
    # 손절을 평소엔 거는데 이번엔 안 걸었다
    if baseline.sl_coverage > 0.5 and not trade.had_stop_loss:
        flags.append(f"평소 {baseline.sl_coverage:.0%}는 손절을 거는데 이번엔 없습니다.")
    return flags


# ================================================================
# 검증
# ================================================================
if __name__ == "__main__":
    from datetime import timezone
    import random
    rnd = random.Random(5)

    def mk_trades(n, win_p, lev, hold_min, sl_p, base_t):
        ts = []
        clock = base_t
        for _ in range(n):
            clock += timedelta(hours=rnd.uniform(1, 8))
            win = rnd.random() < win_p
            pnl = rnd.uniform(1, 5) if win else -rnd.uniform(1, 5)
            hold = timedelta(minutes=hold_min * rnd.uniform(0.5, 1.5))
            ts.append(Trade("BTCUSDT", "LONG", clock, clock+hold,
                            round(pnl,2), lev*rnd.uniform(0.8,1.2),
                            rnd.uniform(0.8,1.3), rnd.random() < sl_p))
        return ts

    print("=== 개인 베이스라인 검증 ===\n")
    style = TradingStyle.SWING

    # 지난달: 잘하던 시절 (승률 높고, 손절 잘 걸고, 스윙답게 길게)
    prev_t = mk_trades(30, 0.60, 8, 1500, 0.8, datetime(2025,12,1,tzinfo=timezone.utc))
    prev = build_baseline(prev_t, style, "2025-12")

    # 이번달: 무너지는 중 (승률 하락, 손절 안 걸고, 레버리지 올리고, 짧아짐 = 스캘핑화)
    curr_t = mk_trades(30, 0.40, 18, 200, 0.3, datetime(2026,1,1,tzinfo=timezone.utc))
    curr = build_baseline(curr_t, style, "2026-01")

    print(f"[지난달] 승률 {prev.win_rate}% | 레버 {prev.median_leverage}x | "
          f"손절 {prev.sl_coverage:.0%} | 보유 {prev.median_hold_minutes:.0f}분 | 일관성 {prev.style_consistency:.0%}")
    print(f"[이번달] 승률 {curr.win_rate}% | 레버 {curr.median_leverage}x | "
          f"손절 {curr.sl_coverage:.0%} | 보유 {curr.median_hold_minutes:.0f}분 | 일관성 {curr.style_consistency:.0%}\n")

    print("[구간 비교 — 나 나아지고 있나?]")
    for c in compare_periods(prev, curr):
        arrow = {"improving":"↑ 개선","worsening":"↓ 악화","stable":"= 유지"}[c.direction]
        print(f"  {c.metric:12s} {c.note:20s} {arrow}")
    print()

    print("[스타일 일탈 감지]")
    drift = detect_style_drift(curr, style)
    print(f"  {drift.message}\n")

    print("[새 거래 — 본인 기준 평가]")
    new_trade = Trade("ETHUSDT", "LONG", datetime(2026,1,20,tzinfo=timezone.utc),
                      datetime(2026,1,20,0,30,tzinfo=timezone.utc),
                      -3.0, leverage=25, size_vs_avg=2.5, had_stop_loss=False)
    for f in evaluate_against_baseline(new_trade, prev):  # 잘하던 시절 기준으로
        print(f"  · {f}")

    print("\n✅ 개인 베이스라인 검증 완료")
