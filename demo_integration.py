"""
Jarvis - 전체 통합 데모 (End-to-End)
================================================================

전체 흐름을 하나로 연결:
  거래소 체결 가져오기 (실전: API / 데모: mock)
    → 포지션 재조립
    → 레버리지 해석
    → 손절 관찰 주입 (실시간이 쌓은 데이터)
    → 상황별 승률 분석
    → 약점 인사이트 자동 추출
    → 실시간 리스크 평가 (현재 포지션)

실전 전환:
  build_demo_fills() 를 fetch_user_fills() 실제 호출로 바꾸기만 하면 됨.
  나머지 파이프라인은 그대로.
"""
from __future__ import annotations
import random
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from app.services.trade_history import Fill, reconstruct_positions, to_trades
from app.services.stoploss_tracker import (
    InMemorySLStore, StopLossTracker, StopLossObservation, enrich_trades_with_sl
)
from app.core.trade_analyzer import TradeAnalyzer
from app.core.risk_math import PositionInput, compute_risk, Side, D
from app.core.jarvis_assess import assess, AccountContext, BehaviorSignals


# ================================================================
# [실전 교체 지점] 거래소에서 체결 가져오기
# ================================================================
def build_demo_fills(rnd) -> list[Fill]:
    """
    데모용 mock 체결 생성. 충동형 트레이더 패턴을 심음.
    실전에선 이 함수를 fetch_user_fills(client, symbol, start_ms)로 교체.
    """
    fills = []
    clock = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]
    streak = 0

    for i in range(80):
        sym = rnd.choice(symbols)
        # 충동 패턴: 연패 후 큰 비중 + 빠른 재진입 + 낮은 승률
        if streak >= 2:
            qty_mult = rnd.uniform(2.0, 3.5)
            gap_min = rnd.uniform(2, 8)
            win_prob = 0.30
        else:
            qty_mult = rnd.uniform(0.7, 1.3)
            gap_min = rnd.uniform(20, 240)
            win_prob = 0.56

        clock += timedelta(minutes=gap_min)
        side_buy = rnd.random() < 0.6
        price = rnd.uniform(100, 50000)
        base_qty = (10000 * 0.5) / price
        qty = base_qty * qty_mult

        win = rnd.random() < win_prob
        # 손익 (수수료 반영해서 realizedPnl에)
        if win:
            move_pct = rnd.uniform(0.01, 0.05)
        else:
            move_pct = -rnd.uniform(0.01, 0.06)
        exit_price = price * (1 + move_pct if side_buy else 1 - move_pct)
        pnl = (exit_price - price) * qty if side_buy else (price - exit_price) * qty

        hold = timedelta(minutes=rnd.uniform(15, 480))
        # 진입 체결
        fills.append(Fill(sym, clock, "BUY" if side_buy else "SELL",
                          D(str(price)), D(str(qty)), D("0"), D("0.4")))
        # 청산 체결 (realizedPnl 여기에)
        fills.append(Fill(sym, clock + hold, "SELL" if side_buy else "BUY",
                          D(str(exit_price)), D(str(qty)), D(str(round(pnl, 2))), D("0.4")))
        clock += hold
        streak = streak + 1 if not win else 0

    return fills


