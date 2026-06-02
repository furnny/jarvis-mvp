"""
Jarvis 백테스트 — "경고가 실제로 도움이 되는가?"
================================================================

검증 질문:
  Jarvis가 위험하다고 한 포지션을 '무시하고 그대로 둔 경우' vs
  '경고를 따라 리스크를 줄인 경우', 실제 결과가 얼마나 다른가?

정직성 원칙 (순환논리 방지):
  - 가격 경로는 Jarvis와 완전히 독립적으로 생성 (Jarvis는 미래 가격을 모름)
  - Jarvis는 '진입 시점의 포지션 상태'만 보고 점수를 매김
  - 그 후 독립적으로 생성된 가격으로 결과(청산/손익)를 채점
  - 즉, Jarvis의 예측 ↔ 실제 결과를 분리해서 비교

이건 합성 데이터라 '로직 타당성' 확인용. 진짜 검증은 실거래 데이터로.
"""
from __future__ import annotations
import random
import math
import statistics
from dataclasses import dataclass
from decimal import Decimal

from app.core.risk_math import PositionInput, compute_risk, Side, D
from app.core.jarvis_score import (
    compute_jarvis_risk_score, AccountContext, BehaviorSignals
)


# ----------------------------------------------------------------
# 1) 독립적 가격 경로 생성 (Jarvis와 무관)
# ----------------------------------------------------------------
def generate_price_path(start_price: float, days: int, daily_vol: float, rnd) -> list[float]:
    """
    기하 브라운 운동 기반 가격 경로 (드리프트 0, 변동성만).
    Jarvis 알고리즘을 전혀 참조하지 않음 → 독립성 보장.
    꼬리를 두껍게 (가끔 큰 점프) 해서 현실의 fat-tail 반영.
    """
    path = [start_price]
    p = start_price
    for _ in range(days):
        # 평소엔 정규분포, 5% 확률로 큰 충격 (fat tail)
        if rnd.random() < 0.05:
            shock = rnd.gauss(0, daily_vol * 3)
        else:
            shock = rnd.gauss(0, daily_vol)
        p = p * (1 + shock)
        path.append(max(p, 0.01))
    return path


# ----------------------------------------------------------------
# 2) 무작위 포지션 생성 (다양한 리스크 수준)
# ----------------------------------------------------------------
@dataclass
class SyntheticTrade:
    pos: PositionInput
    acct: AccountContext
    behavior: BehaviorSignals
    daily_vol: float
    equity: Decimal


def random_trade(rnd) -> SyntheticTrade:
    equity = D("10000")
    entry = rnd.uniform(100, 60000)
    side = Side.LONG if rnd.random() < 0.5 else Side.SHORT
    leverage = rnd.choice([2, 3, 5, 10, 15, 20, 25])
    daily_vol = rnd.uniform(0.02, 0.08)

    # 청산가: 레버리지에 따라 (대략 1/leverage 거리)
    liq_move = entry / leverage * rnd.uniform(0.85, 1.0)
    liq = entry - liq_move if side == Side.LONG else entry + liq_move

    # 포지션 크기: 자본 대비 다양하게
    notional = float(equity) * rnd.uniform(0.3, 3.0)
    qty = notional / entry

    # 손절: 60% 확률로 있음
    has_sl = rnd.random() < 0.6
    if has_sl:
        sl_move = entry * rnd.uniform(0.01, 0.05)
        sl = entry - sl_move if side == Side.LONG else entry + sl_move
        sl = D(str(sl))
    else:
        sl = None

    pos = PositionInput(
        symbol="SYNTH", side=side, quantity=D(str(qty)),
        entry_price=D(str(entry)), mark_price=D(str(entry)),
        leverage=D(str(leverage)), liquidation_price=D(str(liq)),
        unrealized_pnl=D("0"), stop_loss_price=sl,
    )

    # 행동 신호: 무작위 (충동 상황 섞기)
    streak = rnd.choice([0, 0, 0, 1, 2, 3])
    behavior = BehaviorSignals(
        recent_losses_streak=streak,
        minutes_since_last_loss=rnd.uniform(2, 60),
        trades_last_30min=rnd.choice([1, 2, 3, 5, 8, 12]),
        position_size_vs_avg=rnd.uniform(0.8, 3.0),
        leverage_vs_avg=rnd.uniform(0.8, 2.5),
    )

    # 계좌 맥락
    acct = AccountContext(
        equity=equity,
        total_liq_loss_pct=rnd.uniform(5, 50),
        correlated_exposure_pct=rnd.uniform(20, 90),
    )

    return SyntheticTrade(pos, acct, behavior, daily_vol, equity)


# ----------------------------------------------------------------
# 3) 결과 시뮬레이션 (독립 가격으로 채점)
# ----------------------------------------------------------------
def simulate_outcome(trade: SyntheticTrade, rnd, days=14, risk_reduced=False) -> dict:
    """
    독립 가격 경로로 포지션 결과를 채점.
    risk_reduced=True면 'Jarvis 경고를 따라 비중/레버리지를 절반으로 줄인' 시나리오.
    """
    pos = trade.pos
    entry = float(pos.entry_price)
    qty = float(pos.quantity)
    liq = float(pos.liquidation_price)
    sl = float(pos.stop_loss_price) if pos.stop_loss_price else None

    # 경고 따른 경우: 수량 절반 + 청산가를 더 멀리 (레버리지 낮춤 효과)
    if risk_reduced:
        qty *= 0.5
        liq_dist = abs(entry - liq)
        liq = entry - liq_dist * 2 if pos.side == Side.LONG else entry + liq_dist * 2

    path = generate_price_path(entry, days, trade.daily_vol, rnd)

    liquidated = False
    exit_price = path[-1]
    for price in path[1:]:
        # 청산 체크
        if pos.side == Side.LONG and price <= liq:
            liquidated = True; exit_price = liq; break
        if pos.side == Side.SHORT and price >= liq:
            liquidated = True; exit_price = liq; break
        # 손절 체크
        if sl is not None:
            if pos.side == Side.LONG and price <= sl:
                exit_price = sl; break
            if pos.side == Side.SHORT and price >= sl:
                exit_price = sl; break

    # 손익 계산
    if pos.side == Side.LONG:
        pnl = (exit_price - entry) * qty
    else:
        pnl = (entry - exit_price) * qty

    pnl_pct = pnl / float(trade.equity) * 100
    return {"liquidated": liquidated, "pnl_pct": pnl_pct}


