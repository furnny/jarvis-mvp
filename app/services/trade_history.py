"""
Trade History — fill-to-position reconstruction + Trade conversion.

Fill    : raw exchange execution record
Position: reconstructed closed trade (FIFO matching)
Trade   : analysis-ready record with leverage & equity context
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from app.core.trade_analyzer import Trade

D = Decimal


# ── Raw fill ──────────────────────────────────────────────────────────────────

@dataclass
class Fill:
    symbol: str
    time: datetime
    side: str            # "BUY" or "SELL"
    price: Decimal
    qty: Decimal
    realized_pnl: Decimal
    fee: Decimal


# ── Reconstructed position ────────────────────────────────────────────────────

@dataclass
class Position:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal
    side: str            # "LONG" or "SHORT"
    realized_pnl: Decimal
    fees: Decimal


# ── Reconstruction ────────────────────────────────────────────────────────────

def reconstruct_positions(fills: List[Fill]) -> List[Position]:
    """
    FIFO matching of fills into closed Position objects.
    Handles partial closes and re-entries on the same symbol.
    """
    # Group by symbol
    by_symbol: Dict[str, List[Fill]] = {}
    for f in fills:
        by_symbol.setdefault(f.symbol, []).append(f)

    positions: List[Position] = []

    for symbol, sym_fills in by_symbol.items():
        sym_fills.sort(key=lambda f: f.time)

        # Running inventory queue: list of (price, qty, time) per direction
        long_queue: List[tuple] = []   # (price, qty, time)
        short_queue: List[tuple] = []

        pnl_acc = D("0")
        fee_acc = D("0")

        for fill in sym_fills:
            fee_acc += fill.fee
            pnl_acc += fill.realized_pnl

            if fill.side == "BUY":
                # Opens LONG or closes SHORT
                remaining = fill.qty
                closed_short = []
                while remaining > 0 and short_queue:
                    ep, eq, et = short_queue[0]
                    matched = min(remaining, eq)
                    closed_short.append((ep, matched, et, fill.price, fill.time))
                    if matched == eq:
                        short_queue.pop(0)
                    else:
                        short_queue[0] = (ep, eq - matched, et)
                    remaining -= matched

                for ep, mq, et, xp, xt in closed_short:
                    positions.append(Position(
                        symbol=symbol, entry_time=et, exit_time=xt,
                        entry_price=ep, exit_price=xp, qty=mq,
                        side="SHORT", realized_pnl=pnl_acc, fees=fee_acc,
                    ))
                    pnl_acc = D("0")
                    fee_acc = D("0")

                if remaining > 0:
                    long_queue.append((fill.price, remaining, fill.time))

            else:  # SELL
                # Opens SHORT or closes LONG
                remaining = fill.qty
                closed_long = []
                while remaining > 0 and long_queue:
                    ep, eq, et = long_queue[0]
                    matched = min(remaining, eq)
                    closed_long.append((ep, matched, et, fill.price, fill.time))
                    if matched == eq:
                        long_queue.pop(0)
                    else:
                        long_queue[0] = (ep, eq - matched, et)
                    remaining -= matched

                for ep, mq, et, xp, xt in closed_long:
                    positions.append(Position(
                        symbol=symbol, entry_time=et, exit_time=xt,
                        entry_price=ep, exit_price=xp, qty=mq,
                        side="LONG", realized_pnl=pnl_acc, fees=fee_acc,
                    ))
                    pnl_acc = D("0")
                    fee_acc = D("0")

                if remaining > 0:
                    short_queue.append((fill.price, remaining, fill.time))

    positions.sort(key=lambda p: p.exit_time)
    return positions


# ── Trade conversion ──────────────────────────────────────────────────────────

def to_trades(
    positions: List[Position],
    equity: Decimal,
    margin_lookup: Optional[Dict[str, Decimal]] = None,
) -> List[Trade]:
    """
    Convert Position list to Trade list for TradeAnalyzer.
    margin_lookup: {symbol: margin_used} for leverage inference.
    """
    trades: List[Trade] = []

    for pos in positions:
        notional = pos.entry_price * pos.qty

        # Leverage: infer from margin if available, else default 1
        margin = (margin_lookup or {}).get(pos.symbol)
        if margin and margin > 0:
            leverage = (notional / margin).quantize(D("0.01"))
        else:
            leverage = D("1")

        pnl_pct = (pos.realized_pnl / equity * D("100")).quantize(D("0.0001"))
        win = pos.realized_pnl > 0

        trades.append(Trade(
            symbol=pos.symbol,
            entry_time=pos.entry_time,
            exit_time=pos.exit_time,
            pnl_pct=pnl_pct,
            leverage=leverage,
            notional=notional,
            has_stop_loss=False,  # enriched by enrich_trades_with_sl
            win=win,
        ))

    return trades
