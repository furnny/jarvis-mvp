"""
Notifier — the thin seam between Jarvis message logic and the Telegram wire.

Why an abstraction: the message-building logic (warnings/daily/weekly/notebook)
must be unit-testable WITHOUT a live bot token or network. Logic depends on
this Protocol; production wires `PTBNotifier` (python-telegram-bot), tests wire
a `FakeNotifier` that records calls.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Sequence


@dataclass
class Button:
    """One inline button. Exactly one of `callback` (callback_data payload) or
    `url` (open-link button) is set."""
    text: str
    callback: Optional[str] = None
    url: Optional[str] = None


class Notifier(Protocol):
    async def send_text(
        self, chat_id: int, text: str,
        *, buttons: Optional[Sequence[Sequence[Button]]] = None,
    ) -> None: ...

    async def send_photo(
        self, chat_id: int, png: bytes, *, caption: str = "",
        buttons: Optional[Sequence[Sequence[Button]]] = None,
    ) -> None: ...


@dataclass
class _SentText:
    chat_id: int
    text: str
    buttons: list[list[Button]] = field(default_factory=list)


@dataclass
class _SentPhoto:
    chat_id: int
    png: bytes
    caption: str
    buttons: list[list[Button]] = field(default_factory=list)


class FakeNotifier:
    """Records sends for tests/dev. Satisfies the Notifier Protocol."""

    def __init__(self) -> None:
        self.texts: list[_SentText] = []
        self.photos: list[_SentPhoto] = []

    async def send_text(self, chat_id, text, *, buttons=None):
        self.texts.append(_SentText(chat_id, text, [list(r) for r in (buttons or [])]))

    async def send_photo(self, chat_id, png, *, caption="", buttons=None):
        self.photos.append(_SentPhoto(chat_id, png, caption,
                                      [list(r) for r in (buttons or [])]))


class PTBNotifier:
    """Production Notifier backed by a python-telegram-bot Bot."""

    def __init__(self, bot: Any):
        self._bot = bot

    @staticmethod
    def _kb(buttons):
        if not buttons:
            return None
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        def mk(b):
            if b.url is not None:
                return InlineKeyboardButton(b.text, url=b.url)
            return InlineKeyboardButton(b.text, callback_data=b.callback)

        rows = [[mk(b) for b in row] for row in buttons]
        return InlineKeyboardMarkup(rows)

    async def send_text(self, chat_id, text, *, buttons=None):
        await self._bot.send_message(chat_id=chat_id, text=text,
                                     reply_markup=self._kb(buttons))

    async def send_photo(self, chat_id, png, *, caption="", buttons=None):
        import io
        await self._bot.send_photo(chat_id=chat_id, photo=io.BytesIO(png),
                                   caption=caption, reply_markup=self._kb(buttons))
