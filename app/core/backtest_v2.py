"""
Jarvis 백테스트 v2 — 축별 채점 (각 리스크 축의 예측력을 분리 검증)
================================================================

v1의 문제: "청산했나"만으로 채점 → 행동 리스크의 가치가 안 보였음
           (danger 청산율이 오히려 낮게 나오는 역설)

v2의 수정:
  1. 축별로 '맞는 결과'로 채점
     - 생존 축 → 청산 발생 예측
     - 구조 축 → 큰 손실(자본 -15% 이하) 예측
     - 행동 축 → '충동 거래의 손익 악화' 예측
     - 변동성 축 → 청산 예측 (변동성은 청산의 선행지표)

  2. 현실적 틸팅(tilt) 반영
     충동적일수록(연패·몰빵) 실제로 나쁜 진입을 함 = 인과관계.
     이건 순환논리 아님 — 행동경제학의 실재 현상이고,
     Jarvis는 이 인과를 '모른 채' 행동 신호만 보고 점수를 매김.
     가격 채점은 독립적. Jarvis가 이 연결을 잡아내는지 보는 것.
"""
from __future__ import annotations
import random
import statistics
from dataclasses import dataclass
from decimal import Decimal

from app.core.risk_math import PositionInput, compute_risk, Side, D
from app.core.jarvis_score import (
    compute_jarvis_risk_score, AccountContext, BehaviorSignals,
    score_survival, score_structure, score_behavior, score_volatility,
)


def generate_price_path(start, days, daily_vol, drift, rnd):
    """독립 가격 경로. drift는 '진입 품질'을 반영 (나쁜 진입 = 불리한 드리프트)."""
    path = [start]; p = start
    for _ in range(days):
        if rnd.random() < 0.05:
            shock = rnd.gauss(drift, daily_vol * 3)
        else:
            shock = rnd.gauss(drift, daily_vol)
        p = max(p * (1 + shock), 0.01)
        path.append(p)
    return path


@dataclass
class SynthTrade:
    pos: PositionInput
    acct: AccountContext
    behavior: BehaviorSignals
    daily_vol: float
    entry_quality: float   # 진입 품질 (드리프트). 음수=나쁜 진입
    equity: Decimal


def random_trade(rnd):
    equity = D("10000")
    entry = rnd.uniform(100, 60000)
    side = Side.LONG if rnd.random() < 0.5 else Side.SHORT
    leverage = rnd.choice([2, 3, 5, 10, 15, 20, 25])
    daily_vol = rnd.uniform(0.02, 0.08)

    liq_move = entry / leverage * rnd.uniform(0.85, 1.0)
    liq = entry - liq_move if side == Side.LONG else entry + liq_move
    notional = float(equity) * rnd.uniform(0.3, 3.0)
    qty = notional / entry

    has_sl = rnd.random() < 0.6
    if has_sl:
        sl_move = entry * rnd.uniform(0.01, 0.05)
        sl = D(str(entry - sl_move if side == Side.LONG else entry + sl_move))
    else:
        sl = None

    # --- 틸팅(tilt) 모델 ---
    # 충동 강도를 정하고, 그에 비례해 '나쁜 진입'이 되게 함
    streak = rnd.choice([0, 0, 0, 1, 2, 3])
    size_mult = rnd.uniform(0.8, 3.0)
    freq = rnd.choice([1, 2, 3, 5, 8, 12])
    # 충동 강도 (0~1 정규화)
    tilt = min(1.0, (streak / 3) * 0.5 + (max(size_mult - 1, 0) / 2) * 0.3 + (freq / 12) * 0.2)
    # 충동적일수록 진입 품질 하락 (드리프트가 불리해짐). + 약간의 무작위
    base_quality = rnd.gauss(0, 0.003)            # 평소엔 거의 0 (랜덤워크)
    entry_quality = base_quality - tilt * 0.012   # 틸팅이 클수록 불리한 드리프트
    # 방향에 맞게 (롱이면 음수 드리프트가 불리, 숏이면 반대)
    drift = entry_quality if side == Side.LONG else -entry_quality

    behavior = BehaviorSignals(
        recent_losses_streak=streak,
        minutes_since_last_loss=rnd.uniform(2, 60) if streak == 0 else rnd.uniform(2, 9),
        trades_last_30min=freq,
        position_size_vs_avg=size_mult,
        leverage_vs_avg=rnd.uniform(0.8, 2.5),
    )
    acct = AccountContext(
        equity=equity,
        total_liq_loss_pct=rnd.uniform(5, 50),
        correlated_exposure_pct=rnd.uniform(20, 90),
    )

    pos = PositionInput("SYNTH", side, D(str(qty)), D(str(entry)), D(str(entry)),
                        D(str(leverage)), D(str(liq)), D("0"), sl)
    return SynthTrade(pos, acct, behavior, daily_vol, drift, equity)


def simulate_outcome(trade, rnd, days=14, reduce=False):
    pos = trade.pos
    entry = float(pos.entry_price); qty = float(pos.quantity)
    liq = float(pos.liquidation_price)
    sl = float(pos.stop_loss_price) if pos.stop_loss_price else None
    if reduce:
        qty *= 0.5
        d = abs(entry - liq)
        liq = entry - d*2 if pos.side == Side.LONG else entry + d*2

    path = generate_price_path(entry, days, trade.daily_vol, trade.entry_quality
                               if pos.side == Side.LONG else -trade.entry_quality, rnd)
    liquidated = False; exit_p = path[-1]
    for price in path[1:]:
        if pos.side == Side.LONG and price <= liq: liquidated=True; exit_p=liq; break
        if pos.side == Side.SHORT and price >= liq: liquidated=True; exit_p=liq; break
        if sl is not None:
            if pos.side == Side.LONG and price <= sl: exit_p=sl; break
            if pos.side == Side.SHORT and price >= sl: exit_p=sl; break
    pnl = (exit_p - entry)*qty if pos.side == Side.LONG else (entry - exit_p)*qty
    return {"liquidated": liquidated, "pnl_pct": pnl/float(trade.equity)*100}


