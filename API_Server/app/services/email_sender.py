"""Email delivery abstraction — PLAN_01.

Only `ConsoleEmailSender` is wired for MVP. `SmtpEmailSender` is a stub
so Phase 2 can drop in real delivery without rewriting the DI layer, and
`NoopEmailSender` is a test-only double that records calls for assertion.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.config import Settings

logger = logging.getLogger("api_server.email")


class EmailSender(ABC):
    @abstractmethod
    async def send_verification_email(self, to: str, link: str) -> None: ...


class ConsoleEmailSender(EmailSender):
    async def send_verification_email(self, to: str, link: str) -> None:
        logger.info("VERIFY EMAIL to=%s link=%s", to, link)


class NoopEmailSender(EmailSender):
    """Test-only double. Holds sent messages for fixture assertions."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_verification_email(self, to: str, link: str) -> None:
        self.sent.append((to, link))


class SmtpEmailSender(EmailSender):
    """Placeholder. Phase 2 replaces this with real SMTP delivery."""

    async def send_verification_email(self, to: str, link: str) -> None:
        raise NotImplementedError(
            "SmtpEmailSender is a Phase 2 stub — set EMAIL_SENDER=console for MVP"
        )


def make_email_sender(settings: Settings) -> EmailSender:
    if settings.email_sender == "console":
        return ConsoleEmailSender()
    if settings.email_sender == "smtp":
        return SmtpEmailSender()
    raise ValueError(f"unknown EMAIL_SENDER: {settings.email_sender}")
