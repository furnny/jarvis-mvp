"""
Jarvis Score v2 (Lean) — 검증된 축만 사용
================================================================

백테스트 교훈 반영:
  - 변동성 축(+0.490), 구조 축(+0.114)만 점수에 사용 (예측력 검증됨)
  - 생존 축은 점수에서 제외 → '피해 한도 가드레일'로 분리
    (청산 예측은 불가능하지만 피해 규모는 계산 가능하므로)
  - 행동 축은 점수에서 제외 → '코칭 신호'로 분리
    (단건 손익으론 측정 불가. 장기 패턴 코칭의 영역)

설계 철학:
  점수(score)는 '검증된 것'만. 측정 안 되는 건 점수에 안 섞음.
  대신 가드레일/코칭으로 분리해서 여전히 사용자에게 가치 전달.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from app.core.risk_math import RiskMetrics
from app.core.jarvis_score import (
    score_structure, score_volatility, _smoothstep,
    AccountContext, BehaviorSignals,
)


# 검증된 축만, 예측력에 비례한 가중치
VALIDATED_WEIGHTS = {
    "volatility": 0.70,   # 상관 +0.490 → 가장 큰 무게
    "structure": 0.30,    # 상관 +0.114 → 보조
}

# 피해 한도 가드레일 (점수와 무관, 항상 작동)
RUIN_THRESHOLD_PCT = 30.0   # 청산 시 자본 30% 이상 손실 가능 → 무조건 경고


@dataclass
class CoachingSignal:
    """행동 코칭 (점수 아님, 별도 메시지)"""
    triggered: bool
    message: str
    intensity: str  # mild / strong


@dataclass
class Guardrail:
    """피해 한도 가드레일 (점수 아님, 안전장치)"""
    triggered: bool
    message: str
    potential_loss_pct: float


@dataclass
class JarvisAssessment:
    """최종 평가 — 점수 + 가드레일 + 코칭 (세 가지를 분리)"""
    score: float            # 0~100, 검증된 축만
    band: str
    score_breakdown: dict   # 변동성/구조 분해
    guardrail: Guardrail    # 피해 한도 (점수와 독립)
    coaching: CoachingSignal # 행동 코칭 (점수와 독립)
    headline: str


def _band(s: float) -> str:
    if s < 30: return "safe"
    if s < 55: return "caution"
    if s < 75: return "warning"
    return "danger"


def assess(
    m: RiskMetrics,
    acct: AccountContext,
    behavior: BehaviorSignals,
    atr_pct: Optional[float] = None,
) -> JarvisAssessment:
    # --- 1) 점수: 검증된 2축만 ---
    vol = score_volatility(m, atr_pct)
    strc = score_structure(m)
    score = vol * VALIDATED_WEIGHTS["volatility"] + strc * VALIDATED_WEIGHTS["structure"]

    # --- 2) 가드레일: 피해 한도 (예측 아닌 피해 규모) ---
    ruin = m.liq_loss_pct_of_equity >= RUIN_THRESHOLD_PCT
    guardrail = Guardrail(
        triggered=ruin,
        message=(f"청산 시 자본의 {m.liq_loss_pct_of_equity:.0f}%가 사라질 수 있습니다. "
                 f"청산 확률과 무관하게, 이 노출 자체가 회복을 어렵게 합니다."
                 if ruin else ""),
        potential_loss_pct=m.liq_loss_pct_of_equity,
    )

    # --- 3) 코칭: 행동 신호 (점수 아닌 메시지) ---
    coaching = _build_coaching(behavior)

    # --- 헤드라인 결정 (가드레일 > 점수 > 코칭 순) ---
    if guardrail.triggered:
        headline = guardrail.message
        band = "danger"
        score = max(score, 75)  # 가드레일 발동 시 최소 danger
    else:
        band = _band(score)
        if band in ("warning", "danger"):
            headline = ("변동성 대비 청산이 가깝습니다. 여유를 두세요."
                        if vol >= strc else
                        "포지션 설계를 점검하세요 (손절·레버리지).")
        elif coaching.triggered:
            headline = coaching.message  # 점수는 낮아도 충동 신호 있으면 코칭
        else:
            headline = "현재 리스크는 안정적입니다."

    return JarvisAssessment(
        score=round(score, 1), band=band,
        score_breakdown={"volatility": round(vol, 1), "structure": round(strc, 1)},
        guardrail=guardrail, coaching=coaching, headline=headline,
    )


def _build_coaching(b: BehaviorSignals) -> CoachingSignal:
    """행동 코칭: 점수에 안 넣고 별도 메시지로. 충동형 트레이더 케어."""
    # 연패 후 빠른 재진입 + 비중 급증 = 가장 강한 충동 신호
    if b.recent_losses_streak >= 2 and b.minutes_since_last_loss < 10 and b.position_size_vs_avg >= 1.5:
        return CoachingSignal(True,
            f"연패 {b.recent_losses_streak}회 직후 평소 {b.position_size_vs_avg:.1f}배 비중으로 진입했습니다. "
            f"잠시 멈추는 걸 권합니다.", "strong")
    if b.recent_losses_streak >= 2 and b.minutes_since_last_loss < 10:
        return CoachingSignal(True,
            f"연패 후 빠른 재진입입니다. 한 박자 쉬어가는 게 어떨까요?", "mild")
    if b.position_size_vs_avg >= 2.5:
        return CoachingSignal(True,
            f"평소보다 {b.position_size_vs_avg:.1f}배 큰 비중입니다. 의도한 크기가 맞나요?", "mild")
    if b.trades_last_30min >= 10:
        return CoachingSignal(True,
            f"30분간 {b.trades_last_30min}회 거래 중입니다. 과도한 빈도일 수 있어요.", "mild")
    return CoachingSignal(False, "", "")


# ================================================================
# 검증
# ================================================================
if __name__ == "__main__":
    from app.core.risk_math import PositionInput, compute_risk, Side, D

    eq = D("10000")
    print("=== Jarvis Lean Assessment 검증 ===\n")

    # A: 안정적
    pA = PositionInput("BTCUSDT", Side.LONG, D("0.2"), D("50000"), D("50000"),
                       D("3"), D("40000"), D("0"), D("49000"))
    aA = assess(compute_risk(pA, eq),
                AccountContext(eq, 6, 30),
                BehaviorSignals(0, 60, 1, 1.0), atr_pct=3.0)
    print(f"[A] 안정적: score={aA.score} ({aA.band})")
    print(f"    분해={aA.score_breakdown}")
    print(f"    → {aA.headline}\n")

    # B: 손절없고 변동성 대비 청산 가까움
    pB = PositionInput("ALTUSDT", Side.LONG, D("50"), D("100"), D("100"),
                       D("20"), D("96"), D("0"), None)
    aB = assess(compute_risk(pB, eq),
                AccountContext(eq, 12, 50),
                BehaviorSignals(0, 60, 2, 1.0), atr_pct=6.0)
    print(f"[B] 손절없음+변동성위험: score={aB.score} ({aB.band})")
    print(f"    분해={aB.score_breakdown}")
    print(f"    → {aB.headline}\n")

    # C: 점수는 보통이지만 충동 신호 (코칭 발동)
    pC = PositionInput("ETHUSDT", Side.LONG, D("1"), D("3000"), D("3000"),
                       D("5"), D("2700"), D("0"), D("2950"))
    aC = assess(compute_risk(pC, eq),
                AccountContext(eq, 10, 40),
                BehaviorSignals(recent_losses_streak=3, minutes_since_last_loss=4,
                                trades_last_30min=5, position_size_vs_avg=2.0), atr_pct=4.0)
    print(f"[C] 점수보통+충동신호: score={aC.score} ({aC.band})")
    print(f"    코칭 발동: {aC.coaching.triggered} ({aC.coaching.intensity})")
    print(f"    → {aC.headline}\n")

    # D: 피해 한도 가드레일 발동 (청산 시 자본 큰 손실)
    pD = PositionInput("SOLUSDT", Side.LONG, D("200"), D("150"), D("150"),
                       D("4"), D("130"), D("0"), None)
    aD = assess(compute_risk(pD, eq),
                AccountContext(eq, 40, 70),
                BehaviorSignals(0, 60, 2, 1.0), atr_pct=5.0)
    print(f"[D] 피해한도 가드레일: score={aD.score} ({aD.band})")
    print(f"    가드레일 발동: {aD.guardrail.triggered} (청산시 {aD.guardrail.potential_loss_pct:.0f}% 손실)")
    print(f"    → {aD.headline}\n")

    print("✅ Lean Assessment 검증 완료")
