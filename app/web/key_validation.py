"""
API key permission validation — enforce READ-ONLY before a key touches the DB.

This is the hard gate behind the spec rule "read-only keys only; reject
withdraw/trade." It is HARD rejection, not a warning: register_key refuses to
store a credential whose permissions include any forbidden capability.

Binance reality (documented honestly, not hidden):
  Binance exposes key permissions via GET /sapi/v1/account/apiRestrictions:
    enableWithdrawals, enableInternalTransfer,
    enableSpotAndMarginTrading, enableMargin, enableFutures, ipRestrict
  The catastrophic vectors — moving money OUT — are `enableWithdrawals` and
  `enableInternalTransfer`. Those are ALWAYS rejected.
  Spot/margin TRADING is also rejected (a futures risk tool never needs it).
  `enableFutures` is special: on Binance, READING futures positions requires
  the futures permission, which inseparably also grants futures TRADE. So a
  usable futures key necessarily carries trade capability. We surface this and
  gate it behind ALLOW_FUTURES_TRADE_KEYS (default True) rather than pretend we
  can get futures-read without futures-trade. Mitigation: advisory-only design
  (Jarvis never sends orders) + IP whitelist guidance.
"""
from __future__ import annotations
import hashlib
import hmac
import time
import urllib.parse
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from app.config import get_settings

# Permissions that move money out or enable trading we never need → hard reject.
ALWAYS_FORBIDDEN = {
    "enableWithdrawals": "withdrawals",
    "enableInternalTransfer": "internal_transfer",
    "enableSpotAndMarginTrading": "spot_margin_trading",
    "enableMargin": "margin",
}

# Binance SAPI endpoint is on the spot host regardless of futures testnet.
_SAPI_BASE = "https://api.binance.com"
_RESTRICTIONS_PATH = "/sapi/v1/account/apiRestrictions"


@dataclass(frozen=True)
class KeyPermissions:
    enable_withdrawals: bool
    enable_internal_transfer: bool
    enable_spot_margin_trading: bool
    enable_margin: bool
    enable_futures: bool
    ip_restricted: bool

    @classmethod
    def from_binance(cls, payload: dict) -> "KeyPermissions":
        return cls(
            enable_withdrawals=bool(payload.get("enableWithdrawals", False)),
            enable_internal_transfer=bool(payload.get("enableInternalTransfer", False)),
            enable_spot_margin_trading=bool(payload.get("enableSpotAndMarginTrading", False)),
            enable_margin=bool(payload.get("enableMargin", False)),
            enable_futures=bool(payload.get("enableFutures", False)),
            ip_restricted=bool(payload.get("ipRestrict", False)),
        )

    def as_binance_dict(self) -> dict:
        return {
            "enableWithdrawals": self.enable_withdrawals,
            "enableInternalTransfer": self.enable_internal_transfer,
            "enableSpotAndMarginTrading": self.enable_spot_margin_trading,
            "enableMargin": self.enable_margin,
            "enableFutures": self.enable_futures,
            "ipRestrict": self.ip_restricted,
        }


class KeyValidationError(Exception):
    """Raised when a key is invalid/unreachable (distinct from forbidden perms)."""


def check_read_only(
    perms: KeyPermissions, *, allow_futures_trade: bool | None = None
) -> list[str]:
    """Pure policy check. Returns a list of violation slugs; empty == read-only OK.

    Kept side-effect-free so it is unit-testable without any network.
    """
    if allow_futures_trade is None:
        allow_futures_trade = get_settings().ALLOW_FUTURES_TRADE_KEYS

    raw = perms.as_binance_dict()
    violations = [slug for flag, slug in ALWAYS_FORBIDDEN.items() if raw.get(flag)]

    if perms.enable_futures and not allow_futures_trade:
        violations.append("futures_trade")

    return violations


# ── network fetch (injectable for tests) ────────────────────────────────────

def _sign(secret: str, params: dict) -> str:
    query = urllib.parse.urlencode(params)
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return f"{query}&signature={sig}"


async def fetch_key_permissions(api_key: str, api_secret: str) -> KeyPermissions:
    """Query Binance for this key's permissions. Never logs the secret."""
    import aiohttp

    params = {"timestamp": int(time.time() * 1000), "recvWindow": 5000}
    url = f"{_SAPI_BASE}{_RESTRICTIONS_PATH}?{_sign(api_secret, params)}"
    try:
        async with aiohttp.ClientSession(
            headers={"X-MBX-APIKEY": api_key},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as session:
            async with session.get(url) as resp:
                body = await resp.json()
                if resp.status >= 400:
                    code = body.get("code") if isinstance(body, dict) else None
                    raise KeyValidationError(f"key check failed (status={resp.status}, code={code})")
                return KeyPermissions.from_binance(body)
    except KeyValidationError:
        raise
    except Exception as e:  # network/parse — opaque, never include the secret
        raise KeyValidationError(f"could not reach exchange to verify key: {type(e).__name__}")


# A seam tests/dev can override (e.g. testnet keys can't hit SAPI).
PermissionFetcher = Callable[[str, str], Awaitable[KeyPermissions]]
_fetcher: Optional[PermissionFetcher] = None


def set_permission_fetcher(fn: Optional[PermissionFetcher]) -> None:
    global _fetcher
    _fetcher = fn


async def get_key_permissions(api_key: str, api_secret: str) -> KeyPermissions:
    return await (_fetcher or fetch_key_permissions)(api_key, api_secret)
