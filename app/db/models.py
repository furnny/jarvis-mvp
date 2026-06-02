"""
ORM models — Jarvis Phase 1 schema (DEV_SPEC.md §3, adapted for magic-link auth).

Auth note: magic-link (passwordless), so `users` has no password hash.
Telegram is a *delivery channel* (telegram_chat_id), not an auth method.
Magic-link tokens live in `magic_link_tokens`.

Portability: types chosen to work on both Postgres (prod/dev) and
sqlite+aiosqlite (tests). Enums stored as strings; lists as JSON.
"""
from __future__ import annotations
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# sqlite only autoincrements INTEGER PRIMARY KEY, not BIGINT. Use a portable
# PK type that is BIGINT on Postgres and INTEGER on sqlite (tests).
PK = BigInteger().with_variant(Integer, "sqlite")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    trading_style: Mapped[str | None] = mapped_column(String(32), nullable=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(
        BigInteger, unique=True, nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    credentials: Mapped[list["ApiCredential"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class MagicLinkToken(Base):
    __tablename__ = "magic_link_tokens"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    # nullable: a token can be issued for signup before the user row exists
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    email: Mapped[str] = mapped_column(String(320), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ApiCredential(Base):
    __tablename__ = "api_credentials"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    exchange: Mapped[str] = mapped_column(String(32), default="binance")
    ciphertext: Mapped[str] = mapped_column(Text)
    salt: Mapped[str] = mapped_column(String(64))
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    permissions: Mapped[list] = mapped_column(JSON, default=lambda: ["read"])
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="credentials")


class TradeRow(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(8))  # LONG / SHORT
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pnl_pct: Mapped[float] = mapped_column(Float)
    leverage: Mapped[float] = mapped_column(Float)
    size_vs_avg: Mapped[float] = mapped_column(Float)
    had_stop_loss: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("user_id", "symbol", "entry_time", name="uq_trade_natural"),
    )


class JournalEntryRow(Base):
    __tablename__ = "journal_entries"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    trade_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pnl_pct: Mapped[float] = mapped_column(Float)
    rationale: Mapped[str] = mapped_column(String(64))   # BaseRationale value or custom
    emotion: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    followed_stop_loss: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "trade_id", name="uq_journal_user_trade"),
    )


class SLObservationRow(Base):
    __tablename__ = "sl_observations"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    has_stop_loss: Mapped[bool] = mapped_column(Boolean)
    liq_distance_pct: Mapped[float] = mapped_column(Float)
    position_qty: Mapped[float] = mapped_column(Float)


class SLVerdictRow(Base):
    __tablename__ = "sl_verdicts"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ever_had_sl: Mapped[bool] = mapped_column(Boolean)
    coverage_ratio: Mapped[float] = mapped_column(Float)
    had_sl_when_risky: Mapped[bool] = mapped_column(Boolean)
    observations: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "symbol", "entry_time", name="uq_verdict_natural"
        ),
    )


class BaselineRow(Base):
    __tablename__ = "baselines"

    id: Mapped[int] = mapped_column(PK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    period_label: Mapped[str] = mapped_column(String(32))
    n_trades: Mapped[int] = mapped_column(Integer)
    win_rate: Mapped[float] = mapped_column(Float)
    avg_pnl_pct: Mapped[float] = mapped_column(Float)
    median_leverage: Mapped[float] = mapped_column(Float)
    median_size_vs_avg: Mapped[float] = mapped_column(Float)
    median_hold_minutes: Mapped[float] = mapped_column(Float)
    sl_coverage: Mapped[float] = mapped_column(Float)
    style_consistency: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "period_label", name="uq_baseline_user_period"),
    )
