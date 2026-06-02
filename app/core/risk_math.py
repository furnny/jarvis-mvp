"""
Jarvis - Risk Math (정밀 리스크 계산)
================================================

기존 MVP의 치명적 오류 수정:
- 기존: risk_pct = position_value / balance * 100  (← 이건 "포지션 크기"지 "리스크"가 아님)
- 수정: 실제 리스크 = 손절선까지 갔을 때 잃는 금액 / 자본 * 100

핵심 개념 정리:
- Notional (명목가치) = 수량 * 마크가격
- Margin (증거금) = Notional / leverage
- Stop Risk (실제 리스크) = |entry - stop| * 수량  (손절선이 있을 때만 계산 가능)
- Liquidation Risk (청산 리스크) = 손절선이 없을 때의 잠재 최대 손실 추정
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional


# 금융 계산은 float가 아니라 Decimal로. float 누적 오차가 돈 계산에선 치명적임.
def D(value) -> Decimal:
    """안전한 Decimal 변환"""
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def pct(value: Decimal, places: int = 2) -> float:
    """표시용 퍼센트 반올림"""
    return float(value.quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP))


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class PositionInput:
    """거래소에서 받은 원시 포지션 데이터 (정규화됨)"""
    symbol: str
    side: Side
    quantity: Decimal           # 절대값 (항상 양수)
    entry_price: Decimal
    mark_price: Decimal
    leverage: Decimal
    liquidation_price: Decimal  # 0이면 청산가 없음
    unrealized_pnl: Decimal
    stop_loss_price: Optional[Decimal]  # None이면 손절 미설정
    isolated_margin: Optional[Decimal] = None  # 격리마진일 경우


@dataclass(frozen=True)
class RiskMetrics:
    """계산된 리스크 지표 - 이게 모든 룰의 입력값이 됨"""
    symbol: str
    side: Side

    notional_usd: float         # 명목가치
    margin_usd: float           # 묶인 증거금

    # 실제 리스크 (손절 기준) - 손절이 있을 때만 의미 있음
    has_stop_loss: bool
    stop_risk_usd: Optional[float]      # 손절까지 갔을 때 잃을 금액
    stop_risk_pct_of_equity: Optional[float]  # 그게 자본의 몇 %인지 ← 진짜 "리스크%"

    # 청산 리스크 (손절 없을 때의 안전판)
    liq_distance_pct: float     # 마크가격 대비 청산가까지 거리 %
    liq_loss_usd: float         # 청산당하면 잃을 금액 (증거금 전체에 근접)
    liq_loss_pct_of_equity: float

    # 레버리지 / 익스포저
    effective_leverage: float   # 명목가치 / 자본 (전체 계좌 기준 실효 레버리지)
    margin_ratio_pct: float     # 사용 증거금 / 자본

    unrealized_pnl_usd: float


def compute_risk(
    pos: PositionInput,
    account_equity: Decimal,
) -> RiskMetrics:
    """
    단일 포지션의 정밀 리스크 계산.

    account_equity = wallet balance + unrealized PnL (실제 순자산)
    이걸 기준으로 모든 리스크를 % 환산해야 정확함.
    """
    if account_equity <= 0:
        account_equity = Decimal("1")  # 0 나눗셈 방지

    notional = pos.quantity * pos.mark_price
    margin = notional / pos.leverage if pos.leverage > 0 else notional

    # --- 1) 실제 리스크: 손절선까지 갔을 때 손실 ---
    has_sl = pos.stop_loss_price is not None and pos.stop_loss_price > 0
    stop_risk_usd: Optional[Decimal] = None
    stop_risk_pct: Optional[Decimal] = None

    if has_sl:
        sl = pos.stop_loss_price
        if pos.side == Side.LONG:
            # 롱: 손절가가 진입가보다 낮아야 정상. 가격차 * 수량 = 손실
            price_move = pos.entry_price - sl
        else:
            # 숏: 손절가가 진입가보다 높아야 정상
            price_move = sl - pos.entry_price

        # 손절이 이미 이익 구간(브레이크이븐 위)이면 리스크는 0 이하 → 0으로 클램프
        raw_risk = price_move * pos.quantity
        stop_risk_usd = max(raw_risk, Decimal("0"))
        stop_risk_pct = (stop_risk_usd / account_equity) * Decimal("100")

    # --- 2) 청산 리스크: 손절 없을 때의 최후 방어선 ---
    if pos.liquidation_price > 0 and pos.mark_price > 0:
        liq_distance = abs(pos.mark_price - pos.liquidation_price) / pos.mark_price * Decimal("100")
        # 청산 시 손실 ≈ 진입~청산 가격차 * 수량 (격리마진이면 증거금 한도)
        if pos.side == Side.LONG:
            liq_price_move = pos.entry_price - pos.liquidation_price
        else:
            liq_price_move = pos.liquidation_price - pos.entry_price
        liq_loss = max(liq_price_move * pos.quantity, Decimal("0"))
        # 격리마진이면 손실은 증거금으로 한정
        if pos.isolated_margin is not None and pos.isolated_margin > 0:
            liq_loss = min(liq_loss, pos.isolated_margin)
    else:
        liq_distance = Decimal("999")  # 청산가 없음 = 안전
        liq_loss = Decimal("0")

    liq_loss_pct = (liq_loss / account_equity) * Decimal("100")

    # --- 3) 실효 레버리지 & 증거금 비율 ---
    effective_lev = notional / account_equity
    margin_ratio = (margin / account_equity) * Decimal("100")

    return RiskMetrics(
        symbol=pos.symbol,
        side=pos.side,
        notional_usd=pct(notional),
        margin_usd=pct(margin),
        has_stop_loss=has_sl,
        stop_risk_usd=pct(stop_risk_usd) if stop_risk_usd is not None else None,
        stop_risk_pct_of_equity=pct(stop_risk_pct) if stop_risk_pct is not None else None,
        liq_distance_pct=pct(liq_distance),
        liq_loss_usd=pct(liq_loss),
        liq_loss_pct_of_equity=pct(liq_loss_pct),
        effective_leverage=pct(effective_lev),
        margin_ratio_pct=pct(margin_ratio),
        unrealized_pnl_usd=pct(pos.unrealized_pnl),
    )


# ============================================================
# 자가 검증 테스트 - 계산이 맞는지 직접 확인
# ============================================================
if __name__ == "__main__":
    print("=== 리스크 계산 검증 ===\n")

    equity = D("10000")  # 자본 $10,000

    # 케이스 1: 손절 있는 롱 포지션
    # BTC $50,000 진입, 1 BTC, 10x, 손절 $49,000
    # 실제 리스크 = ($50,000 - $49,000) * 1 = $1,000 = 자본의 10%
    pos1 = PositionInput(
        symbol="BTCUSDT", side=Side.LONG,
        quantity=D("1"), entry_price=D("50000"), mark_price=D("50000"),
        leverage=D("10"), liquidation_price=D("45500"),
        unrealized_pnl=D("0"), stop_loss_price=D("49000"),
    )
    r1 = compute_risk(pos1, equity)
    print(f"케이스1 (손절 있음):")
    print(f"  명목가치: ${r1.notional_usd:,.0f}")
    print(f"  증거금: ${r1.margin_usd:,.0f}")
    print(f"  실제 리스크(손절기준): ${r1.stop_risk_usd:,.0f} = {r1.stop_risk_pct_of_equity}% of equity")
    print(f"  → 기존 MVP라면 '리스크 50%'라고 잘못 표시했을 것 (notional/balance)")
    print(f"  실효 레버리지: {r1.effective_leverage}x")
    print(f"  청산까지 거리: {r1.liq_distance_pct}%\n")

    # 케이스 2: 손절 없는 고위험 숏
    pos2 = PositionInput(
        symbol="ETHUSDT", side=Side.SHORT,
        quantity=D("10"), entry_price=D("3000"), mark_price=D("3050"),
        leverage=D("20"), liquidation_price=D("3140"),
        unrealized_pnl=D("-500"), stop_loss_price=None,
    )
    r2 = compute_risk(pos2, equity)
    print(f"케이스2 (손절 없음, 20x 숏, 물려있음):")
    print(f"  명목가치: ${r2.notional_usd:,.0f}")
    print(f"  손절 리스크: {r2.stop_risk_pct_of_equity} (None = 손절 없어서 계산 불가)")
    print(f"  청산까지 거리: {r2.liq_distance_pct}%")
    print(f"  청산 시 손실: ${r2.liq_loss_usd:,.0f} = {r2.liq_loss_pct_of_equity}% of equity")
    print(f"  미실현 손익: ${r2.unrealized_pnl_usd:,.0f}")
    print(f"  → 손절이 없으니 '청산 리스크'로 경고해야 함\n")

    print("✅ 계산 검증 완료")
