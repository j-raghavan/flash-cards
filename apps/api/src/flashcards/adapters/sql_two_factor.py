# SPDX-License-Identifier: AGPL-3.0-only
"""Postgres repository for second factors, recovery codes and challenges (F-AUTH-3).

Every "use once" operation (a TOTP step, a recovery code, a challenge, a passkey
counter) is one conditional UPDATE ... RETURNING, so of two concurrent requests
presenting the same thing exactly one succeeds.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from flashcards.adapters.db import (
    PasskeyRow,
    RecoveryCodeRow,
    TotpCredentialRow,
    TwoFactorChallengeRow,
    challenge_from_row,
    challenge_to_row,
    passkey_from_row,
    passkey_to_row,
    totp_from_row,
)
from flashcards.domain.two_factor import Challenge, Passkey, RecoveryCode, TotpCredential


class SqlTwoFactorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_totp(self, user_id: str) -> TotpCredential | None:
        row = await self._session.get(TotpCredentialRow, user_id, populate_existing=True)
        return totp_from_row(row) if row is not None else None

    async def save_totp(self, credential: TotpCredential) -> None:
        values = {
            "secret_ciphertext": credential.secret_ciphertext,
            "created_at": credential.created_at,
            "confirmed_at": credential.confirmed_at,
            "last_used_step": credential.last_used_step,
        }
        await self._session.execute(
            pg_insert(TotpCredentialRow)
            .values(user_id=credential.user_id, **values)
            .on_conflict_do_update(index_elements=[TotpCredentialRow.user_id], set_=values)
        )

    async def confirm_totp(self, user_id: str, step: int, now: datetime) -> bool:
        confirmed = await self._session.scalar(
            update(TotpCredentialRow)
            .where(TotpCredentialRow.user_id == user_id, TotpCredentialRow.confirmed_at.is_(None))
            .values(confirmed_at=now, last_used_step=step)
            .returning(TotpCredentialRow.user_id)
        )
        return confirmed is not None

    async def use_totp_step(self, user_id: str, step: int) -> bool:
        used = await self._session.scalar(
            update(TotpCredentialRow)
            .where(
                TotpCredentialRow.user_id == user_id,
                TotpCredentialRow.confirmed_at.is_not(None),
                or_(
                    TotpCredentialRow.last_used_step.is_(None),
                    TotpCredentialRow.last_used_step < step,
                ),
            )
            .values(last_used_step=step)
            .returning(TotpCredentialRow.user_id)
        )
        return used is not None

    async def delete_totp(self, user_id: str) -> bool:
        deleted = await self._session.scalar(
            delete(TotpCredentialRow)
            .where(TotpCredentialRow.user_id == user_id)
            .returning(TotpCredentialRow.user_id)
        )
        return deleted is not None

    async def list_totp(self) -> list[TotpCredential]:
        rows = await self._session.scalars(select(TotpCredentialRow))
        return [totp_from_row(row) for row in rows]

    async def set_totp_secret(self, user_id: str, secret_ciphertext: str) -> None:
        await self._session.execute(
            update(TotpCredentialRow)
            .where(TotpCredentialRow.user_id == user_id)
            .values(secret_ciphertext=secret_ciphertext)
        )

    async def replace_recovery_codes(self, user_id: str, codes: Sequence[RecoveryCode]) -> None:
        await self.delete_recovery_codes(user_id)
        self._session.add_all(
            [
                RecoveryCodeRow(
                    id=code.id,
                    user_id=code.user_id,
                    code_hash=code.code_hash,
                    created_at=code.created_at,
                    used_at=code.used_at,
                )
                for code in codes
            ]
        )
        await self._session.flush()

    async def use_recovery_code(self, user_id: str, code_hash: str, now: datetime) -> bool:
        used = await self._session.scalar(
            update(RecoveryCodeRow)
            .where(
                RecoveryCodeRow.user_id == user_id,
                RecoveryCodeRow.code_hash == code_hash,
                RecoveryCodeRow.used_at.is_(None),
            )
            .values(used_at=now)
            .returning(RecoveryCodeRow.id)
        )
        return used is not None

    async def count_recovery_codes(self, user_id: str) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(RecoveryCodeRow)
            .where(RecoveryCodeRow.user_id == user_id, RecoveryCodeRow.used_at.is_(None))
        )
        return count or 0

    async def delete_recovery_codes(self, user_id: str) -> None:
        await self._session.execute(
            delete(RecoveryCodeRow).where(RecoveryCodeRow.user_id == user_id)
        )

    async def add_passkey(self, passkey: Passkey) -> None:
        self._session.add(passkey_to_row(passkey))
        await self._session.flush()

    async def list_passkeys(self, user_id: str) -> list[Passkey]:
        rows = await self._session.scalars(
            select(PasskeyRow)
            .where(PasskeyRow.user_id == user_id)
            .order_by(PasskeyRow.created_at, PasskeyRow.id)
            .execution_options(populate_existing=True)
        )
        return [passkey_from_row(row) for row in rows]

    async def get_passkey_by_credential(self, credential_id: bytes) -> Passkey | None:
        row = await self._session.scalar(
            select(PasskeyRow)
            .where(PasskeyRow.credential_id == credential_id)
            .execution_options(populate_existing=True)
        )
        return passkey_from_row(row) if row is not None else None

    async def record_passkey_use(
        self, passkey_id: str, *, expected_count: int, new_count: int, now: datetime
    ) -> bool:
        updated = await self._session.scalar(
            update(PasskeyRow)
            .where(PasskeyRow.id == passkey_id, PasskeyRow.sign_count == expected_count)
            .values(sign_count=new_count, last_used_at=now)
            .returning(PasskeyRow.id)
        )
        return updated is not None

    async def delete_passkey(self, user_id: str, passkey_id: str) -> bool:
        deleted = await self._session.scalar(
            delete(PasskeyRow)
            .where(PasskeyRow.id == passkey_id, PasskeyRow.user_id == user_id)
            .returning(PasskeyRow.id)
        )
        return deleted is not None

    async def add_challenge(self, challenge: Challenge) -> None:
        self._session.add(challenge_to_row(challenge))
        await self._session.flush()

    async def get_challenge(self, token_hash: str) -> Challenge | None:
        row = await self._session.scalar(
            select(TwoFactorChallengeRow)
            .where(TwoFactorChallengeRow.token_hash == token_hash)
            .execution_options(populate_existing=True)
        )
        return challenge_from_row(row) if row is not None else None

    async def set_webauthn_challenge(self, challenge_id: str, value: bytes | None) -> None:
        await self._session.execute(
            update(TwoFactorChallengeRow)
            .where(TwoFactorChallengeRow.id == challenge_id)
            .values(webauthn_challenge=value)
        )

    async def use_challenge(self, challenge_id: str, now: datetime) -> bool:
        used = await self._session.scalar(
            update(TwoFactorChallengeRow)
            .where(
                TwoFactorChallengeRow.id == challenge_id, TwoFactorChallengeRow.used_at.is_(None)
            )
            .values(used_at=now)
            .returning(TwoFactorChallengeRow.id)
        )
        return used is not None

    async def delete_stale_challenges(self, user_id: str, now: datetime) -> None:
        await self._session.execute(
            delete(TwoFactorChallengeRow).where(
                TwoFactorChallengeRow.user_id == user_id,
                or_(
                    TwoFactorChallengeRow.used_at.is_not(None),
                    TwoFactorChallengeRow.expires_at <= now,
                ),
            )
        )

    async def enabled_user_ids(self, user_ids: Sequence[str]) -> set[str]:
        if not user_ids:
            return set()
        with_totp = select(TotpCredentialRow.user_id).where(
            TotpCredentialRow.user_id.in_(user_ids), TotpCredentialRow.confirmed_at.is_not(None)
        )
        with_passkey = select(PasskeyRow.user_id).where(PasskeyRow.user_id.in_(user_ids))
        rows = await self._session.scalars(with_totp.union(with_passkey))
        return set(rows)

    async def delete_for_user(self, user_id: str) -> None:
        await self._session.execute(
            delete(TotpCredentialRow).where(TotpCredentialRow.user_id == user_id)
        )
        await self._session.execute(
            delete(RecoveryCodeRow).where(RecoveryCodeRow.user_id == user_id)
        )
        await self._session.execute(delete(PasskeyRow).where(PasskeyRow.user_id == user_id))
        await self._session.execute(
            delete(TwoFactorChallengeRow).where(TwoFactorChallengeRow.user_id == user_id)
        )
