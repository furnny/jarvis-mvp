"""
Jarvis - Exchange Abstraction (거래소 추상화)
================================================

기존 MVP의 문제:
- 바이낸스에 직접 의존 → 다른 거래소 추가 시 전면 재작성
- 동기 호출 → 멀티유저에서 블로킹

수정:
- 추상 인터페이스 정의 → Binance, Bybit, OKX 등을 같은 방식으로 다룸
- 비동기 (async) → 수백 유저 동시 처리 가능
- Rate limit 인식 + 에러 격리
"""
from __future__ import annotations
import abc
import asyncio
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from app.core.risk_math import PositionInput, Side, D


@dataclass
class AccountSnapshot:
    """계좌 + 포지션 전체 스냅샷 (한 번의 조회로 다 가져옴)"""
    equity: Decimal              # 순자산 (wallet + unrealized PnL)
    wallet_balance: Decimal
    positions: list[PositionInput]
    fetched_at: float            # epoch seconds


class ExchangeError(Exception):
    """거래소 호출 실패 - 한 유저 에러가 전체를 멈추지 않게 격리"""
    def __init__(self, message: str, *, retryable: bool = True, auth_failed: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.auth_failed = auth_failed  # 키가 잘못됨 → 유저에게 재등록 요청


class ExchangeClient(abc.ABC):
    """모든 거래소 클라이언트가 구현해야 하는 인터페이스"""

    @abc.abstractmethod
    async def fetch_account_snapshot(self) -> AccountSnapshot:
        """계좌 + 열린 포지션 전체를 한 번에 조회"""
        ...

    @abc.abstractmethod
    async def validate_credentials(self) -> bool:
        """키가 유효하고 읽기 권한이 있는지 확인 (등록 시 호출)"""
        ...

    @abc.abstractmethod
    async def close(self) -> None:
        ...


# ------------------------------------------------------------
# 토큰 버킷 레이트 리미터 (거래소 IP 한도 보호)
# ------------------------------------------------------------
class RateLimiter:
    """
    바이낸스 선물은 IP당 분당 약 2400 weight 한도.
    포지션 조회는 weight가 있으니 토큰 버킷으로 호출 속도를 제어.
    """
    def __init__(self, rate_per_sec: float, burst: int):
        self._rate = rate_per_sec
        self._capacity = burst
        self._tokens = float(burst)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1.0) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._last = now
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            if self._tokens < cost:
                wait = (cost - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= cost


# ------------------------------------------------------------
# Binance 구현 (읽기 전용)
# ------------------------------------------------------------
class BinanceFuturesClient(ExchangeClient):
    """
    바이낸스 USDT-M 선물 읽기 전용 클라이언트.
    aiohttp로 비동기 직접 호출 (python-binance는 동기라 멀티유저에 부적합).
    """

    BASE_TESTNET = "https://testnet.binancefuture.com"
    BASE_PROD = "https://fapi.binance.com"

    # 거래소 전역 공유 레이트리미터 (IP 단위)
    _shared_limiter = RateLimiter(rate_per_sec=10.0, burst=20)

    def __init__(self, api_key: str, api_secret: str, *, testnet: bool = True):
        self._key = api_key
        self._secret = api_secret
        self._base = self.BASE_TESTNET if testnet else self.BASE_PROD
        self._session = None

    async def _ensure_session(self):
        if self._session is None:
            import aiohttp
            self._session = aiohttp.ClientSession(
                headers={"X-MBX-APIKEY": self._key},
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    def _sign(self, params: dict) -> str:
        import hmac, hashlib, urllib.parse
        query = urllib.parse.urlencode(params)
        sig = hmac.new(self._secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        return f"{query}&signature={sig}"

    async def _signed_get(self, path: str, params: dict | None = None) -> dict | list:
        await self._shared_limiter.acquire()
        session = await self._ensure_session()
        params = params or {}
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        url = f"{self._base}{path}?{self._sign(params)}"

        async with session.get(url) as resp:
            body = await resp.json()
            if resp.status == 401 or (isinstance(body, dict) and body.get("code") == -2015):
                raise ExchangeError("API 키 인증 실패", retryable=False, auth_failed=True)
            if resp.status == 429:
                raise ExchangeError("레이트리밋 초과", retryable=True)
            if resp.status >= 400:
                raise ExchangeError(f"거래소 오류 {resp.status}: {body}", retryable=resp.status >= 500)
            return body

    async def validate_credentials(self) -> bool:
        try:
            await self._signed_get("/fapi/v2/balance")
            return True
        except ExchangeError as e:
            if e.auth_failed:
                return False
            raise

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        # 한 번의 account 호출로 잔고+포지션 다 가져옴 (호출 수 최소화)
        account = await self._signed_get("/fapi/v2/account")

        wallet = D(account.get("totalWalletBalance", 0))
        unrealized = D(account.get("totalUnrealizedProfit", 0))
        equity = wallet + unrealized

        # 손절 주문 조회 (열린 포지션이 있는 심볼만)
        positions = []
        raw_positions = [p for p in account.get("positions", []) if D(p.get("positionAmt", 0)) != 0]

        # 심볼별 미체결 주문을 병렬 조회해서 손절 여부 파악
        sl_map = await self._fetch_stop_losses([p["symbol"] for p in raw_positions])

        for p in raw_positions:
            amt = D(p["positionAmt"])
            side = Side.LONG if amt > 0 else Side.SHORT
            positions.append(PositionInput(
                symbol=p["symbol"],
                side=side,
                quantity=abs(amt),
                entry_price=D(p["entryPrice"]),
                mark_price=D(p.get("markPrice") or p["entryPrice"]),
                leverage=D(p.get("leverage", 1)),
                liquidation_price=D(p.get("liquidationPrice", 0)),
                unrealized_pnl=D(p.get("unrealizedProfit", 0)),
                stop_loss_price=sl_map.get(p["symbol"]),
                isolated_margin=D(p["isolatedWallet"]) if p.get("isolated") else None,
            ))

        return AccountSnapshot(
            equity=equity, wallet_balance=wallet,
            positions=positions, fetched_at=time.time(),
        )

    async def _fetch_stop_losses(self, symbols: list[str]) -> dict[str, Optional[Decimal]]:
        """각 심볼의 미체결 손절 주문가를 조회"""
        result: dict[str, Optional[Decimal]] = {}
        if not symbols:
            return result

        async def one(sym: str):
            try:
                orders = await self._signed_get("/fapi/v1/openOrders", {"symbol": sym})
                for o in orders:
                    if o.get("type") in ("STOP_MARKET", "STOP", "TRAILING_STOP_MARKET"):
                        result[sym] = D(o.get("stopPrice", 0)) or None
                        return
                result[sym] = None
            except ExchangeError:
                result[sym] = None  # 조회 실패 시 "없음"으로 간주 (보수적)

        await asyncio.gather(*(one(s) for s in symbols))
        return result

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None


# 거래소 팩토리 - 나중에 거래소 추가 시 여기만 수정
def make_exchange_client(exchange: str, api_key: str, api_secret: str, *, testnet: bool) -> ExchangeClient:
    if exchange.lower() == "binance":
        return BinanceFuturesClient(api_key, api_secret, testnet=testnet)
    raise ValueError(f"지원하지 않는 거래소: {exchange}")
