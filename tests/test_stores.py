"""
Round-trip tests for the Phase 1 stores.

Runs on sqlite (async via aiosqlite, sync via plain sqlite) so no Postgres
is required. The same store classes run against Postgres in dev/prod — only
the URL changes.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db import models  # noqa: F401  (register tables on Base.metadata)
from app.db.stores import (
    PostgresSLStore,
    PostgresCredentialStore,
    PostgresTradeStore,
    PostgresJournalStore,
    PostgresBaselineStore,
)
from app.core.crypto import EncryptedCredential
from app.core.trade_analyzer import Trade
from app.core.journal import JournalEntry, BaseRationale, EmotionalState
from app.core.baseline import Baseline
from app.services.stoploss_tracker import StopLossVerdict


UTC = timezone.utc


# ── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def sync_sf(tmp_path):
    """Sync sqlite session factory with tables created."""
    url = f"sqlite:///{tmp_path}/sync.db"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def async_sf(tmp_path):
    """Async sqlite session factory with tables created."""
    url = f"sqlite+aiosqlite:///{tmp_path}/async.db"
    engine = create_async_engine(url)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_init())
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _seed_user(async_sf):
    """Create a user row so FK references resolve."""
    async def _go():
        async with async_sf() as s:
            s.add(models.User(id=1, email="t@example.com", trading_style="swing"))
            await s.commit()
    asyncio.get_event_loop().run_until_complete(_go())


# ── SL verdict store (sync, satisfies StopLossStore) ────────────────────────

def test_sl_store_roundtrip(sync_sf):
    store = PostgresSLStore(sync_sf)
    entry = datetime(2026, 1, 5, 9, 30, 45, tzinfo=UTC)
    v = StopLossVerdict("BTCUSDT", ever_had_sl=True, coverage_ratio=0.8,
                        had_sl_when_risky=True, observations=6)
    store.save_verdict(1, entry, v)

    # truncates to minute — querying with different seconds still matches
    got = store.get_verdict(1, "BTCUSDT", entry.replace(second=10))
    assert got is not None
    assert got.had_sl_when_risky is True
    assert got.coverage_ratio == 0.8
    assert got.observations == 6

    assert store.get_verdict(1, "ETHUSDT", entry) is None

    # upsert overwrites
    store.save_verdict(1, entry, StopLossVerdict("BTCUSDT", False, 0.1, False, 9))
    got2 = store.get_verdict(1, "BTCUSDT", entry)
    assert got2.had_sl_when_risky is False
    assert got2.observations == 9


def _drive_tracker(store):
    """Run a fixed observe/reconcile sequence through a store; return the verdict."""
    from datetime import timedelta
    from decimal import Decimal
    from app.services.stoploss_tracker import StopLossTracker, StopLossObservation
    tracker = StopLossTracker(store)
    base = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    # 6 obs, SL appears from the 3rd → coverage 4/6, risky-window behaviour
    for i in range(6):
        tracker.observe(StopLossObservation(
            user_id=7, symbol="ETHUSDT", observed_at=base + timedelta(minutes=i * 5),
            has_stop_loss=(i >= 2),
            liq_distance_pct=10.0 if i >= 2 else 6.0,
            position_qty=Decimal("2.0"),
        ))
    tracker.reconcile(7, live_symbols=set())
    return store.get_verdict(7, "ETHUSDT", base), base


def test_sl_store_equivalent_to_inmemory(sync_sf):
    """PostgresSLStore must produce the SAME verdict as InMemorySLStore for the
    same caller sequence — proving a true drop-in (no StopLossTracker changes)."""
    from app.services.stoploss_tracker import InMemorySLStore
    mem_v, _ = _drive_tracker(InMemorySLStore())
    pg_v, _ = _drive_tracker(PostgresSLStore(sync_sf))
    assert mem_v is not None and pg_v is not None
    assert (mem_v.ever_had_sl, mem_v.coverage_ratio, mem_v.had_sl_when_risky,
            mem_v.observations) == (pg_v.ever_had_sl, pg_v.coverage_ratio,
                                    pg_v.had_sl_when_risky, pg_v.observations)


def test_credential_encrypt_store_decrypt_roundtrip(async_sf, monkeypatch):
    """End-to-end Phase 1 credential path: encrypt → store → load → decrypt,
    with no plaintext persisted."""
    import base64, os
    from app.config import get_settings
    from app.core.crypto import CredentialVault, MasterKeyProvider
    from app.db.stores import PostgresCredentialStore

    _seed_user(async_sf)
    master = base64.b64encode(os.urandom(32)).decode()
    vault = CredentialVault(MasterKeyProvider(master))
    store = PostgresCredentialStore(async_sf)

    async def _go():
        enc = vault.encrypt("APIKEY-123", "SECRET-xyz")
        await store.save(1, "binance", enc, ["read"])
        got = await store.get(1, "binance")
        # ciphertext stored, plaintext absent
        assert "APIKEY-123" not in got.ciphertext
        assert "SECRET-xyz" not in got.ciphertext
        # decrypts back to the originals
        k, s = vault.decrypt(got)
        assert (k, s) == ("APIKEY-123", "SECRET-xyz")

    asyncio.get_event_loop().run_until_complete(_go())


def test_sl_store_satisfies_protocol(sync_sf):
    """PostgresSLStore must be a drop-in for stoploss_tracker.StopLossStore:
    drive the tracker end-to-end through it and read the verdict back."""
    from datetime import timedelta
    from decimal import Decimal
    from app.services.stoploss_tracker import StopLossTracker, StopLossObservation

    store = PostgresSLStore(sync_sf)
    tracker = StopLossTracker(store)
    base = datetime(2026, 2, 1, 10, 0, tzinfo=UTC)
    for i in range(5):
        tracker.observe(StopLossObservation(
            user_id=1, symbol="BTCUSDT", observed_at=base + timedelta(minutes=i * 5),
            has_stop_loss=False, liq_distance_pct=5.0, position_qty=Decimal("1.0"),
        ))
    tracker.reconcile(1, live_symbols=set())  # close → finalize → save_verdict
    v = store.get_verdict(1, "BTCUSDT", base)
    assert v is not None and v.observations == 5


# ── async entity stores ─────────────────────────────────────────────────────

def test_credential_store_roundtrip(async_sf):
    _seed_user(async_sf)
    store = PostgresCredentialStore(async_sf)
    cred = EncryptedCredential(ciphertext="abc", salt="xyz", version=1)

    async def _go():
        await store.save(1, "binance", cred, ["read"])
        got = await store.get(1, "binance")
        assert got is not None
        assert (got.ciphertext, got.salt, got.version) == ("abc", "xyz", 1)
        # update
        await store.save(1, "binance", EncryptedCredential("def", "uvw", 2), ["read"])
        got2 = await store.get(1, "binance")
        assert got2.ciphertext == "def" and got2.version == 2
        assert await store.get(1, "bybit") is None

    asyncio.get_event_loop().run_until_complete(_go())


def test_trade_store_roundtrip(async_sf):
    _seed_user(async_sf)
    store = PostgresTradeStore(async_sf)
    t0 = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    trades = [
        Trade("BTCUSDT", "LONG", t0, t0 + timedelta(hours=1),
              pnl_pct=1.2, leverage=5, size_vs_avg=1.0, had_stop_loss=True),
        Trade("ETHUSDT", "SHORT", t0 + timedelta(hours=2), t0 + timedelta(hours=3),
              pnl_pct=-0.8, leverage=10, size_vs_avg=2.1, had_stop_loss=False),
    ]

    async def _go():
        written = await store.save_trades(1, trades)
        assert written == 2
        got = await store.get_trades(1)
        assert len(got) == 2
        assert got[0].symbol == "BTCUSDT" and got[0].had_stop_loss is True
        assert got[1].side == "SHORT"
        # idempotent upsert: no new rows
        written2 = await store.save_trades(1, trades)
        assert written2 == 0
        assert len(await store.get_trades(1)) == 2

    asyncio.get_event_loop().run_until_complete(_go())


def test_journal_store_roundtrip(async_sf):
    _seed_user(async_sf)
    store = PostgresJournalStore(async_sf)
    e = JournalEntry(
        trade_id="t-1", symbol="ETHUSDT",
        closed_at=datetime(2026, 1, 5, 12, 0, tzinfo=UTC),
        pnl_pct=-4.2, rationale=BaseRationale.REVENGE.value,
        emotion=EmotionalState.REVENGE, note="too fast",
        followed_stop_loss=False,
    )

    async def _go():
        await store.save_entry(1, e)
        got = await store.get_entries(1)
        assert len(got) == 1
        assert got[0].rationale == BaseRationale.REVENGE.value
        assert got[0].emotion == EmotionalState.REVENGE
        assert got[0].followed_stop_loss is False
        assert got[0].note == "too fast"

    asyncio.get_event_loop().run_until_complete(_go())


def test_baseline_store_roundtrip(async_sf):
    _seed_user(async_sf)
    store = PostgresBaselineStore(async_sf)
    b = Baseline(
        period_label="2026-01", n_trades=40, win_rate=58.0, avg_pnl_pct=0.3,
        median_leverage=8.0, median_size_vs_avg=1.0, median_hold_minutes=120.0,
        sl_coverage=0.77, style_consistency=0.8,
    )

    async def _go():
        await store.save_baseline(1, b)
        got = await store.get_baseline(1, "2026-01")
        assert got is not None
        assert got.win_rate == 58.0 and got.sl_coverage == 0.77
        # upsert
        b2 = Baseline("2026-01", 45, 40.0, -0.5, 18.0, 1.0, 90.0, 0.27, 0.6)
        await store.save_baseline(1, b2)
        got2 = await store.get_baseline(1, "2026-01")
        assert got2.win_rate == 40.0 and got2.median_leverage == 18.0
        assert await store.get_baseline(1, "2025-12") is None

    asyncio.get_event_loop().run_until_complete(_go())