# ----------------------------------------------------------------
# 4) 백테스트 실행
# ----------------------------------------------------------------
def run_backtest(n_trades=5000, seed=42):
    rnd = random.Random(seed)
    rows = []

    for _ in range(n_trades):
        trade = random_trade(rnd)
        m = compute_risk(trade.pos, trade.equity)
        jrs = compute_jarvis_risk_score(m, trade.acct, trade.behavior, atr_pct=trade.daily_vol*100)

        # 같은 가격 시드로 '무시' vs '경고 따름' 두 결과를 비교 (공정 비교)
        outcome_seed = rnd.randint(0, 2**31)
        ignored = simulate_outcome(trade, random.Random(outcome_seed), risk_reduced=False)
        followed = simulate_outcome(trade, random.Random(outcome_seed), risk_reduced=True)

        rows.append({
            "jrs": jrs.total, "band": jrs.band,
            "ignored_liq": ignored["liquidated"], "ignored_pnl": ignored["pnl_pct"],
            "followed_liq": followed["liquidated"], "followed_pnl": followed["pnl_pct"],
        })

    return rows


def analyze(rows):
    print("=== Jarvis 백테스트 결과 (합성 5000건) ===\n")

    # 검증 1: JRS 점수대별 실제 청산율 (점수 타당성)
    print("[검증 1] JRS 점수가 높을수록 실제로 청산이 많은가?")
    bands = ["safe", "caution", "warning", "danger"]
    for band in bands:
        sub = [r for r in rows if r["band"] == band]
        if not sub:
            continue
        liq_rate = sum(r["ignored_liq"] for r in sub) / len(sub) * 100
        avg_pnl = statistics.mean(r["ignored_pnl"] for r in sub)
        print(f"   {band:8s} (n={len(sub):4d}): 청산율 {liq_rate:5.1f}%  평균손익 {avg_pnl:+6.1f}%")
    print("   → 아래로 갈수록 청산율↑ 손익↓ 이면 점수가 타당함\n")

    # 검증 2: 경고를 따랐을 때 vs 무시했을 때 (실용성 — 핵심 질문)
    print("[검증 2] 경고(warning+danger)를 따르면 결과가 나아지는가?")
    risky = [r for r in rows if r["band"] in ("warning", "danger")]
    ign_liq = sum(r["ignored_liq"] for r in risky) / len(risky) * 100
    fol_liq = sum(r["followed_liq"] for r in risky) / len(risky) * 100
    ign_pnl = statistics.mean(r["ignored_pnl"] for r in risky)
    fol_pnl = statistics.mean(r["followed_pnl"] for r in risky)
    # 최악의 손실 (하위 5%)
    ign_sorted = sorted(r["ignored_pnl"] for r in risky)
    fol_sorted = sorted(r["followed_pnl"] for r in risky)
    ign_worst = statistics.mean(ign_sorted[:len(ign_sorted)//20])
    fol_worst = statistics.mean(fol_sorted[:len(fol_sorted)//20])

    print(f"   위험 경고 받은 거래: {len(risky)}건")
    print(f"   {'':18s} {'무시':>10s} {'경고 따름':>10s}")
    print(f"   {'청산율':18s} {ign_liq:9.1f}% {fol_liq:9.1f}%")
    print(f"   {'평균 손익':18s} {ign_pnl:+9.1f}% {fol_pnl:+9.1f}%")
    print(f"   {'최악 5% 손실':18s} {ign_worst:+9.1f}% {fol_worst:+9.1f}%")
    print(f"   → '경고 따름'의 청산율·최악손실이 낮으면 Jarvis가 실제로 도움\n")

    # 검증 3: 안전(safe) 구간은 굳이 줄일 필요 없었나? (헛경보 비용)
    print("[검증 3] 안전 구간에서 불필요하게 줄이면 손해? (헛경보 점검)")
    safe = [r for r in rows if r["band"] == "safe"]
    if safe:
        s_ign = statistics.mean(r["ignored_pnl"] for r in safe)
        s_fol = statistics.mean(r["followed_pnl"] for r in safe)
        print(f"   안전 거래 {len(safe)}건: 그대로 {s_ign:+.1f}% vs 줄임 {s_fol:+.1f}%")
        print(f"   → 안전 구간은 줄여도 큰 손해 없어야 정상 (과잉경고 아님)\n")


if __name__ == "__main__":
    rows = run_backtest()
    analyze(rows)
    print("✅ 백테스트 완료")
    print("\n주의: 합성 데이터 기반 '로직 타당성' 확인용.")
    print("      진짜 검증은 실제 거래소 과거 데이터로 (네트워크 가능 환경에서).")
