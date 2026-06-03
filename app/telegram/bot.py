"""
Bot entry point — wires python-telegram-bot, the worker loop, and the
scheduler into one async process.

Run: python3 -m app.telegram.bot   (requires TELEGRAM_BOT_TOKEN)

Three concurrent concerns share one event loop:
  1. PTB Application — handles /start linking + notebook button taps.
  2. worker.run_forever — polls connected users, emits warnings + prompts.
  3. APScheduler — daily summary + weekly mirror.
"""
from __future__ import annotations
import asyncio
import logging

from app.config import get_settings
from app.services.stoploss_tracker import InMemorySLStore, StopLossTracker
from app.telegram import handlers
from app.telegram.behavior import TradeBehaviorProvider
from app.telegram.notifier import PTBNotifier
from app.telegram.scheduler import build_scheduler
from app.telegram.worker import WorkerContext, run_forever

log = logging.getLogger("jarvis.telegram.bot")


async def _amain() -> None:
    settings = get_settings()
    if not settings.TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    from telegram.ext import Application

    application = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
    notifier = PTBNotifier(application.bot)
    handlers.register(application, notifier)

    # NOTE (scaling boundary): a process-local StopLossTracker + the shared
    # state singletons are correct for the single-process MVP. See worker.py.
    # Real behavior signals from the user's own persisted history (cached,
    # refreshed on an interval — not recomputed every poll).
    behavior = TradeBehaviorProvider()
    ctx = WorkerContext(
        notifier=notifier,
        sl_tracker=StopLossTracker(InMemorySLStore()),
        behavior_for=behavior.signals,
        behavior_provider=behavior,
    )

    scheduler = build_scheduler(notifier)

    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    scheduler.start()
    log.info("Jarvis Telegram bot is live")

    try:
        await run_forever(ctx)  # runs until cancelled
    finally:
        scheduler.shutdown(wait=False)
        await application.updater.stop()
        await application.stop()
        await application.shutdown()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
