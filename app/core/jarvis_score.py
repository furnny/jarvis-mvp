"""
Jarvis Risk Score (JRS) — 통합 리스크 점수 알고리즘
================================================================

철학:
- 검증된 수학(ATR, 손익비, 청산거리)을 building block으로 사용
- Jarvis만의 관점(생존 우선 + 충동 제어)으로 가중 조합
- 단일 점수로 뭉개지 않고 4개 축으로 "분해 가능"하게 유지

핵심 설계 결정 (유저 우선순위 반영):
  평상시 가중치:  행동 > 구조 > 생존 > 변동성
  (충동형 트레이더 도구이므로 "원인"인 행동을 평소엔 무겁게)

  긴급 오버라이드: 청산이 임박하면 순위 무시하고 생존이 점수를 지배
  (충동은 '원인', 청산은 '결과'. 결과가 터지기 직전엔 결과가 최우선)

점수 규약:
  각 축 0~100 (높을수록 위험). 최종 JRS도 0~100.
  0~30 안전 / 30~55 주의 / 55~75 경고 / 75~100 위험
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from app.core.risk_math import RiskMetrics, Side, D


# ----------------------------------------------------------------
# 행동 신호 (계좌 차원, 포지션 단위가 아님)
# ----------------------------------------------------------------
@dataclass
class BehaviorSignals:
    """최근 거래 흐름에서 추출한 충동/복수매매 신호"""
    recent_losses_streak: int = 0       # 최근 연속 손실 횟수
    minutes_since_last_loss: float = 999 # 마지막 손실 후 경과(분)
    trades_last_30min: int = 0          # 30분 내 거래 횟수
    position_size_vs_avg: float = 1.0   # 평소 대비 이번 포지션 크기 배수
    leverage_vs_avg: float = 1.0        # 평소 대비 레버리지 배수


@dataclass
class AccountContext:
    """계좌 전체 맥락 (생존 리스크 계산용)"""
    equity: Decimal
    total_liq_loss_pct: float           # 전 포지션 동시 청산 시 자본 손실%
    correlated_exposure_pct: float      # 같은 방향에 몰린 노출 비중%


@dataclass
class AxisScores:
    """4개 축 점수 (분해해서 보여주기 위함)"""
    survival: float
    structure: float
    behavior: float
    volatility: float

    def as_dict(self) -> dict:
        return {
            "survival": round(self.survival, 1),
            "structure": round(self.structure, 1),
            "behavior": round(self.behavior, 1),
            "volatility": round(self.volatility, 1),
        }


@dataclass
class JarvisRiskScore:
    total: float                # 0~100 통합 점수
    band: str                   # safe/caution/warning/danger
    axes: AxisScores
    dominant_axis: str          # 가장 위험한 축 (뭘 고쳐야 하나)
    emergency_override: bool    # 청산 임박으로 생존이 지배했나
    headline: str               # 한 줄 요약 (자비스 말투)
    explanation: list[str]      # 분해 설명


# ----------------------------------------------------------------
# 각 축 점수 계산 (0~100, 높을수록 위험)
# ----------------------------------------------------------------

def _smoothstep(x: float, lo: float, hi: float) -> float:
    """lo~hi 구간을 0~100으로 부드럽게 매핑 (선형보다 자연스러움)"""
    if x <= lo:
        return 0.0
    if x >= hi:
        return 100.0
    t = (x - lo) / (hi - lo)
    return 100.0 * (t * t * (3 - 2 * t))  # smoothstep


def score_survival(m: RiskMetrics, acct: AccountContext) -> float:
    """
    생존 리스크: '한 번에 죽을 수 있나'
    - 이 포지션 청산 시 자본 손실%
    - 계좌 전체 동시 청산 손실% (상관관계 집중)
    둘 중 더 위험한 쪽을 채택 (max) — 생존은 최악을 봐야 함
    """
    # 단일 포지션 청산 손실: 자본의 10%면 위험 시작, 30%면 치명
    single = _smoothstep(m.liq_loss_pct_of_equity, 10, 30)
    # 계좌 전체 동시 청산: 20%면 위험, 50%면 치명
    account = _smoothstep(acct.total_liq_loss_pct, 20, 50)
    # 상관 집중: 70% 이상 한 방향이면 분산 효과 없음
    concentration = _smoothstep(acct.correlated_exposure_pct, 70, 100)
    return max(single, account, concentration * 0.7)


def score_structure(m: RiskMetrics) -> float:
    """
    구조 리스크: '포지션 설계가 제대로 됐나'
    - 손절 유무 (없으면 큰 페널티)
    - 손익비 (불리하면 페널티)
    - 실효 레버리지 (높으면 페널티)
    """
    parts = []

    # 손절 없음 = 구조적 결함. 단독으로 큰 점수
    if not m.has_stop_loss:
        parts.append(70.0)
    else:
        # 손절 기준 리스크가 2% 넘으면 점수 상승
        if m.stop_risk_pct_of_equity is not None:
            parts.append(_smoothstep(m.stop_risk_pct_of_equity, 2, 6))

    # 실효 레버리지: 5x부터 주의, 20x면 위험
    parts.append(_smoothstep(m.effective_leverage, 5, 20))

    return max(parts) if parts else 0.0


def score_behavior(b: BehaviorSignals) -> float:
    """
    행동 리스크: '지금 충동적으로 움직이나' — Jarvis의 차별점
    여러 신호를 합산하되 상한 100.
    """
    score = 0.0

    # 연패 후 빠른 재진입 = 복수매매 핵심 신호
    if b.recent_losses_streak >= 2 and b.minutes_since_last_loss < 10:
        score += 40 + min(b.recent_losses_streak - 2, 3) * 10  # 연패 깊을수록 가중

    # 거래 빈도 급증 (30분에 6회 초과)
    score += _smoothstep(b.trades_last_30min, 6, 15) * 0.4

    # 비중 급증 (평소 1.5배부터 경고, 3배면 위험) — 충동의 강력 신호
    score += _smoothstep(b.position_size_vs_avg, 1.5, 3.0) * 0.5

    # 레버리지 급증
    score += _smoothstep(b.leverage_vs_avg, 1.5, 3.0) * 0.3

    return min(score, 100.0)


def score_volatility(m: RiskMetrics, atr_pct: Optional[float]) -> float:
    """
    변동성 리스크: ATR 대비 청산이 얼마나 가까운가
    atr_pct = 자산의 일간 변동폭(%). None이면 변동성 데이터 없음 → 0
    청산거리가 (ATR * 2) 안쪽이면 '하루 안에 청산 가능'
    """
    if atr_pct is None or atr_pct <= 0:
        return 0.0
    safe_distance = atr_pct * 2.0  # 안전 버퍼 = 일간 변동폭의 2배
    # 청산거리가 safe_distance보다 작을수록 위험
    ratio = m.liq_distance_pct / safe_distance if safe_distance > 0 else 999
    # ratio 1.0(딱 버퍼)에서 위험 시작, 0.3(버퍼의 30%)이면 치명
    return _smoothstep(2.0 - ratio, 1.0, 1.7)


# ----------------------------------------------------------------
# 통합 점수
# ----------------------------------------------------------------

# 평상시 가중치 (유저 우선순위: 행동 > 구조 > 생존 > 변동성)
BASE_WEIGHTS = {
    "behavior": 0.35,
    "structure": 0.28,
    "survival": 0.22,
    "volatility": 0.15,
}

# 긴급 오버라이드 임계: 생존 점수가 이 이상이면 순위 무시
SURVIVAL_OVERRIDE_THRESHOLD = 75.0


def _band(score: float) -> str:
    if score < 30:
        return "safe"
    if score < 55:
        return "caution"
    if score < 75:
        return "warning"
    return "danger"


def compute_jarvis_risk_score(
    m: RiskMetrics,
    acct: AccountContext,
    behavior: BehaviorSignals,
    atr_pct: Optional[float] = None,
) -> JarvisRiskScore:
    axes = AxisScores(
        survival=score_survival(m, acct),
        structure=score_structure(m),
        behavior=score_behavior(behavior),
        volatility=score_volatility(m, atr_pct),
    )

    # --- 긴급 오버라이드: 청산 임박 시 생존이 점수를 지배 ---
    override = axes.survival >= SURVIVAL_OVERRIDE_THRESHOLD
    if override:
        # 생존 점수를 그대로 최종 점수로 (충동이고 뭐고 일단 살아남아야)
        total = max(axes.survival, _weighted(axes))
        dominant = "survival"
        headline = f"청산 임박 — 다른 모든 것보다 먼저 포지션을 줄이세요."
    else:
        total = _weighted(axes)
        dominant = _dominant_axis(axes)
        headline = _make_headline(dominant, axes, total)

    return JarvisRiskScore(
        total=round(total, 1),
        band=_band(total),
        axes=axes,
        dominant_axis=dominant,
        emergency_override=override,
        headline=headline,
        explanation=_explain(axes, m, behavior),
    )


def _weighted(axes: AxisScores) -> float:
    return (
        axes.behavior * BASE_WEIGHTS["behavior"]
        + axes.structure * BASE_WEIGHTS["structure"]
        + axes.survival * BASE_WEIGHTS["survival"]
        + axes.volatility * BASE_WEIGHTS["volatility"]
    )


def _dominant_axis(axes: AxisScores) -> str:
    d = axes.as_dict()
    return max(d, key=d.get)


def _make_headline(dominant: str, axes: AxisScores, total: float) -> str:
    msgs = {
        "behavior": "지금 충동적으로 움직이고 있을 수 있습니다. 잠시 멈추는 걸 권합니다.",
        "structure": "포지션 설계에 구멍이 있습니다. 손절·레버리지를 점검하세요.",
        "survival": "손실이 커질 여지가 있습니다. 노출을 줄이는 게 안전합니다.",
        "volatility": "이 자산의 변동성 대비 청산이 가깝습니다. 여유를 두세요.",
    }
    return msgs.get(dominant, "리스크를 점검하세요.")


def _explain(axes: AxisScores, m: RiskMetrics, b: BehaviorSignals) -> list[str]:
    out = []
    d = axes.as_dict()
    for axis, val in sorted(d.items(), key=lambda x: -x[1]):
        if val < 15:
            continue
        if axis == "behavior":
            out.append(f"행동 {val:.0f}: 최근 연패 {b.recent_losses_streak}회, "
                       f"비중 평소 {b.position_size_vs_avg:.1f}배")
        elif axis == "structure":
            sl = "있음" if m.has_stop_loss else "없음"
            out.append(f"구조 {val:.0f}: 손절 {sl}, 실효레버리지 {m.effective_leverage}x")
        elif axis == "survival":
            out.append(f"생존 {val:.0f}: 청산 시 자본 {m.liq_loss_pct_of_equity}% 손실 가능")
        elif axis == "volatility":
            out.append(f"변동성 {val:.0f}: 청산까지 {m.liq_distance_pct}%")
    return out


# ================================================================
# 자가 검증
# ================================================================
if __name__ == "__main__":
    from app.core.risk_math import PositionInput, compute_risk

    equity = D("10000")
    print("=== Jarvis Risk Score 검증 ===\n")

    # 시나리오 A: 침착한 트레이더 - 손절 있고, 충동 없고, 적정 레버리지
    posA = PositionInput("BTCUSDT", Side.LONG, D("0.3"), D("50000"), D("50000"),
                         D("5"), D("45000"), D("0"), stop_loss_price=D("49000"))
    mA = compute_risk(posA, equity)
    jrsA = compute_jarvis_risk_score(
        mA,
        AccountContext(equity, total_liq_loss_pct=8, correlated_exposure_pct=40),
        BehaviorSignals(recent_losses_streak=0, trades_last_30min=1, position_size_vs_avg=1.0),
        atr_pct=3.0,
    )
    print(f"[A] 침착한 트레이더")
    print(f"    JRS={jrsA.total} ({jrsA.band}) | 축={jrsA.axes.as_dict()}")
    print(f"    → {jrsA.headline}\n")

    # 시나리오 B: 충동형 - 연패 후 큰 비중 재진입, 손절 없음
    posB = PositionInput("ETHUSDT", Side.LONG, D("5"), D("3000"), D("3000"),
                         D("15"), D("2850"), D("0"), stop_loss_price=None)
    mB = compute_risk(posB, equity)
    jrsB = compute_jarvis_risk_score(
        mB,
        AccountContext(equity, total_liq_loss_pct=18, correlated_exposure_pct=60),
        BehaviorSignals(recent_losses_streak=3, minutes_since_last_loss=4,
                        trades_last_30min=8, position_size_vs_avg=2.8, leverage_vs_avg=2.0),
        atr_pct=5.0,
    )
    print(f"[B] 충동형 (연패 후 몰빵, 손절 없음)")
    print(f"    JRS={jrsB.total} ({jrsB.band}) | 축={jrsB.axes.as_dict()}")
    print(f"    지배축: {jrsB.dominant_axis}")
    print(f"    → {jrsB.headline}")
    for e in jrsB.explanation:
        print(f"      · {e}")
    print()

    # 시나리오 C: 청산 임박 - 긴급 오버라이드 발동 테스트
    posC = PositionInput("SOLUSDT", Side.LONG, D("100"), D("150"), D("148"),
                         D("25"), D("147"), D("-200"), stop_loss_price=None)
    mC = compute_risk(posC, equity)
    jrsC = compute_jarvis_risk_score(
        mC,
        AccountContext(equity, total_liq_loss_pct=45, correlated_exposure_pct=80),
        BehaviorSignals(recent_losses_streak=1, trades_last_30min=3, position_size_vs_avg=1.2),
        atr_pct=6.0,
    )
    print(f"[C] 청산 임박 (긴급 오버라이드 테스트)")
    print(f"    JRS={jrsC.total} ({jrsC.band}) | 축={jrsC.axes.as_dict()}")
    print(f"    오버라이드 발동: {jrsC.emergency_override}")
    print(f"    → {jrsC.headline}\n")

    print("✅ Jarvis Risk Score 검증 완료")
