"""
Async SQLAlchemy engine + session factory + declarative Base.

Usage:
    from app.db.base import get_sessionmaker
    async with get_sessionmaker()() as session:
        ...

The engine is built lazily from Settings.DATABASE_URL so tests can point it
at sqlite+aiosqlite without a running Postgres.
"""
from __future__ import annotations
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


def to_sync_url(url: str) -> str:
    """Derive a synchronous driver URL from an async one.

    The StopLossStore Protocol is sync (StopLossTracker calls it inline),
    so PostgresSLStore needs a sync engine while the rest of the app is async.
    """
    return url.replace("+asyncpg", "+psycopg2").replace("+aiosqlite", "")


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


@lru_cache
def get_engine(url: str | None = None) -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        url or settings.DATABASE_URL,
        echo=settings.SQL_ECHO,
        pool_pre_ping=True,
    )


@lru_cache
def get_sessionmaker(url: str | None = None) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        get_engine(url), expire_on_commit=False, class_=AsyncSession
    )


@lru_cache
def get_sync_engine(url: str | None = None) -> Engine:
    settings = get_settings()
    return create_engine(
        to_sync_url(url or settings.DATABASE_URL),
        echo=settings.SQL_ECHO,
        pool_pre_ping=True,
    )


@lru_cache
def get_sync_sessionmaker(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(get_sync_engine(url), expire_on_commit=False)