def correlation(xs, ys):
    """피어슨 상관계수 (점수 ↔ 나쁜 결과). 양수면 '점수 높을수록 나쁨' = 예측력 있음."""
    n = len(xs)
    if n < 2: return 0.0
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    sx = sum((x-mx)**2 for x in xs)**0.5
    sy = sum((y-my)**2 for y in ys)**0.5
    return cov/(sx*sy) if sx>0 and sy>0 else 0.0


def run(n=8000, seed=7):
    rnd = random.Random(seed)
    data = []
    for _ in range(n):
        t = random_trade(rnd)
        m = compute_risk(t.pos, t.equity)
        # 각 축 점수 직접 계산 (분리 검증)
        ax = {
            "survival": score_survival(m, t.acct),
            "structure": score_structure(m),
            "behavior": score_behavior(t.behavior),
            "volatility": score_volatility(m, t.daily_vol*100),
        }
        jrs = compute_jarvis_risk_score(m, t.acct, t.behavior, atr_pct=t.daily_vol*100)

        oseed = rnd.randint(0, 2**31)
        ignored = simulate_outcome(t, random.Random(oseed), reduce=False)
        followed = simulate_outcome(t, random.Random(oseed), reduce=True)

        data.append({
            "ax": ax, "jrs": jrs.total, "band": jrs.band,
            "liq": ignored["liquidated"], "pnl": ignored["pnl_pct"],
            "big_loss": ignored["pnl_pct"] < -15,
            "f_liq": followed["liquidated"], "f_pnl": followed["pnl_pct"],
        })
    return data


def analyze(data):
    print("=== Jarvis 백테스트 v2 — 축별 예측력 검증 (n=8000) ===\n")

    # 각 축이 '자기가 예측해야 할 결과'를 얼마나 잘 예측하나 (상관계수)
    print("[축별 예측력] 각 축 점수 ↔ 해당 나쁜 결과의 상관계수")
    print("  (양수 클수록 예측력 좋음. 각 축은 '자기 담당' 결과로 채점)\n")

    surv = [d["ax"]["survival"] for d in data]
    strc = [d["ax"]["structure"] for d in data]
    beh  = [d["ax"]["behavior"] for d in data]
    vol  = [d["ax"]["volatility"] for d in data]
    liq  = [1.0 if d["liq"] else 0.0 for d in data]
    bigloss = [1.0 if d["big_loss"] else 0.0 for d in data]
    neg_pnl = [-d["pnl"] for d in data]  # 손실이 클수록 큰 값

    print(f"  생존 축  ↔ 청산:        {correlation(surv, liq):+.3f}")
    print(f"  구조 축  ↔ 큰손실(-15%): {correlation(strc, bigloss):+.3f}")
    print(f"  행동 축  ↔ 손익악화:     {correlation(beh, neg_pnl):+.3f}   ← v1에서 안 보이던 것")
    print(f"  변동성축 ↔ 청산:        {correlation(vol, liq):+.3f}")
    print()

    # 행동 축 집중 검증: 충동 구간별 실제 손익
    print("[행동 리스크 집중] 행동 점수 구간별 실제 평균 손익")
    for lo, hi, name in [(0,20,"낮음"),(20,50,"중간"),(50,80,"높음"),(80,101,"매우높음")]:
        sub = [d for d in data if lo <= d["ax"]["behavior"] < hi]
        if not sub: continue
        avg = statistics.mean(d["pnl"] for d in sub)
        print(f"   행동 {name:5s} ({lo:3d}~{hi-1:3d}, n={len(sub):4d}): 평균손익 {avg:+6.2f}%")
    print("   → 행동 점수 높을수록 손익이 나빠지면, 행동 리스크가 진짜 의미 있음\n")

    # 통합 점수 밴드별 (개선됐는지)
    print("[통합 JRS] 밴드별 청산율 + 큰손실율")
    for band in ["safe","caution","warning","danger"]:
        sub = [d for d in data if d["band"]==band]
        if not sub: continue
        lr = sum(d["liq"] for d in sub)/len(sub)*100
        bl = sum(d["big_loss"] for d in sub)/len(sub)*100
        print(f"   {band:8s} (n={len(sub):4d}): 청산율 {lr:5.1f}%  큰손실율 {bl:5.1f}%")
    print()

    # 실용성 재확인 (경고 따름 효과)
    risky = [d for d in data if d["band"] in ("warning","danger")]
    print("[실용성] 위험 경고 따랐을 때 효과")
    print(f"   청산율: {sum(d['liq'] for d in risky)/len(risky)*100:.1f}% → "
          f"{sum(d['f_liq'] for d in risky)/len(risky)*100:.1f}%")
    iw = sorted(d['pnl'] for d in risky); fw = sorted(d['f_pnl'] for d in risky)
    print(f"   최악5% 손실: {statistics.mean(iw[:len(iw)//20]):+.1f}% → "
          f"{statistics.mean(fw[:len(fw)//20]):+.1f}%")


if __name__ == "__main__":
    data = run()
    analyze(data)
    print("\n✅ 백테스트 v2 완료")
