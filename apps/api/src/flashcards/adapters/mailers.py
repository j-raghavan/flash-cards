# SPDX-License-Identifier: AGPL-3.0-only
"""Mailer adapters (F-AUTH-2).

LogMailer is the default. In development it logs the full message, links
included, so the flow can be followed locally; elsewhere it logs only that a
message was not sent, because a logged reset link would be a working
credential in the log. SmtpMailer sends through a configured SMTP server.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Literal

import anyio

from flashcards.domain.mail import OutgoingEmail

logger = logging.getLogger(__name__)

SmtpSecurity = Literal["starttls", "ssl", "none"]


class LogMailer:
    def __init__(self, *, show_content: bool) -> None:
        self._show_content = show_content

    async def send(self, email: OutgoingEmail) -> None:
        if self._show_content:
            logger.warning(
                "DEV MAIL (not sent) to %s: %s\n%s", email.to, email.subject, email.body
            )
        else:
            logger.warning("No mail server configured; did not send: %s", email.subject)


class SmtpMailer:
    """Send through an SMTP server, always over a verified TLS connection.

    `security` is "starttls" (upgrade a plain connection, usually port 587),
    "ssl" (TLS from the start, usually port 465) or "none" (a local relay only).
    Certificates and host names are verified. Credentials are never sent without
    TLS: "none" with a username or password is refused at startup.
    """

    def __init__(
        self,
        *,
        host: str,
        port: int,
        sender: str,
        username: str | None = None,
        password: str | None = None,
        security: SmtpSecurity = "starttls",
        timeout_seconds: float = 30.0,
    ) -> None:
        if security == "none" and (username is not None or password is not None):
            raise ValueError("refusing to send SMTP credentials without TLS")
        self._host = host
        self._port = port
        self._sender = sender
        self._username = username
        self._password = password
        self._security = security
        self._timeout = timeout_seconds

    async def send(self, email: OutgoingEmail) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = email.to
        message["Subject"] = email.subject
        message.set_content(email.body)
        await anyio.to_thread.run_sync(self._deliver, message)

    def _deliver(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()  # verifies the certificate and host name
        smtp: smtplib.SMTP
        if self._security == "ssl":
            smtp = smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout, context=context)
        else:
            smtp = smtplib.SMTP(self._host, self._port, timeout=self._timeout)
        with smtp:
            if self._security == "starttls":
                smtp.starttls(context=context)
            if self._username is not None and self._password is not None:
                smtp.login(self._username, self._password)
            smtp.send_message(message)
