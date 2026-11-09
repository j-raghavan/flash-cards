# SPDX-License-Identifier: AGPL-3.0-only
"""Privacy policy, terms of use and imprint (F-LEGAL-1).

The software cannot know who runs an instance, so these pages are the operator's
to write. Each page is Markdown from the operator's legal directory; a page the
operator has not provided falls back to a template that says so plainly and
lists what the operator must fill in. Everything here is pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Generous for a legal page, small enough that a mistaken file (a log, a binary)
# is refused at startup instead of being served.
MAX_LEGAL_PAGE_BYTES = 200_000


class LegalPage(StrEnum):
    PRIVACY = "privacy"
    TERMS = "terms"
    IMPRINT = "imprint"

    @property
    def heading(self) -> str:
        return _TITLES[self]

    @property
    def file_name(self) -> str:
        return f"{self.value}.md"


_TITLES = {
    LegalPage.PRIVACY: "Privacy policy",
    LegalPage.TERMS: "Terms of use",
    LegalPage.IMPRINT: "Imprint",
}


class InvalidLegalPage(Exception):
    """An operator-supplied page is too large, not UTF-8 or empty."""


@dataclass(frozen=True, slots=True)
class LegalDocument:
    page: LegalPage
    markdown: str
    # True while the operator has not supplied the page: the web app says so.
    is_template: bool


def operator_document(page: LegalPage, data: bytes) -> LegalDocument:
    """An operator's page from its raw file bytes, validated."""
    if len(data) > MAX_LEGAL_PAGE_BYTES:
        raise InvalidLegalPage(f"{page.file_name} is larger than {MAX_LEGAL_PAGE_BYTES} bytes")
    try:
        text = data.decode("utf-8-sig")  # tolerate a byte-order mark from an editor
    except UnicodeDecodeError as exc:
        raise InvalidLegalPage(f"{page.file_name} is not UTF-8 text") from exc
    if not text.strip():
        raise InvalidLegalPage(f"{page.file_name} is empty")
    if "\x00" in text:
        raise InvalidLegalPage(f"{page.file_name} contains a NUL byte")
    return LegalDocument(page=page, markdown=text, is_template=False)


def template_document(page: LegalPage) -> LegalDocument:
    return LegalDocument(page=page, markdown=_TEMPLATES[page], is_template=True)


_NOTICE = (
    "> **The operator of this server has not filled in this page yet.** "
    "This is the template that ships with Flash-Cards. It is not legal advice and "
    "does not describe how this particular server is run."
)

_PRIVACY = f"""{_NOTICE}

## Who is responsible

*Operator: name, postal address and contact email of whoever runs this server.*

## What is stored

- Your account: email address, a password hash and your sign-in sessions,
  including the device and the time each was last used.
- What you create: decks, notes, cards, media, review history, goals and mock exams.
- Files you upload for AI card generation, and the drafts generated from them.

## AI features

Content you send to an AI feature is passed to the AI provider configured on this
server. Flash-Cards never uses your content to train models. The account page shows
which provider this server uses and links to its terms.

## How long data is kept

*Operator: how long accounts, uploads, backups and server logs are kept.*

## Your rights

You can download all your data and delete your account from the account page at any
time. *Operator: add how to reach you for any other request, and the supervisory
authority that applies to you.*

## Cookies

Flash-Cards sets one session cookie, needed to keep you signed in. It sets no
tracking or advertising cookies.
"""

_TERMS = f"""{_NOTICE}

## The service

*Operator: who provides this service, to whom, and whether it is free.*

## Your account

Keep your password safe. You are responsible for what happens under your account.

## Your content

You keep the rights to what you upload and create. Upload only material you are
allowed to use. Decks are private unless you share a link to them.

## Acceptable use

*Operator: what is not allowed, such as unlawful content or attempts to disrupt the
service, and how infringing content is reported and taken down.*

## Availability and liability

*Operator: availability, backups, limits of liability and the law that applies.*
"""

_IMPRINT = f"""{_NOTICE}

*Operator: in many countries a public website must name who is responsible for it.
Fill in what applies to you:*

- Name or company name
- Postal address
- Email address and, where required, a telephone number
- Register and registration number, VAT number or responsible person, where required
"""

_TEMPLATES = {
    LegalPage.PRIVACY: _PRIVACY,
    LegalPage.TERMS: _TERMS,
    LegalPage.IMPRINT: _IMPRINT,
}
