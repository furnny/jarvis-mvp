"""
Postgres-backed stores behind the existing Protocols.

Two registers, by necessity:
  - PostgresSLStore  : SYNC, satisfies stoploss_tracker.StopLossStore exactly
                       (the tracker calls save/get inline). Named swap target:
                       InMemorySLStore → PostgresSLStore, no caller change.
  - Async entity stores (credentials / trades / journal / baselines) : called
    from async web + worker contexts.

All share app.db.base.Base.metadata. Tests run them on sqlite.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, Protocol, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.baseline import Baseline
from app.core.crypto import EncryptedCredential
from app.core.journal import JournalEntry
from app.core.trade_analyzer import Trade
from app.services.stoploss_tracker import StopLossVerdict
from app.db import models


# ════════════════════════════════════════════════════════════════════
# SL verdict store — SYNC (satisfies stoploss_tracker.StopLossStore)
# ════════════════════════════════════════════════════════════════════

def _minute(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


class PostgresSLStore:
    """Sync StopLossStore impl. Matches InMemorySLStore keying:
    (user_id, symbol, entry_time truncated to the minute)."""

    def __init__(self, session_factory):
        self._sf = session_factory

    def save_verdict(self, user_id: int, entry_time: datetime, v: StopLossVerdict) -> None:
        et = _minute(entry_time)
        with self._sf() as s:  # type: Session
            existing = s.execute(
                select(models.SLVerdictRow).where(
                    models.SLVerdictRow.user_id == user_id,
                    models.SLVerdictRow.symbol == v.symbol,
                    models.SLVerdictRow.entry_time == et,
                )
            ).scalar_one_or_none()
            if existing is None:
                existing = models.SLVerdictRow(
                    user_id=user_id, symbol=v.symbol, entry_time=et,
                    ever_had_sl=v.ever_had_sl, coverage_ratio=v.coverage_ratio,
                    had_sl_when_risky=v.had_sl_when_risky, observations=v.observations,
                )
                s.add(existing)
            else:
                existing.ever_had_sl = v.ever_had_sl
                existing.coverage_ratio = v.coverage_ratio
                existing.had_sl_when_risky = v.had_sl_when_risky
                existing.observations = v.observations
            s.commit()

    def get_verdict(
        self, user_id: int, symbol: str, entry_time: datetime
    ) -> Optional[StopLossVerdict]:
        et = _minute(entry_time)
        with self._sf() as s:  # type: Session
            row = s.execute(
                select(models.SLVerdictRow).where(
                    models.SLVerdictRow.user_id == user_id,
                    models.SLVerdictRow.symbol == symbol,
                    models.SLVerdictRow.entry_time == et,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return StopLossVerdict(
                symbol=row.symbol, ever_had_sl=row.ever_had_sl,
                coverage_ratio=row.coverage_ratio,
                had_sl_when_risky=row.had_sl_when_risky,
                observations=row.observations,
            )


# ════════════════════════════════════════════════════════════════════
# Async entity stores
# ════════════════════════════════════════════════════════════════════

class CredentialStore(Protocol):
    async def save(self, user_id: int, exchange: str, cred: EncryptedCredential,
                   permissions: list[str]) -> None: ...
    async def get(self, user_id: int, exchange: str) -> Optional[EncryptedCredential]: ...


class PostgresCredentialStore:
    def __init__(self, session_factory):
        self._sf = session_factory

    async def save(self, user_id: int, exchange: str, cred: EncryptedCredential,
                   permissions: list[str] | None = None) -> None:
        async with self._sf() as s:  # type: AsyncSession
            existing = (await s.execute(
                select(models.ApiCredential).where(
                    models.ApiCredential.user_id == user_id,
                    models.ApiCredential.exchange == exchange,
                )
            )).scalar_one_or_none()
            if existing is None:
                s.add(models.ApiCredential(
                    user_id=user_id, exchange=exchange,
                    ciphertext=cred.ciphertext, salt=cred.salt,
                    key_version=cred.version, permissions=permissions or ["read"],
                ))
            else:
                existing.ciphertext = cred.ciphertext
                existing.salt = cred.salt
                existing.key_version = cred.version
                existing.permissions = permissions or ["read"]
                existing.is_valid = True
            await s.commit()

    async def get(self, user_id: int, exchange: str) -> Optional[EncryptedCredential]:
        async with self._sf() as s:  # type: AsyncSession
            row = (await s.execute(
                select(models.ApiCredential).where(
                    models.ApiCredential.user_id == user_id,
                    models.ApiCredential.exchange == exchange,
                )
            )).scalar_one_or_none()
            if row is None:
                return None
            return EncryptedCredential(
                ciphertext=row.ciphertext, salt=row.salt, version=row.key_version
            )


class PostgresTradeStore:
    def __init__(self, session_factory):
        self._sf = session_factory

    async def save_trades(self, user_id: int, trades: Sequence[Trade]) -> int:
        """Upsert trades by (user_id, symbol, entry_time). Returns count written."""
        written = 0
        async with self._sf() as s:  # type: AsyncSession
            for t in trades:
                existing = (await s.execute(
                    select(models.TradeRow).where(
                        models.TradeRow.user_id == user_id,
                        models.TradeRow.symbol == t.symbol,
                        models.TradeRow.entry_time == t.entry_time,
                    )
                )).scalar_one_or_none()
                if existing is None:
                    s.add(models.TradeRow(
                        user_id=user_id, symbol=t.symbol, side=t.side,
                        entry_time=t.entry_time, exit_time=t.exit_time,
                        pnl_pct=t.pnl_pct, leverage=t.leverage,
                        size_vs_avg=t.size_vs_avg, had_stop_loss=t.had_stop_loss,
                    ))
                    written += 1
                else:
                    existing.exit_time = t.exit_time
                    existing.pnl_pct = t.pnl_pct
                    existing.leverage = t.leverage
                    existing.size_vs_avg = t.size_vs_avg
                    existing.had_stop_loss = t.had_stop_loss
            await s.commit()
        return written

    async def get_trades(self, user_id: int) -> list[Trade]:
        async with self._sf() as s:  # type: AsyncSession
            rows = (await s.execute(
                select(models.TradeRow)
                .where(models.TradeRow.user_id == user_id)
                .order_by(models.TradeRow.entry_time)
            )).scalars().all()
            return [
                Trade(
                    symbol=r.symbol, side=r.side,
                    entry_time=r.entry_time, exit_time=r.exit_time,
                    pnl_pct=r.pnl_pct, leverage=r.leverage,
                    size_vs_avg=r.size_vs_avg, had_stop_loss=r.had_stop_loss,
                )
                for r in rows
            ]


class PostgresJournalStore:
    def __init__(self, session_factory):
        self._sf = session_factory

    async def save_entry(self, user_id: int, entry: JournalEntry) -> None:
        emotion = entry.emotion.value if entry.emotion is not None else None
        async with self._sf() as s:  # type: AsyncSession
            existing = (await s.execute(
                select(models.JournalEntryRow).where(
                    models.JournalEntryRow.user_id == user_id,
                    models.JournalEntryRow.trade_id == entry.trade_id,
                )
            )).scalar_one_or_none()
            if existing is None:
                s.add(models.JournalEntryRow(
                    user_id=user_id, trade_id=entry.trade_id, symbol=entry.symbol,
                    closed_at=entry.closed_at, pnl_pct=entry.pnl_pct,
                    rationale=entry.rationale, emotion=emotion, note=entry.note,
                    followed_stop_loss=entry.followed_stop_loss,
                ))
            else:
                existing.rationale = entry.rationale
                existing.emotion = emotion
                existing.note = entry.note
                existing.followed_stop_loss = entry.followed_stop_loss
            await s.commit()

    async def get_entries(self, user_id: int) -> list[JournalEntry]:
        from app.core.journal import EmotionalState
        async with self._sf() as s:  # type: AsyncSession
            rows = (await s.execute(
                select(models.JournalEntryRow)
                .where(models.JournalEntryRow.user_id == user_id)
                .order_by(models.JournalEntryRow.closed_at)
            )).scalars().all()
            out = []
            for r in rows:
                emotion = EmotionalState(r.emotion) if r.emotion else None
                out.append(JournalEntry(
                    trade_id=r.trade_id, symbol=r.symbol, closed_at=r.closed_at,
                    pnl_pct=r.pnl_pct, rationale=r.rationale, emotion=emotion,
                    note=r.note, followed_stop_loss=r.followed_stop_loss,
                ))
            return out


class PostgresBaselineStore:
    def __init__(self, session_factory):
        self._sf = session_factory

    async def save_baseline(self, user_id: int, b: Baseline) -> None:
        async with self._sf() as s:  # type: AsyncSession
            existing = (await s.execute(
                select(models.BaselineRow).where(
                    models.BaselineRow.user_id == user_id,
                    models.BaselineRow.period_label == b.period_label,
                )
            )).scalar_one_or_none()
            fields = dict(
                n_trades=b.n_trades, win_rate=b.win_rate, avg_pnl_pct=b.avg_pnl_pct,
                median_leverage=b.median_leverage,
                median_size_vs_avg=b.median_size_vs_avg,
                median_hold_minutes=b.median_hold_minutes,
                sl_coverage=b.sl_coverage, style_consistency=b.style_consistency,
            )
            if existing is None:
                s.add(models.BaselineRow(
                    user_id=user_id, period_label=b.period_label, **fields
                ))
            else:
                for k, v in fields.items():
                    setattr(existing, k, v)
            await s.commit()

    async def get_baseline(self, user_id: int, period_label: str) -> Optional[Baseline]:
        async with self._sf() as s:  # type: AsyncSession
            r = (await s.execute(
                select(models.BaselineRow).where(
                    models.BaselineRow.user_id == user_id,
                    models.BaselineRow.period_label == period_label,
                )
            )).scalar_one_or_none()
            if r is None:
                return None
            return Baseline(
                period_label=r.period_label, n_trades=r.n_trades,
                win_rate=r.win_rate, avg_pnl_pct=r.avg_pnl_pct,
                median_leverage=r.median_leverage,
                median_size_vs_avg=r.median_size_vs_avg,
                median_hold_minutes=r.median_hold_minutes,
                sl_coverage=r.sl_coverage, style_consistency=r.style_consistency,
            )
