# SPDX-License-Identifier: AGPL-3.0-only
"""Sending email from a route without letting it affect the request (ADR 0010)."""

from __future__ import annotations

import logging

from fastapi import BackgroundTasks

from flashcards.domain.mail import OutgoingEmail
from flashcards.domain.ports import Mailer

logger = logging.getLogger(__name__)


def send_later(tasks: BackgroundTasks, mailer: Mailer, email: OutgoingEmail | None) -> None:
    """Send after the response (and after the commit), never failing the request.

    Sending later also keeps a request's timing the same whether or not an email
    goes out, so it says nothing about whether an account exists.
    """
    if email is not None:
        tasks.add_task(_send_quietly, mailer, email)


async def _send_quietly(mailer: Mailer, email: OutgoingEmail) -> None:
    try:
        await mailer.send(email)
    except Exception:  # a mail outage must not surface as a server error
        logger.exception("could not send email: %s", email.subject)
