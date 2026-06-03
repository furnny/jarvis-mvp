"""
Jarvis - 손절 관찰 추적기 (Stop-Loss Observation Tracker)
================================================================

문제: 과거 거래의 '손절 여부'는 API로 못 가져옴 (미체결 주문은 기록에 안 남음)

해결: 실시간 모니터링이 매 스냅샷마다 '지금 손절 있나'를 관찰 → 누적
      포지션이 닫히면 그 생애 동안의 관찰을 종합해 '손절 커버리지' 확정
      → 이게 과거 분석의 had_stop_loss 빈칸을 진짜 데이터로 채움

이 모듈이 실시간(monitoring) ↔ 과거분석(trade_analyzer)을 잇는 다리.

"손절을 걸었나"의 정의 — 단순 yes/no가 아니라 3가지로 기록:
  - ever_had_sl:      생애 중 한 번이라도 손절이 있었나
  - coverage_ratio:   포지션을 보유한 시간 중 손절이 걸려 있던 비율
  - had_sl_when_risky: 위험했던 순간(청산 근처)에 손절이 있었나 ← 가장 의미 있음
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Protocol


# ----------------------------------------------------------------
# 한 번의 관찰 (워커가 스냅샷마다 기록)
# ----------------------------------------------------------------
@dataclass
class StopLossObservation:
    user_id: int
    symbol: str
    observed_at: datetime
    has_stop_loss: bool        # 이 순간 손절이 걸려 있었나
    liq_distance_pct: float    # 이 순간 청산까지 거리 (위험도 판단용)
    position_qty: Decimal      # 포지션이 살아있는지 확인용


# ----------------------------------------------------------------
# 포지션 생애 동안 관찰을 누적하는 상태
# ----------------------------------------------------------------
@dataclass
class PositionLifecycle:
    """한 포지션의 관찰 누적. 포지션이 닫히면 확정됨."""
    user_id: int
    symbol: str
    first_seen: datetime
    last_seen: datetime
    total_observations: int = 0
    sl_observations: int = 0           # 손절이 있던 관찰 수
    risky_observations: int = 0        # 위험했던 순간 수 (청산 근처)
    risky_with_sl: int = 0             # 위험했는데 손절도 있던 수

    RISKY_THRESHOLD = 8.0  # 청산까지 8% 이내면 '위험한 순간'

    def record(self, obs: StopLossObservation):
        self.total_observations += 1
        self.last_seen = obs.observed_at
        if obs.has_stop_loss:
            self.sl_observations += 1
        if obs.liq_distance_pct < self.RISKY_THRESHOLD:
            self.risky_observations += 1
            if obs.has_stop_loss:
                self.risky_with_sl += 1

    def finalize(self) -> "StopLossVerdict":
        """포지션이 닫혔을 때 최종 손절 커버리지 확정"""
        ever = self.sl_observations > 0
        coverage = (self.sl_observations / self.total_observations
                    if self.total_observations > 0 else 0.0)
        # 위험한 순간이 있었다면, 그때 손절이 있었는지가 핵심
        if self.risky_observations > 0:
            had_when_risky = (self.risky_with_sl / self.risky_observations) >= 0.5
        else:
            # 위험한 순간이 없었으면 평소 커버리지로 판단
            had_when_risky = coverage >= 0.5

        return StopLossVerdict(
            symbol=self.symbol,
            ever_had_sl=ever,
            coverage_ratio=round(coverage, 2),
            had_sl_when_risky=had_when_risky,
            observations=self.total_observations,
        )


@dataclass
class StopLossVerdict:
    """포지션 종료 후 확정된 손절 판정 — 과거 분석에 주입됨"""
    symbol: str
    ever_had_sl: bool
    coverage_ratio: float       # 0~1
    had_sl_when_risky: bool     # 분석에서 had_stop_loss로 쓸 핵심 값
    observations: int


# ----------------------------------------------------------------
# 추적기 (워커가 사용)
# ----------------------------------------------------------------
class StopLossStore(Protocol):
    """확정된 판정 저장. 개발=메모리, 프로덕션=DB."""
    def save_verdict(self, user_id: int, entry_time: datetime, v: StopLossVerdict) -> None: ...
    def get_verdict(self, user_id: int, symbol: str, entry_time: datetime) -> Optional[StopLossVerdict]: ...


class InMemorySLStore:
    def __init__(self):
        self._data: dict[tuple, StopLossVerdict] = {}

    def save_verdict(self, user_id, entry_time, v):
        # 키: (user, symbol, 진입시각 분단위) — 과거 포지션과 매칭용
        key = (user_id, v.symbol, entry_time.replace(second=0, microsecond=0))
        self._data[key] = v

    def get_verdict(self, user_id, symbol, entry_time):
        key = (user_id, symbol, entry_time.replace(second=0, microsecond=0))
        return self._data.get(key)


class StopLossTracker:
    """
    실시간 워커가 매 스냅샷마다 호출.
    포지션이 살아있는 동안 관찰을 누적하고, 사라지면 확정 저장.
    """
    def __init__(self, store: StopLossStore):
        self._store = store
        self._active: dict[tuple, PositionLifecycle] = {}  # (user, symbol) -> lifecycle

    def observe(self, obs: StopLossObservation):
        """워커가 스냅샷마다 호출. 살아있는 포지션의 손절 상태를 기록."""
        key = (obs.user_id, obs.symbol)
        if obs.position_qty == 0:
            # 포지션이 닫힘 → 확정
            self._close(key)
            return
        if key not in self._active:
            self._active[key] = PositionLifecycle(
                user_id=obs.user_id, symbol=obs.symbol,
                first_seen=obs.observed_at, last_seen=obs.observed_at,
            )
        self._active[key].record(obs)

    def reconcile(self, user_id: int, live_symbols: set[str]):
        """워커가 매 사이클 끝에 호출. 더 이상 안 보이는 포지션을 닫힘 처리."""
        for key in list(self._active.keys()):
            uid, sym = key
            if uid == user_id and sym not in live_symbols:
                self._close(key)

    def _close(self, key):
        lifecycle = self._active.pop(key, None)
        if lifecycle and lifecycle.total_observations > 0:
            verdict = lifecycle.finalize()
            self._store.save_verdict(lifecycle.user_id, lifecycle.first_seen, verdict)


# ----------------------------------------------------------------
# 과거 분석에 손절 판정 주입
# ----------------------------------------------------------------
def enrich_trades_with_sl(trades, user_id: int, store: StopLossStore):
    """
    재조립된 과거 Trade들에 실시간이 쌓은 손절 판정을 매칭.
    매칭되면 had_stop_loss를 실제 관찰값으로 교체.
    매칭 안 되면(관찰 데이터 없는 옛 거래) 그대로 둠.
    """
    from app.core.trade_analyzer import Trade
    enriched = []
    matched = 0
    for t in trades:
        verdict = store.get_verdict(user_id, t.symbol, t.entry_time)
        if verdict is not None:
            matched += 1
            t = Trade(
                symbol=t.symbol, side=t.side,
                entry_time=t.entry_time, exit_time=t.exit_time,
                pnl_pct=t.pnl_pct, leverage=t.leverage,
                size_vs_avg=t.size_vs_avg,
                had_stop_loss=verdict.had_sl_when_risky,  # 핵심: 위험할 때 손절 있었나
            )
        enriched.append(t)
    return enriched, matched


# ================================================================
# 검증
# ================================================================
if __name__ == "__main__":
    from datetime import timedelta

    store = InMemorySLStore()
    tracker = StopLossTracker(store)

    print("=== 손절 관찰 추적 검증 ===\n")

    base = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

    # 포지션 A: 손절 없이 위험하게 보유하다 닫힘
    print("[포지션 A] 손절 없이 위험 구간 보유")
    for i in range(6):
        tracker.observe(StopLossObservation(
            user_id=1, symbol="BTCUSDT",
            observed_at=base + timedelta(minutes=i*5),
            has_stop_loss=False,
            liq_distance_pct=5.0,  # 위험 구간
            position_qty=Decimal("1.0"),
        ))
    # 닫힘
    tracker.observe(StopLossObservation(1, "BTCUSDT", base+timedelta(minutes=35),
                                        False, 5.0, Decimal("0")))
    vA = store.get_verdict(1, "BTCUSDT", base)
    print(f"   ever_had_sl={vA.ever_had_sl}  coverage={vA.coverage_ratio}  "
          f"위험할때손절={vA.had_sl_when_risky}  관찰={vA.observations}회\n")

    # 포지션 B: 처음엔 손절 없다가 중간에 손절 걸고 안전하게 종료
    print("[포지션 B] 늦게라도 손절 설정")
    base2 = base + timedelta(hours=2)
    for i in range(6):
        has_sl = i >= 2  # 3번째 관찰부터 손절 있음
        tracker.observe(StopLossObservation(
            user_id=1, symbol="ETHUSDT",
            observed_at=base2 + timedelta(minutes=i*5),
            has_stop_loss=has_sl,
            liq_distance_pct=10.0 if has_sl else 6.0,
            position_qty=Decimal("2.0"),
        ))
    tracker.reconcile(1, live_symbols=set())  # ETH 사라짐 → 닫힘
    vB = store.get_verdict(1, "ETHUSDT", base2)
    print(f"   ever_had_sl={vB.ever_had_sl}  coverage={vB.coverage_ratio}  "
          f"위험할때손절={vB.had_sl_when_risky}  관찰={vB.observations}회\n")

    # 과거 분석에 주입
    print("[과거 Trade에 손절 판정 주입]")
    from app.core.trade_analyzer import Trade
    old_trades = [
        Trade("BTCUSDT", "LONG", base, base+timedelta(minutes=35),
              -8.0, 10.0, 1.5, had_stop_loss=False),  # 곧 교체됨
        Trade("ETHUSDT", "LONG", base2, base2+timedelta(minutes=30),
              2.0, 15.0, 1.0, had_stop_loss=False),
        Trade("SOLUSDT", "LONG", base+timedelta(days=30), base, 1.0, 5.0, 1.0, False),  # 관찰없음
    ]
    enriched, matched = enrich_trades_with_sl(old_trades, 1, store)
    print(f"   {matched}/{len(old_trades)}건 매칭됨 (관찰 데이터 있는 것만)")
    for t in enriched:
        print(f"   {t.symbol}: had_stop_loss={t.had_stop_loss}")
    print()
    print("✅ 손절 추적 검증 완료")
    print("   → 실시간이 관찰 → 종료시 확정 → 과거 분석 빈칸을 메움")
