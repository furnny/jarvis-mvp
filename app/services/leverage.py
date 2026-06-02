"""
Jarvis - 레버리지 해석 (Leverage Resolution)
================================================================

문제: 바이낸스는 '과거 거래 시점의 레버리지'를 직접 안 줌
  - positionRisk: 현재 설정값만 (과거 X)
  - userTrades: 레버리지 정보 없음

해결: 세 단계 우선순위로 채움
  1순위 - income 기록의 증거금 변동으로 역산 (가장 정확, 가능할 때)
  2순위 - 현재 설정 레버리지 (positionRisk) — 근사치
  3순위 - 추정 불가 → None (분석에서 제외, 정직하게)

핵심 원리:
  레버리지 ≈ 명목가치 / 사용증거금
  명목가치 = 진입가 × 수량 (재조립에서 이미 앎)
  증거금 = income 기록 또는 잔고 변동에서 추정
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from app.services.trade_history import ReconstructedPosition


@dataclass
class LeverageEstimate:
    value: Optional[float]   # 추정 레버리지, 불가하면 None
    source: str              # "inferred" / "current_setting" / "unknown"
    confidence: str          # "high" / "medium" / "low"


# ----------------------------------------------------------------
# 1순위: 증거금으로 역산
# ----------------------------------------------------------------
def infer_from_margin(
    notional: Decimal,
    margin_used: Optional[Decimal],
) -> Optional[LeverageEstimate]:
    """
    명목가치 / 증거금 = 레버리지.
    margin_used를 income 기록 등에서 얻을 수 있을 때만 가능.
    """
    if margin_used is None or margin_used <= 0 or notional <= 0:
        return None
    lev = float(notional / margin_used)
    # 바이낸스 레버리지는 보통 정수 단계 → 가장 가까운 일반값으로 스냅
    snapped = _snap_to_common(lev)
    # 역산값과 스냅값이 가까우면 신뢰도 높음
    conf = "high" if abs(lev - snapped) / snapped < 0.15 else "medium"
    return LeverageEstimate(value=snapped, source="inferred", confidence=conf)


COMMON_LEVERAGES = [1, 2, 3, 5, 10, 15, 20, 25, 50, 75, 100, 125]


def _snap_to_common(lev: float) -> float:
    """역산 레버리지를 바이낸스가 제공하는 일반 단계로 스냅"""
    return float(min(COMMON_LEVERAGES, key=lambda c: abs(c - lev)))


# ----------------------------------------------------------------
# 2순위: 현재 설정값
# ----------------------------------------------------------------
def from_current_setting(
    symbol: str,
    current_leverage_map: dict[str, float],
) -> Optional[LeverageEstimate]:
    if symbol in current_leverage_map:
        return LeverageEstimate(
            value=current_leverage_map[symbol],
            source="current_setting",
            confidence="low",  # 과거와 다를 수 있어 신뢰도 낮음
        )
    return None


# ----------------------------------------------------------------
# 통합 해석기
# ----------------------------------------------------------------
def resolve_leverage(
    position: ReconstructedPosition,
    margin_used: Optional[Decimal] = None,
    current_leverage_map: Optional[dict[str, float]] = None,
) -> LeverageEstimate:
    """우선순위대로 레버리지를 해석"""
    notional = position.entry_price * position.qty

    # 1순위: 증거금 역산
    est = infer_from_margin(notional, margin_used)
    if est is not None:
        return est

    # 2순위: 현재 설정
    if current_leverage_map:
        est = from_current_setting(position.symbol, current_leverage_map)
        if est is not None:
            return est

    # 3순위: 불가
    return LeverageEstimate(value=None, source="unknown", confidence="low")


# ----------------------------------------------------------------
# 거래소에서 현재 레버리지 + 증거금 가져오기
# ----------------------------------------------------------------
async def fetch_current_leverage(client) -> dict[str, float]:
    """positionRisk로 현재 설정된 레버리지 맵 조회"""
    raw = await client._signed_get("/fapi/v2/positionRisk")
    result = {}
    for p in raw:
        try:
            result[p["symbol"]] = float(p["leverage"])
        except (KeyError, ValueError):
            continue
    return result


async def fetch_margin_history(client, start_ms: int) -> dict[str, Decimal]:
    """
    income 기록에서 심볼별 증거금 정보 추출 시도.
    바이낸스 income type 중 일부로 증거금 흐름을 근사.
    완벽하진 않아 — 가능한 만큼만.
    """
    raw = await client._signed_get("/fapi/v1/income", {
        "startTime": start_ms, "limit": 1000,
    })
    # 실제론 더 정교한 매핑 필요. 여기선 인터페이스만.
    margin_map: dict[str, Decimal] = {}
    return margin_map


# ================================================================
# 검증
# ================================================================
if __name__ == "__main__":
    from datetime import datetime, timezone

    def mk_pos(symbol, entry, qty):
        return ReconstructedPosition(
            symbol=symbol, side="LONG",
            entry_time=datetime(2026,1,1,tzinfo=timezone.utc),
            exit_time=datetime(2026,1,1,1,tzinfo=timezone.utc),
            entry_price=Decimal(str(entry)), qty=Decimal(str(qty)),
            realized_pnl=Decimal("100"), leverage=Decimal("0"),
        )

    print("=== 레버리지 해석 검증 ===\n")

    # 케이스 1: 증거금 역산 (명목 $50,000, 증거금 $5,000 → 10x)
    pos1 = mk_pos("BTCUSDT", 50000, 1.0)
    est1 = resolve_leverage(pos1, margin_used=Decimal("5000"))
    print(f"[1] 증거금 역산 (명목 $50k, 증거금 $5k)")
    print(f"    → {est1.value}x  출처={est1.source}  신뢰도={est1.confidence}\n")

    # 케이스 2: 역산 약간 어긋남 (명목 $50,000, 증거금 $2,400 → ~20.8x → 20x 스냅)
    pos2 = mk_pos("ETHUSDT", 50000, 1.0)
    est2 = resolve_leverage(pos2, margin_used=Decimal("2400"))
    print(f"[2] 증거금 역산 (명목 $50k, 증거금 $2.4k → 20.8x)")
    print(f"    → {est2.value}x (일반단계로 스냅)  신뢰도={est2.confidence}\n")

    # 케이스 3: 증거금 없음 → 현재 설정값 폴백
    pos3 = mk_pos("SOLUSDT", 150, 100)
    est3 = resolve_leverage(pos3, margin_used=None,
                            current_leverage_map={"SOLUSDT": 5.0})
    print(f"[3] 증거금 없음 → 현재 설정값 폴백")
    print(f"    → {est3.value}x  출처={est3.source}  신뢰도={est3.confidence}\n")

    # 케이스 4: 둘 다 없음 → 불가
    pos4 = mk_pos("DOGEUSDT", 0.1, 10000)
    est4 = resolve_leverage(pos4)
    print(f"[4] 정보 없음 → 정직하게 불가")
    print(f"    → value={est4.value}  출처={est4.source}\n")

    print("✅ 레버리지 해석 검증 완료")