def build_demo_sl_observations(positions, rnd) -> list[StopLossObservation]:
    """
    데모용 mock 손절 관찰. 실전에선 워커가 실시간으로 쌓음.
    충동 패턴: 큰 비중 포지션일수록 손절을 안 거는 경향.
    """
    obs = []
    # 명목 중앙값으로 '큰 비중' 판단
    notionals = sorted(float(p.entry_price * p.qty) for p in positions)
    median_n = notionals[len(notionals)//2] if notionals else 1

    for p in positions:
        notional = float(p.entry_price * p.qty)
        is_big = notional > median_n * 1.5
        # 큰 비중이면 손절 안 거는 경향 (충동)
        has_sl_prob = 0.25 if is_big else 0.7
        n_obs = max(3, int((p.exit_time - p.entry_time).total_seconds() / 600))  # 10분마다
        for k in range(min(n_obs, 12)):
            t = p.entry_time + timedelta(minutes=k*10)
            if t >= p.exit_time:
                break
            has_sl = rnd.random() < has_sl_prob
            obs.append(StopLossObservation(
                user_id=1, symbol=p.symbol, observed_at=t,
                has_stop_loss=has_sl,
                liq_distance_pct=rnd.uniform(4, 15),
                position_qty=p.qty,
            ))
        # 종료 관찰
        obs.append(StopLossObservation(1, p.symbol, p.exit_time, False, 10, D("0")))
    return obs


# ================================================================
# 통합 파이프라인
# ================================================================
def run_pipeline():
    rnd = random.Random(99)
    equity = D("10000")

    print("=" * 60)
    print("  Jarvis 통합 분석 파이프라인")
    print("=" * 60)

    # --- 1) 체결 가져오기 (실전: API) ---
    fills = build_demo_fills(rnd)
    print(f"\n[1] 체결 수집: {len(fills)}건")

    # --- 2) 포지션 재조립 ---
    positions = reconstruct_positions(fills)
    print(f"[2] 포지션 재조립: {len(positions)}개")

    # --- 3) 손절 관찰 (실전: 워커가 쌓은 것) ---
    sl_store = InMemorySLStore()
    tracker = StopLossTracker(sl_store)
    for obs in build_demo_sl_observations(positions, rnd):
        tracker.observe(obs)
    tracker.reconcile(1, live_symbols=set())  # 남은 것 정리
    print(f"[3] 손절 관찰 처리 완료")

    # --- 4) Trade 변환 (레버리지 해석 포함) ---
    margin_lookup = {p.symbol: (p.entry_price * p.qty) / D(str(rnd.choice([5, 10, 15, 20])))
                     for p in positions}
    trades = to_trades(positions, equity, margin_lookup=margin_lookup)
    print(f"[4] Trade 변환 + 레버리지 해석 완료")

    # --- 5) 손절 판정 주입 ---
    trades, matched = enrich_trades_with_sl(trades, 1, sl_store)
    print(f"[5] 손절 판정 주입: {matched}/{len(trades)}건 매칭")

    # --- 6) 분석 ---
    a = TradeAnalyzer(trades)
    o = a.overall()
    print(f"\n{'─'*60}")
    print(f"  분석 결과 (총 {o.n}건)")
    print(f"{'─'*60}")
    print(f"  전체 승률 {o.win_rate}%  |  평균손익 {o.avg_pnl}%  |  누적 {o.total_pnl}%\n")

    def show(title, stats):
        print(f"  [{title}]")
        for s in stats:
            print(f"    {s.label:20s} n={s.n:3d}  승률 {s.win_rate:5.1f}%  평균 {s.avg_pnl:+6.2f}%")

    show("연패 상황별", a.by_loss_streak())
    show("비중별", a.by_size())
    show("손절 유무 (실시간 관찰 기반)", a.by_stop_loss())
    show("레버리지별", a.by_leverage())

    # --- 7) 인사이트 ---
    print(f"\n{'─'*60}")
    print(f"  자동 추출 인사이트")
    print(f"{'─'*60}")
    for ins in a.insights():
        print(f"  · {ins}")

    # --- 8) 현재 포지션 실시간 평가 (예시) ---
    print(f"\n{'─'*60}")
    print(f"  현재 포지션 실시간 리스크 평가 (예시)")
    print(f"{'─'*60}")
    cur = PositionInput("BTCUSDT", Side.LONG, D("1.5"), D("50000"), D("49500"),
                        D("20"), D("48000"), D("-750"), stop_loss_price=None)
    m = compute_risk(cur, equity)
    result = assess(
        m,
        AccountContext(equity, total_liq_loss_pct=m.liq_loss_pct_of_equity, correlated_exposure_pct=50),
        BehaviorSignals(recent_losses_streak=2, minutes_since_last_loss=5,
                        trades_last_30min=4, position_size_vs_avg=2.2),
        atr_pct=4.0,
    )
    print(f"  BTCUSDT 롱 | JRS={result.score} ({result.band})")
    print(f"  → {result.headline}")
    if result.coaching.triggered:
        print(f"  코칭: {result.coaching.message}")

    print(f"\n{'='*60}")
    print(f"  ✅ 전체 파이프라인 완료")
    print(f"{'='*60}")
    print(f"\n  실전 전환: build_demo_fills() → fetch_user_fills() 교체만.")
    print(f"            나머지 파이프라인은 그대로 작동.")


if __name__ == "__main__":
    run_pipeline()
