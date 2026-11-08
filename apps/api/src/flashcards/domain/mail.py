# SPDX-License-Identifier: AGPL-3.0-only
"""Outgoing email (F-AUTH-2). Sent through the Mailer port."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str  # plain text
