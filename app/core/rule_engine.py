"""
Jarvis - Rule Engine (정밀 룰 엔진)
================================================

기존 MVP의 문제 수정:
1. "포지션 오픈 시간"을 추적 못 함 → 손절 타임아웃 룰이 작동 안 했음
   → 포지션을 상태로 추적해서 "처음 본 시각"을 기록
2. 리스크 계산이 틀림 → risk_math.py의 정밀 계산 사용
3. 쿨다운이 in-memory dict → 재시작하면 날아감, 멀티 워커에서 공유 안 됨
   → 쿨다운/상태를 외부 저장소(Redis)에 위임할 수 있는 인터페이스로 분리
4. 알림 중복/스팸 → "동일 위반은 해소될 때까지 한 번만" (hysteresis)
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Protocol

from app.core.risk_math import RiskMetrics, Side


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class RuleType(str, Enum):
    EXCESSIVE_RISK = "excessive_risk"        # 손절 기준 실제 리스크 초과
    NO_STOP_LOSS = "no_stop_loss"            # 손절 미설정 (시간 경과 후)
    LIQUIDATION_PROXIMITY = "liq_proximity"  # 청산 임박
    OVER_LEVERAGE = "over_leverage"          # 실효 레버리지 과다
    REVENGE_PATTERN = "revenge_pattern"      # 복수매매 패턴


@dataclass
class UserRiskConfig:
    """유저별 설정 (DB에서 로드). 기본값은 보수적으로."""
    max_stop_risk_pct: float = 2.0        # 손절 리스크 한도 (자본 대비 %)
    min_liq_distance_pct: float = 8.0     # 청산까지 최소 거리 %
    max_effective_leverage: float = 10.0  # 실효 레버리지 한도
    no_sl_grace_minutes: float = 5.0      # 손절 없이 허용되는 시간
    revenge_window_minutes: float = 30.0
    revenge_max_trades: int = 6           # 이 시간 내 거래 횟수 한도


@dataclass
class Alert:
    rule_type: RuleType
    severity: Severity
    symbol: str
    title: str
    body: str
    suggestion: str
    metrics_snapshot: dict
    dedup_key: str          # 중복 방지 키 (룰+심볼+유저)
    created_at: float = field(default_factory=time.time)


# ------------------------------------------------------------
# 위반 상태 추적 (hysteresis) - 알림 스팸 방지의 핵심
# ------------------------------------------------------------
class ViolationStore(Protocol):
    """위반 상태 저장 인터페이스. 개발=메모리, 프로덕션=Redis."""
    def is_active(self, dedup_key: str) -> bool: ...
    def mark_active(self, dedup_key: str, ttl_sec: float) -> None: ...
    def clear(self, dedup_key: str) -> None: ...
    def get_position_first_seen(self, key: str) -> Optional[float]: ...
    def set_position_first_seen(self, key: str, ts: float) -> None: ...
    def clear_position(self, key: str) -> None: ...


class InMemoryViolationStore:
    """개발/단일 워커용. 프로덕션은 RedisViolationStore로 교체."""
    def __init__(self):
        self._active: dict[str, float] = {}      # dedup_key -> expiry
        self._first_seen: dict[str, float] = {}  # position_key -> first_seen_ts

    def is_active(self, dedup_key: str) -> bool:
        exp = self._active.get(dedup_key)
        if exp is None:
            return False
        if time.time() > exp:
            del self._active[dedup_key]
            return False
        return True

    def mark_active(self, dedup_key: str, ttl_sec: float) -> None:
        self._active[dedup_key] = time.time() + ttl_sec

    def clear(self, dedup_key: str) -> None:
        self._active.pop(dedup_key, None)

    def get_position_first_seen(self, key: str) -> Optional[float]:
        return self._first_seen.get(key)

    def set_position_first_seen(self, key: str, ts: float) -> None:
        self._first_seen.setdefault(key, ts)

    def clear_position(self, key: str) -> None:
        self._first_seen.pop(key, None)


# ------------------------------------------------------------
# 룰 엔진
# ------------------------------------------------------------
class RuleEngine:
    """
    리스크 지표 → 알림 생성.
    hysteresis: 위반이 시작되면 알림 1회, 해소되면 상태 클리어.
    같은 위반이 지속되는 동안엔 재알림 안 함 (스팸 방지).
    """

    # 위반 상태 TTL (이 시간 지나면 자동으로 "재알림 가능")
    REALERT_TTL = {
        RuleType.EXCESSIVE_RISK: 3600,
        RuleType.NO_STOP_LOSS: 1800,
        RuleType.LIQUIDATION_PROXIMITY: 600,   # 청산은 자주 상기
        RuleType.OVER_LEVERAGE: 3600,
        RuleType.REVENGE_PATTERN: 3600,
    }

    def __init__(self, store: ViolationStore):
        self._store = store

    def evaluate(
        self,
        user_id: int,
        metrics: RiskMetrics,
        config: UserRiskConfig,
        now: float,
    ) -> list[Alert]:
        alerts: list[Alert] = []
        pos_key = f"{user_id}:{metrics.symbol}"

        # 포지션 최초 관측 시각 기록 (손절 타임아웃 계산용)
        self._store.set_position_first_seen(pos_key, now)
        first_seen = self._store.get_position_first_seen(pos_key) or now
        position_age_min = (now - first_seen) / 60.0

        # --- Rule: 청산 임박 (가장 위급) ---
        if metrics.liq_distance_pct < config.min_liq_distance_pct:
            alerts.append(self._maybe_alert(
                user_id, RuleType.LIQUIDATION_PROXIMITY, Severity.CRITICAL, metrics,
                title="청산 임박",
                body=f"{metrics.symbol} 청산까지 {metrics.liq_distance_pct}% "
                     f"(안전선 {config.min_liq_distance_pct}%). "
                     f"청산 시 약 ${metrics.liq_loss_usd:,.0f} ({metrics.liq_loss_pct_of_equity}%) 손실 가능.",
                suggestion="증거금 추가 또는 포지션 축소로 청산가를 멀리 두세요.",
            ))

        # --- Rule: 손절 기준 실제 리스크 초과 ---
        if metrics.has_stop_loss and metrics.stop_risk_pct_of_equity is not None:
            if metrics.stop_risk_pct_of_equity > config.max_stop_risk_pct:
                alerts.append(self._maybe_alert(
                    user_id, RuleType.EXCESSIVE_RISK, Severity.WARNING, metrics,
                    title="리스크 한도 초과",
                    body=f"{metrics.symbol} 손절 시 손실 ${metrics.stop_risk_usd:,.0f} "
                         f"= 자본의 {metrics.stop_risk_pct_of_equity}% "
                         f"(한도 {config.max_stop_risk_pct}%).",
                    suggestion=f"포지션을 줄이거나 손절을 진입가에 더 가깝게 옮기세요.",
                ))

        # --- Rule: 손절 미설정 (유예시간 경과 후) ---
        if not metrics.has_stop_loss and position_age_min >= config.no_sl_grace_minutes:
            alerts.append(self._maybe_alert(
                user_id, RuleType.NO_STOP_LOSS, Severity.WARNING, metrics,
                title="손절 미설정",
                body=f"{metrics.symbol} 포지션을 {position_age_min:.0f}분째 손절 없이 보유 중. "
                     f"청산 시 약 ${metrics.liq_loss_usd:,.0f} ({metrics.liq_loss_pct_of_equity}%) 손실 가능.",
                suggestion="지금 손절 주문을 설정하세요.",
            ))

        # --- Rule: 실효 레버리지 과다 ---
        if metrics.effective_leverage > config.max_effective_leverage:
            alerts.append(self._maybe_alert(
                user_id, RuleType.OVER_LEVERAGE, Severity.WARNING, metrics,
                title="과도한 레버리지",
                body=f"{metrics.symbol} 실효 레버리지 {metrics.effective_leverage}x "
                     f"(권장 {config.max_effective_leverage}x 이하). "
                     f"명목가치 ${metrics.notional_usd:,.0f}.",
                suggestion="명목 노출을 줄여 변동성 충격을 완화하세요.",
            ))

        return [a for a in alerts if a is not None]

    def _maybe_alert(self, user_id, rule_type, severity, metrics, *, title, body, suggestion) -> Optional[Alert]:
        """hysteresis: 이미 활성 위반이면 None (재알림 안 함)"""
        dedup_key = f"{user_id}:{metrics.symbol}:{rule_type.value}"
        if self._store.is_active(dedup_key):
            return None
        self._store.mark_active(dedup_key, self.REALERT_TTL[rule_type])
        return Alert(
            rule_type=rule_type, severity=severity, symbol=metrics.symbol,
            title=title, body=body, suggestion=suggestion,
            metrics_snapshot={
                "stop_risk_pct": metrics.stop_risk_pct_of_equity,
                "liq_distance_pct": metrics.liq_distance_pct,
                "effective_leverage": metrics.effective_leverage,
                "notional_usd": metrics.notional_usd,
            },
            dedup_key=dedup_key,
        )

    def on_position_closed(self, user_id: int, symbol: str):
        """포지션이 닫히면 관련 상태 전부 클리어 (다음에 다시 열면 새 알림 가능)"""
        pos_key = f"{user_id}:{symbol}"
        self._store.clear_position(pos_key)
        for rt in RuleType:
            self._store.clear(f"{user_id}:{symbol}:{rt.value}")


# ============================================================
# 자가 검증
# ============================================================
if __name__ == "__main__":
    from app.core.risk_math import PositionInput, compute_risk, D

    store = InMemoryViolationStore()
    engine = RuleEngine(store)
    config = UserRiskConfig()
    equity = D("10000")

    # 손절 없는 고위험 포지션
    pos = PositionInput(
        symbol="BTCUSDT", side=Side.LONG,
        quantity=D("2"), entry_price=D("50000"), mark_price=D("50000"),
        leverage=D("20"), liquidation_price=D("48000"),
        unrealized_pnl=D("0"), stop_loss_price=None,
    )
    metrics = compute_risk(pos, equity)

    print("=== 룰 엔진 검증 ===\n")
    print(f"포지션: BTCUSDT 롱, 명목 ${metrics.notional_usd:,.0f}, "
          f"실효레버리지 {metrics.effective_leverage}x, 청산거리 {metrics.liq_distance_pct}%\n")

    # 1차 평가 (포지션 막 열림 - 손절 유예시간 내)
    now = time.time()
    alerts = engine.evaluate(1, metrics, config, now)
    print(f"1차 평가 (방금 오픈): {len(alerts)}개 알림")
    for a in alerts:
        print(f"  [{a.severity.value}] {a.title}: {a.body[:50]}...")

    # 2차 평가 (6분 후 - 손절 유예시간 경과)
    alerts2 = engine.evaluate(1, metrics, config, now + 360)
    print(f"\n2차 평가 (6분 후): {len(alerts2)}개 알림")
    for a in alerts2:
        print(f"  [{a.severity.value}] {a.title}")

    # 3차 평가 (즉시 재평가 - hysteresis로 중복 억제되어야 함)
    alerts3 = engine.evaluate(1, metrics, config, now + 361)
    print(f"\n3차 평가 (즉시 재평가): {len(alerts3)}개 알림 (hysteresis로 0이어야 정상)")

    print("\n✅ 룰 엔진 검증 완료")
