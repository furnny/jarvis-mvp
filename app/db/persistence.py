"""
High-level persistence helpers tying the stores to the domain.

These are the functions the web layer (Phase 2) and worker (Phase 3) call:
  - register_credential / load_credential : encrypt-at-rest API keys
  - persist_trades / persist_baseline      : analysis outputs

Credentials are decrypted only here, in memory, never logged or stored plain.
"""
from __future__ import annotations
from typing import Optional, Sequence

from app.core.crypto import CredentialVault, MasterKeyProvider
from app.core.baseline import Baseline
from app.core.journal import JournalEntry
from app.core.trade_analyzer import Trade
from app.config import get_settings
from app.db.base import get_sessionmaker
from app.db.stores import (
    PostgresCredentialStore,
    PostgresTradeStore,
    PostgresJournalStore,
    PostgresBaselineStore,
)


def _vault() -> CredentialVault:
    return CredentialVault(MasterKeyProvider(get_settings().JARVIS_MASTER_KEY))


async def register_credential(
    user_id: int,
    api_key: str,
    api_secret: str,
    *,
    exchange: str = "binance",
    permissions: list[str] | None = None,
) -> None:
    """Encrypt and store a user's read-only API credentials."""
    cred = _vault().encrypt(api_key, api_secret)
    store = PostgresCredentialStore(get_sessionmaker())
    await store.save(user_id, exchange, cred, permissions or ["read"])


async def load_credential(
    user_id: int, *, exchange: str = "binance"
) -> Optional[tuple[str, str]]:
    """Return (api_key, api_secret) decrypted in memory, or None if absent."""
    store = PostgresCredentialStore(get_sessionmaker())
    enc = await store.get(user_id, exchange)
    if enc is None:
        return None
    return _vault().decrypt(enc)


async def persist_trades(user_id: int, trades: Sequence[Trade]) -> int:
    store = PostgresTradeStore(get_sessionmaker())
    return await store.save_trades(user_id, trades)


async def persist_baseline(user_id: int, baseline: Baseline) -> None:
    store = PostgresBaselineStore(get_sessionmaker())
    await store.save_baseline(user_id, baseline)


async def get_journal_entry(user_id: int, trade_id: str) -> Optional[JournalEntry]:
    """Load one journal entry by trade_id (for merging notebook taps)."""
    store = PostgresJournalStore(get_sessionmaker())
    for e in await store.get_entries(user_id):
        if e.trade_id == trade_id:
            return e
    return None


async def save_journal_entry(user_id: int, entry: JournalEntry) -> None:
    store = PostgresJournalStore(get_sessionmaker())
    await store.save_entry(user_id, entry)
