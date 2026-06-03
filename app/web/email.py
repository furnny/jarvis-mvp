"""
Email sender abstraction. Dev uses a console sender so the magic-link flow
works end-to-end locally with no provider. Prod swaps in Resend/Postmark/SES
behind the same interface.
"""
from __future__ import annotations
import abc
import logging

from app.config import get_settings
from app.i18n import t

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


class ResendEmailSender(EmailSender):
    """Sends the magic link via Resend (https://resend.com) over aiohttp.

    Slots behind EmailSender unchanged. The link is never logged at INFO to
    avoid leaking a live login token into logs.
    """

    ENDPOINT = "https://api.resend.com/emails"

    def __init__(self, api_key: str, from_addr: str):
        self._api_key = api_key
        self._from = from_addr

    async def send_magic_link(self, to_email: str, link: str) -> None:
        import aiohttp

        subject = t("email.magic_link.subject")
        body = t("email.magic_link.body", link=link)
        payload = {
            "from": self._from,
            "to": [to_email],
            "subject": subject,
            "text": body,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with aiohttp.ClientSession() as s:
            async with s.post(self.ENDPOINT, json=payload, headers=headers) as resp:
                if resp.status >= 400:
                    # never include the link/token in the error
                    raise RuntimeError(f"resend send failed: HTTP {resp.status}")
        log.info("magic-link emailed to %s via resend", to_email)


def get_email_sender() -> EmailSender:
    settings = get_settings()
    provider = settings.EMAIL_SENDER.lower()
    if provider == "console":
        return ConsoleEmailSender()
    if provider == "resend":
        if not settings.RESEND_API_KEY:
            raise RuntimeError("EMAIL_SENDER=resend but RESEND_API_KEY is not set")
        return ResendEmailSender(settings.RESEND_API_KEY, settings.EMAIL_FROM)
    # Postmark/SES implementations slot in here (same interface).
    raise NotImplementedError(f"email provider not implemented: {provider}")
