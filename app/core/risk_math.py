"""
Precision risk math — two distinct risk types, all Decimal.

Stop Risk   : |entry − stop| × qty  (only when SL exists)
Liq Risk    : estimated loss if liquidated from current mark price
              Used as guardrail, NOT in score (backtest: -0.307 correlation).
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional

D = Decimal


class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class PositionInput:
    symbol: str
    side: Side
    qty: Decimal          # base asset quantity
    entry_price: Decimal
    mark_price: Decimal
    leverage: Decimal     # current setting (fallback if margin unknown)
    liq_price: Decimal
    unrealized_pnl: Decimal
    stop_loss_price: Optional[Decimal]


@dataclass
class RiskMetrics:
    symbol: str
    side: Side

    # — Stop-loss based (None when no SL)
    stop_risk_usd: Optional[Decimal]
    stop_risk_pct_of_equity: Optional[Decimal]

    # — Liquidation based (always calculable)
    liq_loss_usd: Decimal          # loss from current mark to liq
    liq_loss_pct_of_equity: Decimal

    # — Distance / leverage
    liq_distance_pct: Decimal      # |mark − liq| / mark × 100
    effective_leverage: Decimal    # notional / equity

    has_stop_loss: bool


def _pct(value: Decimal, total: Decimal, decimals: int = 2) -> Decimal:
    if total == 0:
        return D("0")
    return (value / total * D("100")).quantize(
        Decimal("0." + "0" * decimals), rounding=ROUND_HALF_UP
    )


def compute_risk(pos: PositionInput, equity: Decimal) -> RiskMetrics:
    """
    Compute both risk types for a position.
    Raises ValueError if equity is zero.
    """
    if equity <= 0:
        raise ValueError("equity must be positive")

    notional = pos.mark_price * pos.qty

    # Effective leverage: notional / equity
    eff_lev = (notional / equity).quantize(D("0.01"), rounding=ROUND_HALF_UP)

    # Liquidation distance
    liq_dist = abs(pos.mark_price - pos.liq_price)
    liq_dist_pct = _pct(liq_dist, pos.mark_price)

    # Liquidation loss (mark → liq, current unrealised already embedded)
    liq_loss_usd = liq_dist * pos.qty
    liq_loss_pct = _pct(liq_loss_usd, equity)

    # Stop-loss risk
    has_sl = pos.stop_loss_price is not None and pos.stop_loss_price > 0
    if has_sl:
        sl_dist = abs(pos.entry_price - pos.stop_loss_price)
        stop_risk_usd: Optional[Decimal] = sl_dist * pos.qty
        stop_risk_pct: Optional[Decimal] = _pct(stop_risk_usd, equity)
    else:
        stop_risk_usd = None
        stop_risk_pct = None

    return RiskMetrics(
        symbol=pos.symbol,
        side=pos.side,
        stop_risk_usd=stop_risk_usd,
        stop_risk_pct_of_equity=stop_risk_pct,
        liq_loss_usd=liq_loss_usd,
        liq_loss_pct_of_equity=liq_loss_pct,
        liq_distance_pct=liq_dist_pct,
        effective_leverage=eff_lev,
        has_stop_loss=has_sl,
    )
