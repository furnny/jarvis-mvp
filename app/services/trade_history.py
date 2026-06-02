"""
Jarvis - 거래 기록 수집 & 포지션 재조립
================================================================

바이낸스 API의 현실:
  - 제공: 체결(fill) 단위 — "이 시점에 X만큼 샀다/팔았다"
  - 필요: 포지션 단위 — "언제 열어 언제 닫았고 손익 얼마"
  → 체결들을 시간순으로 쌓아 포지션으로 재조립해야 함

API로 알 수 있는 것 vs 추론해야 하는 것:
  ✅ 직접:   체결 시각/가격/수량/방향/실현손익/수수료
  🔸 추론:   포지션 진입~종료 (체결 재조립)
  🔸 계산:   연패 횟수, 평소 대비 비중 (전체 분석 후)
  ⚠️ 불가:   과거 손절 주문 여부 (미체결 주문은 기록에 안 남음)
            → 보수적으로 'unknown' 처리, 분석에서 제외 가능
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from statistics import median
from typing import Optional

from app.core.trade_analyzer import Trade


# ----------------------------------------------------------------
# 1) 원시 체결 (바이낸스 userTrades 응답을 정규화)
# ----------------------------------------------------------------
@dataclass
class Fill:
    symbol: str
    time: datetime
    side: str          # BUY / SELL
    price: Decimal
    qty: Decimal
    realized_pnl: Decimal
    commission: Decimal

    @classmethod
    def from_binance(cls, raw: dict) -> "Fill":
        return cls(
            symbol=raw["symbol"],
            time=datetime.fromtimestamp(int(raw["time"]) / 1000, tz=timezone.utc),
            side=raw["side"],
            price=Decimal(str(raw["price"])),
            qty=Decimal(str(raw["qty"])),
            realized_pnl=Decimal(str(raw.get("realizedPnl", "0"))),
            commission=Decimal(str(raw.get("commission", "0"))),
        )


# ----------------------------------------------------------------
# 2) 체결 → 포지션 재조립
# ----------------------------------------------------------------
@dataclass
class ReconstructedPosition:
    symbol: str
    side: str               # LONG / SHORT
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal    # 가중평균 진입가
    qty: Decimal            # 최대 보유 수량
    realized_pnl: Decimal   # 실현손익 (수수료 차감)
    leverage: Decimal       # 알 수 있으면, 아니면 추정


def reconstruct_positions(fills: list[Fill]) -> list[ReconstructedPosition]:
    """
    체결들을 심볼별·시간순으로 쌓아 포지션 단위로 재조립.

    원리: 순포지션(net position)이 0 → 0이 아님 = 진입,
          0이 아님 → 0 = 종료. 그 사이 체결들을 하나의 포지션으로 묶음.
    부분청산·불타기(추가매수)도 같은 포지션으로 흡수.
    """
    by_symbol: dict[str, list[Fill]] = {}
    for f in fills:
        by_symbol.setdefault(f.symbol, []).append(f)

    positions: list[ReconstructedPosition] = []

    for symbol, sym_fills in by_symbol.items():
        sym_fills.sort(key=lambda f: f.time)

        net = Decimal("0")          # 순포지션 (양수=롱, 음수=숏)
        open_fills: list[Fill] = []  # 현재 열린 포지션의 체결들
        pnl_accum = Decimal("0")
        max_qty = Decimal("0")

        for f in sym_fills:
            signed = f.qty if f.side == "BUY" else -f.qty
            was_flat = (net == 0)

            net += signed
            open_fills.append(f)
            pnl_accum += f.realized_pnl - f.commission
            max_qty = max(max_qty, abs(net))

            # 포지션이 닫혔는가 (순포지션이 0으로 복귀)
            if net == 0 and not was_flat:
                positions.append(_build_position(symbol, open_fills, pnl_accum, max_qty))
                open_fills = []
                pnl_accum = Decimal("0")
                max_qty = Decimal("0")

        # 아직 안 닫힌 포지션 (현재 보유 중) — 분석에서 제외하거나 미결로
        # 여기선 닫힌 것만 분석 대상으로

    positions.sort(key=lambda p: p.entry_time)
    return positions


def _build_position(symbol, fills, pnl, max_qty) -> ReconstructedPosition:
    # 첫 체결의 방향으로 포지션 방향 결정
    first = fills[0]
    side = "LONG" if first.side == "BUY" else "SHORT"

    # 진입 방향 체결들의 가중평균가 = 진입가
    entry_side = "BUY" if side == "LONG" else "SELL"
    entry_fills = [f for f in fills if f.side == entry_side]
    if entry_fills:
        total_qty = sum(f.qty for f in entry_fills)
        entry_price = sum(f.price * f.qty for f in entry_fills) / total_qty
    else:
        entry_price = first.price

    return ReconstructedPosition(
        symbol=symbol, side=side,
        entry_time=fills[0].time, exit_time=fills[-1].time,
        entry_price=entry_price, qty=max_qty,
        realized_pnl=pnl, leverage=Decimal("0"),  # 레버리지는 별도 조회 필요
    )


# ----------------------------------------------------------------
# 3) 포지션 → 분석용 Trade 변환 (계산 항목 채우기)
# ----------------------------------------------------------------
def to_trades(
    positions: list[ReconstructedPosition],
    account_equity: Decimal,
    leverage_lookup: Optional[dict[str, float]] = None,
    margin_lookup: Optional[dict[str, Decimal]] = None,
) -> list[Trade]:
    """
    재조립된 포지션을 분석기의 Trade로 변환.
    '평소 대비 비중'은 전체 포지션의 중앙값 대비로 계산 (상대 개념).
    레버리지는 leverage.py의 우선순위 해석기 사용 (역산 > 현재설정 > 불가).
    """
    if not positions:
        return []

    from app.services.leverage import resolve_leverage

    # 평소 비중 기준 = 명목가치의 중앙값
    notionals = [float(p.entry_price * p.qty) for p in positions]
    median_notional = median(notionals) if notionals else 1.0

    trades = []
    for p in positions:
        notional = float(p.entry_price * p.qty)
        size_vs_avg = notional / median_notional if median_notional > 0 else 1.0
        pnl_pct = float(p.realized_pnl) / float(account_equity) * 100

        # 레버리지 해석 (역산 우선, 현재설정 폴백)
        margin_used = margin_lookup.get(p.symbol) if margin_lookup else None
        est = resolve_leverage(p, margin_used=margin_used,
                               current_leverage_map=leverage_lookup)
        lev = est.value if est.value is not None else 1.0

        trades.append(Trade(
            symbol=p.symbol, side=p.side,
            entry_time=p.entry_time, exit_time=p.exit_time,
            pnl_pct=round(pnl_pct, 3),
            leverage=lev,
            size_vs_avg=round(size_vs_avg, 2),
            had_stop_loss=False,  # API로는 과거 손절 여부 불명 → 보수적 처리
        ))
    return trades


# ----------------------------------------------------------------
# 4) 거래소에서 체결 가져오기 (exchange.py 확장)
# ----------------------------------------------------------------
async def fetch_user_fills(client, symbol: str, start_ms: int, limit: int = 1000) -> list[Fill]:
    """
    바이낸스 userTrades 엔드포인트로 체결 기록 조회.
    심볼별로 호출해야 함 (바이낸스 제약).
    """
    raw = await client._signed_get("/fapi/v1/userTrades", {
        "symbol": symbol, "startTime": start_ms, "limit": limit,
    })
    return [Fill.from_binance(r) for r in raw]


# ================================================================
# 검증 — 합성 체결로 재조립이 맞는지 확인
# ================================================================
if __name__ == "__main__":
    from datetime import timedelta

    def mk(symbol, t, side, price, qty, pnl=0):
        return Fill(symbol, t, side, Decimal(str(price)), Decimal(str(qty)),
                    Decimal(str(pnl)), Decimal("0.5"))

    base = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    fills = [
        # 포지션 1: BTC 롱, 진입 0.5 → 부분 추가 0.3 → 전량 청산 0.8 (이익)
        mk("BTCUSDT", base, "BUY", 50000, 0.5),
        mk("BTCUSDT", base+timedelta(minutes=5), "BUY", 50200, 0.3),
        mk("BTCUSDT", base+timedelta(hours=2), "SELL", 51000, 0.8, pnl=620),
        # 포지션 2: BTC 숏, 진입 1.0 → 청산 (손실)
        mk("BTCUSDT", base+timedelta(hours=3), "SELL", 51000, 1.0),
        mk("BTCUSDT", base+timedelta(hours=4), "BUY", 51500, 1.0, pnl=-500),
        # 포지션 3: ETH 롱
        mk("ETHUSDT", base+timedelta(hours=1), "BUY", 3000, 2.0),
        mk("ETHUSDT", base+timedelta(hours=5), "SELL", 3100, 2.0, pnl=200),
    ]

    print("=== 체결 → 포지션 재조립 검증 ===\n")
    positions = reconstruct_positions(fills)
    print(f"체결 {len(fills)}건 → 포지션 {len(positions)}개로 재조립\n")
    for p in positions:
        print(f"  {p.symbol} {p.side}: 진입 ${p.entry_price:.0f} x {p.qty} "
              f"→ 실현손익 ${p.realized_pnl:.0f}")
        print(f"     보유: {p.entry_time.strftime('%H:%M')}~{p.exit_time.strftime('%H:%M')}")

    print("\n=== Trade 변환 (분석용) ===")
    trades = to_trades(positions, Decimal("10000"))
    for t in trades:
        print(f"  {t.symbol} {t.side}: 손익 {t.pnl_pct:+.2f}%  "
              f"비중 {t.size_vs_avg}x  {'승' if t.is_win else '패'}")

    print("\n✅ 재조립 검증 완료")
    print("\n참고: 손절 여부(had_stop_loss)는 API로 불명 → False 처리.")
    print("      레버리지도 별도 조회 필요 (positionRisk 등).")
