"""
Jarvis - 시각화 데이터 계약 (Visualization Contract)
================================================================

프론트(시각화) ↔ 백(분석 로직) 사이의 약속을 고정.

역할:
  baseline.py / trade_analyzer.py 의 출력을
  → 시각화 화면들이 그대로 렌더할 수 있는 JSON으로 변환

이 계층이 있어야:
  - 프론트와 백이 독립 개발 가능
  - mock → 실데이터 전환 시 이 변환기만 통과하면 됨
  - 시각화 화면은 데이터 출처(mock/실제)를 모름

대응 화면:
  - to_journal_comparison()  → 구간 비교 레이더 (지난달 vs 이번달)
  - to_timeseries()          → 시계열 저널 (주별 자본+승률)
  - to_situation_breakdown() → 거래 분석 대시보드 (상황별 승률)
"""
from __future__ import annotations
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Optional

from app.core.baseline import Baseline, compare_periods, detect_style_drift, TradingStyle
from app.core.trade_analyzer import TradeAnalyzer, Trade


# ----------------------------------------------------------------
# 1) 구간 비교 레이더 화면
# ----------------------------------------------------------------
def to_journal_comparison(
    prev: Baseline,
    curr: Baseline,
    style: TradingStyle,
) -> dict:
    """
    레이더 차트 + 지표 카드용 JSON.
    각 지표를 0~100으로 정규화 (높을수록 생존에 유리).
    """
    comparisons = compare_periods(prev, curr)
    drift = detect_style_drift(curr, style)

    # 레이더용 5축 정규화 (높을수록 좋음)
    def norm_winrate(wr): return min(wr / 70 * 100, 100)  # 70%를 만점 기준
    def norm_pnl(p): return max(0, min((p + 5) / 10 * 100, 100))  # -5~+5% → 0~100
    def norm_coverage(c): return c * 100
    def norm_leverage(lev): return max(0, min((20 - lev) / 20 * 100, 100))  # 낮을수록 좋음
    def norm_consistency(c): return c * 100

    radar = {
        "axes": ["승률", "수익성", "손절 커버리지", "레버리지 절제", "스타일 일관성"],
        "previous": [
            round(norm_winrate(prev.win_rate)),
            round(norm_pnl(prev.avg_pnl_pct)),
            round(norm_coverage(prev.sl_coverage)),
            round(norm_leverage(prev.median_leverage)),
            round(norm_consistency(prev.style_consistency)),
        ],
        "current": [
            round(norm_winrate(curr.win_rate)),
            round(norm_pnl(curr.avg_pnl_pct)),
            round(norm_coverage(curr.sl_coverage)),
            round(norm_leverage(curr.median_leverage)),
            round(norm_consistency(curr.style_consistency)),
        ],
    }

    # 지표 카드 (원본 수치 + 방향)
    cards = []
    for c in comparisons:
        cards.append({
            "metric": c.metric,
            "previous": c.previous,
            "current": c.current,
            "direction": c.direction,  # improving/worsening/stable
            "note": c.note,
        })

    # 전체 추세 판정
    worsening = sum(1 for c in comparisons if c.direction == "worsening")
    improving = sum(1 for c in comparisons if c.direction == "improving")
    if worsening >= 4:
        verdict = {"tone": "danger", "text": f"{worsening}개 지표 악화 — 강가에서 멀어지고 있습니다"}
    elif improving >= 4:
        verdict = {"tone": "success", "text": f"{improving}개 지표 개선 — 단단히 서 있습니다"}
    else:
        verdict = {"tone": "neutral", "text": "혼조 — 일부 개선, 일부 악화"}

    return {
        "period": {"previous": prev.period_label, "current": curr.period_label},
        "verdict": verdict,
        "radar": radar,
        "cards": cards,
        "style_drift": {
            "drifted": drift.drifted,
            "message": drift.message,
        },
    }


