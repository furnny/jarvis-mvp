"""Shared test setup: point the app at a fresh sqlite DB and reset caches."""
from __future__ import annotations
import base64
import os

import pytest


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    """Configure env for a throwaway sqlite DB, create schema, reset caches.

    Yields the async sessionmaker bound to that DB.
    """
    db_path = tmp_path / "app.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("JARVIS_MASTER_KEY", base64.b64encode(os.urandom(32)).decode())
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("EMAIL_SENDER", "console")
    monkeypatch.setenv("APP_BASE_URL", "http://testserver")

    # reset cached settings/engines so the new env takes effect
    from app.config import get_settings
    from app.db import base as dbbase
    get_settings.cache_clear()
    dbbase.get_engine.cache_clear()
    dbbase.get_sessionmaker.cache_clear()
    dbbase.get_sync_engine.cache_clear()
    dbbase.get_sync_sessionmaker.cache_clear()

    # create all tables
    import asyncio
    from app.db.base import get_engine, Base
    from app.db import models  # noqa: F401

    async def _init():
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.get_event_loop().run_until_complete(_init())

    yield dbbase.get_sessionmaker()

    # cleanup caches for the next test
    get_settings.cache_clear()
    dbbase.get_engine.cache_clear()
    dbbase.get_sessionmaker.cache_clear()
    dbbase.get_sync_engine.cache_clear()
    dbbase.get_sync_sessionmaker.cache_clear()
