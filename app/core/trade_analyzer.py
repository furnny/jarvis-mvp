"""
Jarvis 거래 내역 분석기 — "나는 어떤 상황에서 잘하고 못하는가"
================================================================

사용자가 진짜 원한 것:
  실시간 경고가 아니라, 과거 거래를 쭉 분석해서
  '어떤 상황에서 승률이 어떤지'를 거울처럼 보여주는 것.

분석 축:
  [행동] 연패 직후 / 비중 / 거래빈도
  [시간] 요일 / 시간대 / 보유기간
  [구조] 손절 유무 / 레버리지 / 롱숏 / 종목

핵심 가치:
  막연한 "충동매매 하지마"가 아니라
  "당신은 연패 직후 승률이 X%로 떨어진다"는 본인 데이터로 직면.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Optional


@dataclass
class Trade:
    """정규화된 거래 한 건 (CSV/API에서 변환됨)"""
    symbol: str
    side: str               # LONG / SHORT
    entry_time: datetime
    exit_time: datetime
    pnl_pct: float          # 자본 대비 손익% (수수료 포함)
    leverage: float
    size_vs_avg: float      # 평소 대비 비중 배수
    had_stop_loss: bool

    @property
    def is_win(self) -> bool:
        return self.pnl_pct > 0

    @property
    def hold_minutes(self) -> float:
        return (self.exit_time - self.entry_time).total_seconds() / 60


@dataclass
class SegmentStat:
    """한 상황(세그먼트)의 통계"""
    label: str
    n: int
    win_rate: float
    avg_pnl: float
    total_pnl: float


def _stat(label: str, trades: list[Trade]) -> Optional[SegmentStat]:
    if not trades:
        return None
    wins = sum(1 for t in trades if t.is_win)
    return SegmentStat(
        label=label,
        n=len(trades),
        win_rate=round(wins / len(trades) * 100, 1),
        avg_pnl=round(mean(t.pnl_pct for t in trades), 2),
        total_pnl=round(sum(t.pnl_pct for t in trades), 1),
    )


class TradeAnalyzer:
    def __init__(self, trades: list[Trade]):
        # 시간순 정렬 (연패 계산에 필요)
        self.trades = sorted(trades, key=lambda t: t.entry_time)
        self._tag_streaks()

    def _tag_streaks(self):
        """각 거래 직전의 연패 횟수와 마지막 거래로부터의 간격을 태깅"""
        self.loss_streak_before = []
        self.minutes_since_prev = []
        streak = 0
        prev_exit = None
        for t in self.trades:
            self.loss_streak_before.append(streak)
            if prev_exit:
                gap = (t.entry_time - prev_exit).total_seconds() / 60
            else:
                gap = 9999
            self.minutes_since_prev.append(gap)
            # 업데이트
            streak = streak + 1 if not t.is_win else 0
            prev_exit = t.exit_time

    def overall(self) -> SegmentStat:
        return _stat("전체", self.trades)

    # ---------- 행동 축 ----------
    def by_loss_streak(self) -> list[SegmentStat]:
        groups = {"평상시 (연패 0)": [], "연패 1회 후": [], "연패 2회+ 후": []}
        for t, s in zip(self.trades, self.loss_streak_before):
            if s == 0: groups["평상시 (연패 0)"].append(t)
            elif s == 1: groups["연패 1회 후"].append(t)
            else: groups["연패 2회+ 후"].append(t)
        return [r for r in (_stat(k, v) for k, v in groups.items()) if r]

    def by_size(self) -> list[SegmentStat]:
        groups = {"작은 비중 (<1x)": [], "보통 (1~1.5x)": [], "큰 비중 (1.5x+)": []}
        for t in self.trades:
            if t.size_vs_avg < 1.0: groups["작은 비중 (<1x)"].append(t)
            elif t.size_vs_avg < 1.5: groups["보통 (1~1.5x)"].append(t)
            else: groups["큰 비중 (1.5x+)"].append(t)
        return [r for r in (_stat(k, v) for k, v in groups.items()) if r]

    def by_reentry_speed(self) -> list[SegmentStat]:
        groups = {"즉시 재진입 (<10분)": [], "여유 (10분+)": []}
        for t, g in zip(self.trades, self.minutes_since_prev):
            if g < 10: groups["즉시 재진입 (<10분)"].append(t)
            else: groups["여유 (10분+)"].append(t)
        return [r for r in (_stat(k, v) for k, v in groups.items()) if r]

    # ---------- 시간 축 ----------
    def by_hour_bucket(self) -> list[SegmentStat]:
        buckets = {"새벽 (0~6시)": [], "오전 (6~12시)": [], "오후 (12~18시)": [], "저녁 (18~24시)": []}
        for t in self.trades:
            h = t.entry_time.hour
            if h < 6: buckets["새벽 (0~6시)"].append(t)
            elif h < 12: buckets["오전 (6~12시)"].append(t)
            elif h < 18: buckets["오후 (12~18시)"].append(t)
            else: buckets["저녁 (18~24시)"].append(t)
        return [r for r in (_stat(k, v) for k, v in buckets.items()) if r]

    def by_hold_time(self) -> list[SegmentStat]:
        groups = {"단타 (<1시간)": [], "당일 (1~24시간)": [], "스윙 (1일+)": []}
        for t in self.trades:
            m = t.hold_minutes
            if m < 60: groups["단타 (<1시간)"].append(t)
            elif m < 1440: groups["당일 (1~24시간)"].append(t)
            else: groups["스윙 (1일+)"].append(t)
        return [r for r in (_stat(k, v) for k, v in groups.items()) if r]

    # ---------- 구조 축 ----------
    def by_stop_loss(self) -> list[SegmentStat]:
        with_sl = [t for t in self.trades if t.had_stop_loss]
        without = [t for t in self.trades if not t.had_stop_loss]
        return [r for r in (_stat("손절 있음", with_sl), _stat("손절 없음", without)) if r]

    def by_leverage(self) -> list[SegmentStat]:
        groups = {"저레버 (<5x)": [], "중레버 (5~10x)": [], "고레버 (10x+)": []}
        for t in self.trades:
            if t.leverage < 5: groups["저레버 (<5x)"].append(t)
            elif t.leverage < 10: groups["중레버 (5~10x)"].append(t)
            else: groups["고레버 (10x+)"].append(t)
        return [r for r in (_stat(k, v) for k, v in groups.items()) if r]

    def by_side(self) -> list[SegmentStat]:
        longs = [t for t in self.trades if t.side == "LONG"]
        shorts = [t for t in self.trades if t.side == "SHORT"]
        return [r for r in (_stat("롱", longs), _stat("숏", shorts)) if r]

    def by_symbol(self, top=5) -> list[SegmentStat]:
        from collections import defaultdict
        groups = defaultdict(list)
        for t in self.trades:
            groups[t.symbol].append(t)
        stats = [s for s in (_stat(k, v) for k, v in groups.items()) if s]
        return sorted(stats, key=lambda s: -s.n)[:top]

    # ---------- 인사이트 자동 추출 ----------
    def insights(self) -> list[str]:
        """가장 두드러진 약점/강점을 자동으로 찾아 문장으로"""
        out = []
        overall_wr = self.overall().win_rate

        # 행동: 연패 후 승률 급락 감지
        streak_stats = {s.label: s for s in self.by_loss_streak()}
        if "연패 2회+ 후" in streak_stats and "평상시 (연패 0)" in streak_stats:
            after = streak_stats["연패 2회+ 후"]
            normal = streak_stats["평상시 (연패 0)"]
            if after.n >= 5 and after.win_rate < normal.win_rate - 10:
                out.append(
                    f"연패 2회 후 승률이 {after.win_rate}%로 떨어집니다 "
                    f"(평상시 {normal.win_rate}%). 연패 시 멈추는 게 유리합니다.")

        # 비중: 큰 비중 거래가 더 나쁜가
        size_stats = {s.label: s for s in self.by_size()}
        if "큰 비중 (1.5x+)" in size_stats:
            big = size_stats["큰 비중 (1.5x+)"]
            if big.n >= 5 and big.avg_pnl < 0:
                out.append(
                    f"큰 비중(1.5배+) 거래의 평균 손익이 {big.avg_pnl}%로 손실입니다. "
                    f"비중을 키울수록 결과가 나빠지는 패턴입니다.")

        # 구조: 손절 유무 차이
        sl_stats = {s.label: s for s in self.by_stop_loss()}
        if "손절 있음" in sl_stats and "손절 없음" in sl_stats:
            w, wo = sl_stats["손절 있음"], sl_stats["손절 없음"]
            if wo.n >= 5 and wo.avg_pnl < w.avg_pnl:
                out.append(
                    f"손절 없는 거래(평균 {wo.avg_pnl}%)가 손절 있는 거래"
                    f"(평균 {w.avg_pnl}%)보다 나쁩니다.")

        # 시간: 특정 시간대 약점
        hour_stats = self.by_hour_bucket()
        if hour_stats:
            worst = min((s for s in hour_stats if s.n >= 5), key=lambda s: s.win_rate, default=None)
            if worst and worst.win_rate < overall_wr - 15:
                out.append(
                    f"{worst.label} 거래 승률이 {worst.win_rate}%로 가장 낮습니다 "
                    f"(전체 {overall_wr}%). 이 시간대를 피하는 걸 고려하세요.")

        if not out:
            out.append("뚜렷한 약점 패턴은 발견되지 않았습니다. 거래가 더 쌓이면 정밀해집니다.")
        return out


if __name__ == "__main__":
    # 검증은 별도 샘플 생성기로 (sample_trades.py)
    print("TradeAnalyzer 모듈 로드 OK")
