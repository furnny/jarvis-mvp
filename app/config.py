"""
Jarvis — application settings (Phase 1+).

All secrets come from the environment (never hardcoded). See .env.example.
"""
from __future__ import annotations
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # — Database —
    # Async SQLAlchemy URL. Dev: local Postgres. Tests override with sqlite+aiosqlite.
    DATABASE_URL: str = "postgresql+asyncpg://jarvis:jarvis@localhost:5432/jarvis"

    # — Crypto —
    # 32-byte base64 master key for envelope encryption (crypto.MasterKeyProvider).
    # Generate: python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"
    JARVIS_MASTER_KEY: str | None = None

    # — Exchange —
    EXCHANGE: str = "binance"
    BINANCE_TESTNET: bool = True

    # — i18n —
    DEFAULT_LOCALE: str = "ko"

    # — Misc —
    SQL_ECHO: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
