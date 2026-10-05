"""Gmail SMTP sender (SSL 465, app password). DRY_RUN guard lives here too (defence in depth)."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger(__name__)


class SendBlocked(Exception):
    pass


class GmailSender:
    def __init__(self, address: str | None, app_password: str | None, dry_run: bool = True,
                 host: str = "smtp.gmail.com", port: int = 465, smtp_factory=None):
        self.address, self.password, self.dry_run = address, app_password, dry_run
        self.host, self.port = host, port
        self._smtp_factory = smtp_factory  # injectable for tests
        self.sent_count = 0

    def build_message(self, to: str, subject: str, body: str, resume: Path | None) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.address or ""
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        if resume is not None and resume.exists():
            msg.add_attachment(resume.read_bytes(), maintype="application", subtype="pdf",
                               filename=f"{resume.stem}.pdf" if resume.name != "resume.pdf" else "Resume.pdf")
        return msg

    def send(self, to: str, subject: str, body: str, resume: Path | None = None) -> bool:
        """Returns True only if an email was really sent. In DRY_RUN logs WOULD SEND and returns False."""
        if self.dry_run:
            log.info("WOULD SEND email to=%s subject=%r attach=%s", to, subject, bool(resume and resume.exists()))
            return False
        if not (self.address and self.password):
            raise SendBlocked("GMAIL_ADDRESS / GMAIL_APP_PASSWORD not configured")
        if resume is None or not resume.exists():
            raise SendBlocked("resume PDF missing - refusing to send an application without it")
        msg = self.build_message(to, subject, body, resume)
        if self._smtp_factory:
            with self._smtp_factory() as s:
                s.send_message(msg)
        else:
            with smtplib.SMTP_SSL(self.host, self.port, context=ssl.create_default_context(), timeout=30) as s:
                s.login(self.address, self.password)
                s.send_message(msg)
        self.sent_count += 1
        log.info("email sent to=%s", to)
        return True
