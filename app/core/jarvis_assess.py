"""
Jarvis Risk Assessment — validated axes only.

Backtest conclusions:
  Volatility (ATR)   +0.490  → in score
  Structure (SL/lev) +0.114  → in score (supplementary)
  Survival (liq)     -0.307  → guardrail only
  Behaviour (impulse)+0.002  → coaching only

Score 0–100. Band: LOW / MODERATE / HIGH / CRITICAL.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from app.core.risk_math import RiskMetrics


# ── Inputs ────────────────────────────────────────────────────────────────────

@dataclass
class AccountContext:
    equity: Decimal
    total_liq_loss_pct: Decimal    # % of equity lost if all positions liquidated
    correlated_exposure_pct: float # 0-100, how correlated open positions are


@dataclass
class BehaviorSignals:
    recent_losses_streak: int      # consecutive losses before this position
    minutes_since_last_loss: float # how quickly re-entered
    trades_last_30min: int         # trade frequency signal
    position_size_vs_avg: float    # 1.0 = average; >2 = big vs personal norm


# ── Outputs ───────────────────────────────────────────────────────────────────

@dataclass
class CoachingSignal:
    triggered: bool
    message: str = ""


@dataclass
class GuardrailAlert:
    triggered: bool
    reason: str = ""


@dataclass
class AssessResult:
    score: int              # 0-100, lower = riskier
    band: str               # LOW / MODERATE / HIGH / CRITICAL
    headline: str
    coaching: CoachingSignal
    guardrail: GuardrailAlert
    volatility_score: int   # 0-50, raw axis
    structure_score: int    # 0-50, raw axis


# ── Constants ─────────────────────────────────────────────────────────────────

_BANDS = [
    (80, "LOW"),
    (55, "MODERATE"),
    (30, "HIGH"),
    (0,  "CRITICAL"),
]

_HEADLINES = {
    "LOW":      "리스크 수준 양호. 계획대로 유지하세요.",
    "MODERATE": "주의 필요. 손절 설정 여부를 확인하세요.",
    "HIGH":     "위험 수준 높음. 포지션 축소를 검토하세요.",
    "CRITICAL": "즉각 조치 필요. 청산 위험이 가까워지고 있습니다.",
}


# ── Scoring ───────────────────────────────────────────────────────────────────

def _volatility_score(atr_pct: float, liq_distance_pct: Decimal) -> int:
    """
    Volatility axis (weight ~82% of validated signal).
    Higher ATR relative to liq distance = higher risk = lower score.
    Returns 0-50 (50 = safest).
    """
    liq_d = float(liq_distance_pct)
    if liq_d <= 0:
        return 0
    # ratio: how many ATR swings until liquidation
    swings_to_liq = liq_d / max(atr_pct, 0.1)
    # 5+ swings → very safe; <1 → critical
    raw = min(swings_to_liq / 5.0, 1.0)
    return int(raw * 50)


def _structure_score(metrics: RiskMetrics, eff_lev_limit: float = 15.0) -> int:
    """
    Structure axis: SL presence + leverage level.
    Returns 0-50.
    """
    score = 0

    # SL presence (25 pts)
    if metrics.has_stop_loss:
        score += 25
        # Bonus: SL risk within 2%
        if (metrics.stop_risk_pct_of_equity or Decimal("999")) <= Decimal("2"):
            score += 10

    # Leverage penalty (up to 25 pts)
    eff = float(metrics.effective_leverage)
    if eff <= 3:
        score += 25
    elif eff <= 5:
        score += 20
    elif eff <= 10:
        score += 12
    elif eff <= eff_lev_limit:
        score += 5
    # >limit → 0

    return min(score, 50)


def _guardrail(metrics: RiskMetrics, account: AccountContext) -> GuardrailAlert:
    """
    Survival axis — not in score, used as hard guardrail.
    Fires when a single liquidation would be account-damaging.
    """
    liq_pct = float(metrics.liq_loss_pct_of_equity)
    total_pct = float(account.total_liq_loss_pct)

    if liq_pct >= 20:
        return GuardrailAlert(
            True,
            f"이 포지션 청산 시 자본 {liq_pct:.1f}% 손실 — 회복 불능 구간"
        )
    if total_pct >= 30:
        return GuardrailAlert(
            True,
            f"전체 포지션 동시 청산 시 자본 {total_pct:.1f}% 손실 — 분산 필요"
        )
    if account.correlated_exposure_pct >= 80:
        return GuardrailAlert(
            True,
            f"포지션 상관도 {account.correlated_exposure_pct:.0f}% — 한 방향 쏠림 위험"
        )
    return GuardrailAlert(False)


def _coaching(signals: BehaviorSignals) -> CoachingSignal:
    """
    Behaviour axis — not in score, surfaces as coaching message.
    """
    msgs = []

    if signals.recent_losses_streak >= 2 and signals.minutes_since_last_loss < 15:
        msgs.append(
            f"연패 {signals.recent_losses_streak}회 후 {signals.minutes_since_last_loss:.0f}분 만에 재진입 — "
            "복수 매매 패턴. 15분 쿨다운을 권장합니다."
        )
    if signals.trades_last_30min >= 5:
        msgs.append(
            f"30분 내 {signals.trades_last_30min}건 — 과매매 신호. 속도를 줄이세요."
        )
    if signals.position_size_vs_avg >= 2.0:
        msgs.append(
            f"평소 대비 {signals.position_size_vs_avg:.1f}배 비중 — 당신답지 않은 크기입니다."
        )

    if msgs:
        return CoachingSignal(True, " / ".join(msgs))
    return CoachingSignal(False)


# ── Public API ────────────────────────────────────────────────────────────────

def assess(
    metrics: RiskMetrics,
    account: AccountContext,
    behavior: BehaviorSignals,
    atr_pct: float,
) -> AssessResult:
    vol = _volatility_score(atr_pct, metrics.liq_distance_pct)
    struct = _structure_score(metrics)
    total = vol + struct  # 0-100

    band = "CRITICAL"
    for threshold, label in _BANDS:
        if total >= threshold:
            band = label
            break

    return AssessResult(
        score=total,
        band=band,
        headline=_HEADLINES[band],
        coaching=_coaching(behavior),
        guardrail=_guardrail(metrics, account),
        volatility_score=vol,
        structure_score=struct,
    )
