"""
Scheduled Telegram jobs (TELEGRAM_SPEC §1.3 daily, §1.4 weekly).

These are the SCHEDULED messages (vs. the event-driven warning/notebook from
worker.py). Both pull exclusively from viz_contract — same data the web renders,
drawn as static images for the channel.

APScheduler (AsyncIOScheduler) fires per-user dispatch at the configured local
times. Users with insufficient data (MIN_RELIABLE_SAMPLE) are skipped — Jarvis
withholds judgment rather than showing a thin, misleading stat.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

from app.config import get_settings
from app.core.baseline import TradingStyle, build_baseline
from app.core.journal import MIN_RELIABLE_SAMPLE, MistakeNotebook
from app.core.trade_analyzer import TradeAnalyzer
from app.services import viz_contract
from app.telegram import daily as daily_mod
from app.telegram import weekly as weekly_mod
from app.telegram.notifier import Notifier

log = logging.getLogger("jarvis.scheduler")


async def _load(user_id: int):
    from app.db.base import get_sessionmaker
    from app.db.stores import PostgresTradeStore, PostgresJournalStore
    sm = get_sessionmaker()
    trades = await PostgresTradeStore(sm).get_trades(user_id)
    entries = await PostgresJournalStore(sm).get_entries(user_id)
    return trades, entries


async def _chat_id(user_id: int):
    from app.db.base import get_sessionmaker
    from app.db import models
    from sqlalchemy import select
    async with get_sessionmaker()() as s:
        u = (await s.execute(
            select(models.User).where(models.User.id == user_id)
        )).scalar_one_or_none()
        return (u.telegram_chat_id, u.trading_style) if u else (None, None)


async def send_daily(notifier: Notifier, user_id: int, *, lang: str | None = None) -> bool:
    """Build + send today's review. Returns True if a message was sent."""
    chat_id, _ = await _chat_id(user_id)
    if chat_id is None:
        return False
    trades, entries = await _load(user_id)

    # today's slice (UTC day) — data only from viz_contract transforms
    today = datetime.now(timezone.utc).date()
    todays = [t for t in trades if t.entry_time.date() == today]
    todays_entries = [e for e in entries if e.closed_at.date() == today]

    situation = viz_contract.to_situation_breakdown(TradeAnalyzer(todays))
    timeseries = viz_contract.to_timeseries(todays, start_equity=100.0, weeks=1)
    split = MistakeNotebook(todays_entries).discipline_split()

    png, caption = daily_mod.build_daily(
        situation, timeseries, split,
        date_label=today.isoformat(), lang=lang,
    )
    if png is None:
        await notifier.send_text(chat_id, caption)
    else:
        await notifier.send_photo(chat_id, png, caption=caption)
    return True


async def send_weekly(notifier: Notifier, user_id: int, *, lang: str | None = None) -> bool:
    """Build + send this week's mirror (prev week vs this week). True if sent."""
    chat_id, style_str = await _chat_id(user_id)
    if chat_id is None:
        return False
    trades, _ = await _load(user_id)
    if len(trades) < MIN_RELIABLE_SAMPLE:
        return False  # withhold judgment on a thin sample

    sorted_t = sorted(trades, key=lambda t: t.entry_time)
    now = datetime.now(timezone.utc)
    this_week = [t for t in sorted_t if (now - t.entry_time).days < 7]
    last_week = [t for t in sorted_t if 7 <= (now - t.entry_time).days < 14]
    if len(this_week) < 3 or len(last_week) < 3:
        return False

    style = TradingStyle(style_str) if style_str else TradingStyle.SWING
    prev = build_baseline(last_week, style, "지난주")
    curr = build_baseline(this_week, style, "이번주")
    if prev is None or curr is None:
        return False

    comparison = viz_contract.to_journal_comparison(prev, curr, style)
    web_url = f"{get_settings().APP_BASE_URL}/journal"
    png, caption, buttons = weekly_mod.build_weekly(
        comparison, prev, curr, web_url=web_url, lang=lang,
    )
    await notifier.send_photo(chat_id, png, caption=caption, buttons=buttons)
    return True


async def _run_for_all(coro_fn, notifier: Notifier) -> None:
    from app.db.base import get_sessionmaker
    from app.db import models
    from sqlalchemy import select
    async with get_sessionmaker()() as s:
        ids = (await s.execute(
            select(models.User.id).where(models.User.telegram_chat_id.isnot(None))
        )).scalars().all()
    for uid in ids:
        try:
            await coro_fn(notifier, uid)
        except Exception:
            log.exception("scheduled job failed for user %s", uid)


def build_scheduler(notifier: Notifier):
    """Create (but don't start) the AsyncIOScheduler with daily + weekly jobs."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    s = get_settings()
    sched = AsyncIOScheduler(timezone=s.SCHEDULER_TZ)
    sched.add_job(
        _run_for_all, CronTrigger(hour=s.DAILY_SUMMARY_HOUR, minute=0),
        args=[send_daily, notifier], id="daily_summary", replace_existing=True,
    )
    sched.add_job(
        _run_for_all,
        CronTrigger(day_of_week=s.WEEKLY_MIRROR_DOW, hour=s.WEEKLY_MIRROR_HOUR, minute=0),
        args=[send_weekly, notifier], id="weekly_mirror", replace_existing=True,
    )
    return sched