# ----------------------------------------------------------------
# 2) 시계열 저널 화면 (주별)
# ----------------------------------------------------------------
def to_timeseries(trades: list[Trade], start_equity: float, weeks: int = 10) -> dict:
    """
    주별 누적 자본 + 승률 시계열.
    자본 곡선, 승률 추세, 드로다운, 꺾인 지점을 계산.
    """
    if not trades:
        return {"weeks": [], "equity": [], "winrate": [], "summary": {}}

    sorted_trades = sorted(trades, key=lambda t: t.entry_time)
    t0 = sorted_trades[0].entry_time

    # 주별 버킷
    buckets: list[list[Trade]] = [[] for _ in range(weeks)]
    for t in sorted_trades:
        wk = int((t.entry_time - t0).days // 7)
        if 0 <= wk < weeks:
            buckets[wk].append(t)

    equity = start_equity
    equity_curve = [round(equity)]
    winrates = []
    week_labels = []

    for i, bucket in enumerate(buckets):
        week_labels.append(f"{i+1}주")
        if bucket:
            week_pnl = sum(t.pnl_pct for t in bucket)
            equity *= (1 + week_pnl / 100)
            wins = sum(1 for t in bucket if t.is_win)
            winrates.append(round(wins / len(bucket) * 100, 1))
        else:
            winrates.append(None)  # 거래 없는 주
        equity_curve.append(round(equity))

    # 드로다운 계산
    peak = max(equity_curve)
    trough_after_peak = min(equity_curve[equity_curve.index(peak):])
    max_dd = round((trough_after_peak - peak) / peak * 100, 1) if peak > 0 else 0

    # 꺾인 지점: 승률이 처음으로 크게 떨어진 주
    breakpoint_week = None
    valid_wr = [(i, w) for i, w in enumerate(winrates) if w is not None]
    for j in range(1, len(valid_wr)):
        prev_w = valid_wr[j-1][1]
        cur_w = valid_wr[j][1]
        if prev_w - cur_w >= 15:  # 15%p 이상 급락
            breakpoint_week = valid_wr[j][0] + 1
            break

    # 회복 필요율 (드로다운의 비대칭)
    recovery_needed = round((peak / equity - 1) * 100, 1) if equity > 0 and equity < peak else 0

    return {
        "weeks": week_labels,
        "equity": equity_curve[1:],  # 시작값 제외하고 주별 종가
        "winrate": winrates,
        "summary": {
            "start_equity": round(start_equity),
            "current_equity": round(equity),
            "peak_equity": round(peak),
            "max_drawdown_pct": max_dd,
            "breakpoint_week": breakpoint_week,
            "recovery_needed_pct": recovery_needed,
        },
    }


# ----------------------------------------------------------------
# 3) 상황별 분석 화면
# ----------------------------------------------------------------
def to_situation_breakdown(analyzer: TradeAnalyzer) -> dict:
    """거래 분석 대시보드용. 상황별 승률 + 인사이트."""
    def seg(stats):
        return [{"label": s.label, "n": s.n, "win_rate": s.win_rate, "avg_pnl": s.avg_pnl}
                for s in stats]

    overall = analyzer.overall()
    return {
        "overall": {
            "n": overall.n, "win_rate": overall.win_rate,
            "avg_pnl": overall.avg_pnl, "total_pnl": overall.total_pnl,
        },
        "segments": {
            "loss_streak": seg(analyzer.by_loss_streak()),
            "size": seg(analyzer.by_size()),
            "reentry": seg(analyzer.by_reentry_speed()),
            "stop_loss": seg(analyzer.by_stop_loss()),
            "leverage": seg(analyzer.by_leverage()),
            "hour": seg(analyzer.by_hour_bucket()),
            "side": seg(analyzer.by_side()),
        },
        "insights": analyzer.insights(),
    }


# ================================================================
# 검증 — 실제 분석 출력이 시각화 JSON으로 정확히 변환되는지
# ================================================================
if __name__ == "__main__":
    import json
    import random
    from datetime import timezone
    from app.core.baseline import build_baseline

    rnd = random.Random(11)

    def mk_trades(n, win_p, lev, hold_min, sl_p, base_t):
        ts = []
        clock = base_t
        for _ in range(n):
            clock += timedelta(hours=rnd.uniform(2, 10))
            win = rnd.random() < win_p
            pnl = rnd.uniform(1, 4) if win else -rnd.uniform(1, 4)
            hold = timedelta(minutes=hold_min * rnd.uniform(0.6, 1.4))
            ts.append(Trade("BTCUSDT", "LONG" if rnd.random()<0.6 else "SHORT",
                            clock, clock+hold, round(pnl,2),
                            lev*rnd.uniform(0.8,1.2), rnd.uniform(0.8,1.4),
                            rnd.random() < sl_p))
        return ts

    print("=== 시각화 데이터 계약 검증 ===\n")

    style = TradingStyle.SWING
    prev_t = mk_trades(30, 0.60, 8, 1500, 0.85, datetime(2025,12,1,tzinfo=timezone.utc))
    curr_t = mk_trades(35, 0.42, 17, 250, 0.30, datetime(2026,1,1,tzinfo=timezone.utc))
    prev_bl = build_baseline(prev_t, style, "2025-12")
    curr_bl = build_baseline(curr_t, style, "2026-01")

    # 1) 구간 비교
    comp = to_journal_comparison(prev_bl, curr_bl, style)
    print("[1] 구간 비교 레이더 JSON")
    print(f"    판정: {comp['verdict']['text']}")
    print(f"    레이더 지난달: {comp['radar']['previous']}")
    print(f"    레이더 이번달: {comp['radar']['current']}")
    print(f"    카드 {len(comp['cards'])}개, 스타일일탈={comp['style_drift']['drifted']}\n")

    # 2) 시계열
    ts = to_timeseries(curr_t, start_equity=10000, weeks=6)
    print("[2] 시계열 저널 JSON")
    print(f"    주차: {ts['weeks']}")
    print(f"    자본: {ts['equity']}")
    print(f"    승률: {ts['winrate']}")
    print(f"    요약: 드로다운 {ts['summary']['max_drawdown_pct']}%, "
          f"꺾인주 {ts['summary']['breakpoint_week']}, "
          f"회복필요 {ts['summary']['recovery_needed_pct']}%\n")

    # 3) 상황별
    analyzer = TradeAnalyzer(curr_t)
    sb = to_situation_breakdown(analyzer)
    print("[3] 상황별 분석 JSON")
    print(f"    전체: 승률 {sb['overall']['win_rate']}%, n={sb['overall']['n']}")
    print(f"    세그먼트: {list(sb['segments'].keys())}")
    print(f"    인사이트 {len(sb['insights'])}개")
    print()

    # JSON 직렬화 가능한지 최종 확인 (프론트로 보낼 수 있나)
    payload = {"comparison": comp, "timeseries": ts, "breakdown": sb}
    serialized = json.dumps(payload, ensure_ascii=False)
    print(f"✅ 전체 JSON 직렬화 OK ({len(serialized):,} bytes)")
    print("   → 이 JSON을 프론트로 보내면 시각화가 그대로 렌더됨")
