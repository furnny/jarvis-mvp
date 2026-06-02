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

    # — Web / auth (Phase 2) —
    APP_BASE_URL: str = "http://localhost:8000"
    SESSION_SECRET: str = "dev-only-insecure-change-me"   # sign session cookies
    SESSION_TTL_HOURS: int = 24 * 30
    MAGIC_LINK_TTL_MIN: int = 15
    TELEGRAM_LINK_TTL_MIN: int = 30
    TELEGRAM_BOT_USERNAME: str = "JarvisRiskBot"
    EMAIL_SENDER: str = "console"   # console | resend | postmark | ses
    # If true, also allow keys that have futures-trade enabled (Binance couples
    # futures READ with futures TRADE — see app/web/key_validation.py).
    ALLOW_FUTURES_TRADE_KEYS: bool = True

    # — Misc —
    SQL_ECHO: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
