"""
Telegram update handlers — /start linking, notebook-tag button taps, and
free-text note replies.

Channel discipline: handlers only LINK accounts and CAPTURE journal taps. They
never run analysis or render the dashboard — deep review lives on the web.
"""
from __future__ import annotations
import logging

from app.i18n import t
from app.telegram import notebook, state
from app.telegram.notifier import Notifier
from app.web.services import consume_telegram_link_code, user_id_for_chat
from app.db.persistence import get_journal_entry, save_journal_entry

log = logging.getLogger("jarvis.telegram")


async def handle_start(notifier: Notifier, chat_id: int, payload: str) -> None:
    """/start <code> — consume the web-issued link code, bind chat_id↔user."""
    code = (payload or "").strip()
    if code:
        ok = await consume_telegram_link_code(code, chat_id)
        if ok:
            await notifier.send_text(chat_id, t("telegram.start.welcome"))
            return
        # Maybe already linked from a prior code.
        if await user_id_for_chat(chat_id) is not None:
            await notifier.send_text(chat_id, t("telegram.start.already_linked"))
            return
        await notifier.send_text(chat_id, t("telegram.link.invalid"))
        return
    # bare /start
    if await user_id_for_chat(chat_id) is not None:
        await notifier.send_text(chat_id, t("telegram.start.already_linked"))
    else:
        await notifier.send_text(chat_id, t("telegram.link.invalid"))


async def handle_callback(notifier: Notifier, chat_id: int, data: str) -> None:
    """Notebook button tap → upsert a JournalEntry (idempotent, merges taps)."""
    parsed = notebook.parse_callback(data)
    if parsed is None:
        return
    tag = state.pending_tags.get(parsed.code)
    if tag is None:
        return  # context expired (e.g. worker restart) — silently ignore
    existing = await get_journal_entry(tag.user_id, tag.trade_id)
    entry = notebook.apply_to_entry(existing, tag, parsed)
    await save_journal_entry(tag.user_id, entry)
    await notifier.send_text(chat_id, notebook.confirmation(parsed))


async def handle_text_reply(notifier: Notifier, chat_id: int, text: str) -> None:
    """A plain reply becomes the most-recent open entry's note (optional)."""
    uid = await user_id_for_chat(chat_id)
    if uid is None:
        return
    code = state.pending_tags.by_chat_recent(user_id=uid)
    if code is None:
        return
    tag = state.pending_tags.get(code)
    if tag is None:
        return
    existing = await get_journal_entry(tag.user_id, tag.trade_id)
    if existing is None:
        from app.core.journal import JournalEntry
        existing = JournalEntry(
            trade_id=tag.trade_id, symbol=tag.symbol, closed_at=tag.closed_at,
            pnl_pct=tag.pnl_pct, rationale="", emotion=None,
            followed_stop_loss=tag.followed_stop_loss,
        )
    existing.note = text.strip()
    await save_journal_entry(tag.user_id, existing)
    await notifier.send_text(chat_id, t("telegram.notebook.note_saved"))


# ── python-telegram-bot adapter (registered in bot.py) ───────────────────────

def register(application, notifier: Notifier) -> None:
    """Wire PTB handlers to the framework-agnostic functions above."""
    from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, filters

    async def _start(update, _ctx):
        args = _ctx.args if hasattr(_ctx, "args") else []
        await handle_start(notifier, update.effective_chat.id,
                           args[0] if args else "")

    async def _callback(update, _ctx):
        q = update.callback_query
        await q.answer()
        await handle_callback(notifier, q.message.chat.id, q.data)

    async def _text(update, _ctx):
        await handle_text_reply(notifier, update.effective_chat.id,
                                update.message.text)

    application.add_handler(CommandHandler("start", _start))
    application.add_handler(CallbackQueryHandler(_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _text))
