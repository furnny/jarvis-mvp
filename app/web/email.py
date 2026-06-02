"""
Email sender abstraction. Dev uses a console sender so the magic-link flow
works end-to-end locally with no provider. Prod swaps in Resend/Postmark/SES
behind the same interface.
"""
from __future__ import annotations
import abc
import logging

from app.config import get_settings

log = logging.getLogger("jarvis.email")


class EmailSender(abc.ABC):
    @abc.abstractmethod
    async def send_magic_link(self, to_email: str, link: str) -> None: ...


class ConsoleEmailSender(EmailSender):
    """Logs the magic link instead of emailing — for local dev/tests."""

    last_link: str | None = None  # convenience for tests/dev

    async def send_magic_link(self, to_email: str, link: str) -> None:
        ConsoleEmailSender.last_link = link
        log.info("magic-link for %s: %s", to_email, link)
        print(f"[email:console] magic link for {to_email}: {link}")


def get_email_sender() -> EmailSender:
    provider = get_settings().EMAIL_SENDER.lower()
    if provider == "console":
        return ConsoleEmailSender()
    # Resend/Postmark/SES implementations slot in here (same interface).
    raise NotImplementedError(f"email provider not implemented: {provider}")
