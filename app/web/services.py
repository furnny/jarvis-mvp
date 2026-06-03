"""
Web services — DB-touching auth + telegram-linking logic (endpoints stay thin).

Magic-link: tokens are stored only as sha256 hashes; the raw token lives only
in the emailed URL. One-time use, expiring.
Telegram link codes: same shape; the bot consumes them on /start <code>.
"""
from __future__ import annotations
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db.base import get_sessionmaker
from app.db import models


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ── magic link ───────────────────────────────────────────────────────────────

async def create_magic_link_token(email: str) -> str:
    """Create a one-time login token for `email`. Returns the RAW token (goes in
    the emailed link). Only its hash is persisted."""
    raw = secrets.token_urlsafe(32)
    ttl = get_settings().MAGIC_LINK_TTL_MIN
    async with get_sessionmaker()() as s:
        s.add(models.MagicLinkToken(
            email=email.lower().strip(),
            token_hash=_hash(raw),
            expires_at=_now() + timedelta(minutes=ttl),
        ))
        await s.commit()
    return raw


async def consume_magic_link_token(raw: str) -> int | None:
    """Validate a magic-link token. On success returns the user id (creating the
    user on first login) and marks the token used. None if invalid/expired/used."""
    th = _hash(raw)
    async with get_sessionmaker()() as s:
        tok = (await s.execute(
            select(models.MagicLinkToken).where(models.MagicLinkToken.token_hash == th)
        )).scalar_one_or_none()
        if tok is None or tok.used_at is not None:
            return None
        expires_at = tok.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < _now():
            return None

        # find or create the user
        user = (await s.execute(
            select(models.User).where(models.User.email == tok.email)
        )).scalar_one_or_none()
        if user is None:
            user = models.User(email=tok.email)
            s.add(user)
            await s.flush()
        tok.used_at = _now()
        tok.user_id = user.id
        await s.commit()
        return user.id


# ── telegram link code ───────────────────────────────────────────────────────

async def create_telegram_link_code(user_id: int) -> str:
    raw = secrets.token_urlsafe(9)  # short, fits a /start payload
    ttl = get_settings().TELEGRAM_LINK_TTL_MIN
    async with get_sessionmaker()() as s:
        s.add(models.TelegramLinkCode(
            user_id=user_id, code=raw,
            expires_at=_now() + timedelta(minutes=ttl),
        ))
        await s.commit()
    return raw


async def consume_telegram_link_code(code: str, chat_id: int) -> bool:
    """Called by the bot on /start <code>. Links chat_id to the user. Returns
    True on success. One-time use, expiring."""
    async with get_sessionmaker()() as s:
        row = (await s.execute(
            select(models.TelegramLinkCode).where(models.TelegramLinkCode.code == code)
        )).scalar_one_or_none()
        if row is None or row.used_at is not None:
            return False
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < _now():
            return False
        user = (await s.execute(
            select(models.User).where(models.User.id == row.user_id)
        )).scalar_one_or_none()
        if user is None:
            return False
        user.telegram_chat_id = chat_id
        row.used_at = _now()
        await s.commit()
        return True


async def user_id_for_chat(chat_id: int) -> int | None:
    """Resolve a linked user id from a Telegram chat id (bot-side lookups)."""
    async with get_sessionmaker()() as s:
        user = (await s.execute(
            select(models.User).where(models.User.telegram_chat_id == chat_id)
        )).scalar_one_or_none()
        return user.id if user else None


async def set_trading_style(user_id: int, style: str) -> None:
    async with get_sessionmaker()() as s:
        user = (await s.execute(
            select(models.User).where(models.User.id == user_id)
        )).scalar_one_or_none()
        if user is not None:
            user.trading_style = style
            await s.commit()
