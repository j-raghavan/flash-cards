# SPDX-License-Identifier: AGPL-3.0-only
"""Behaviour every TwoFactorRepository must have (run against memory and Postgres)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flashcards.domain.ports import TwoFactorRepository
from flashcards.domain.two_factor import (
    Challenge,
    ChallengePurpose,
    Passkey,
    RecoveryCode,
    TotpCredential,
)

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def _passkey(passkey_id: str, user_id: str, credential: bytes, offset: int = 0) -> Passkey:
    return Passkey(
        id=passkey_id,
        user_id=user_id,
        credential_id=credential,
        public_key=b"cose",
        sign_count=3,
        name="Laptop",
        created_at=NOW + timedelta(seconds=offset),
        transports=("internal",),
    )


def _challenge(challenge_id: str, user_id: str, *, expires_in: timedelta) -> Challenge:
    return Challenge(
        id=challenge_id,
        user_id=user_id,
        purpose=ChallengePurpose.SIGN_IN,
        token_hash=f"hash-{challenge_id}",
        created_at=NOW,
        expires_at=NOW + expires_in,
    )


async def check_totp(repo: TwoFactorRepository, user: str) -> None:
    assert await repo.get_totp(user) is None
    assert not await repo.use_totp_step(user, 1)  # nothing to use
    assert not await repo.confirm_totp(user, 1, NOW)
    await repo.save_totp(TotpCredential(user_id=user, secret_ciphertext="a", created_at=NOW))
    assert not await repo.use_totp_step(user, 5)  # unconfirmed codes do not count
    # Saving again replaces an unconfirmed setup.
    await repo.save_totp(TotpCredential(user_id=user, secret_ciphertext="b", created_at=NOW))
    assert await repo.enabled_user_ids([user]) == set()
    assert await repo.confirm_totp(user, 10, NOW)
    assert not await repo.confirm_totp(user, 11, NOW)  # once
    stored = await repo.get_totp(user)
    assert stored is not None and stored.confirmed and stored.secret_ciphertext == "b"
    assert stored.last_used_step == 10
    assert not await repo.use_totp_step(user, 10)  # the confirming code is spent
    assert not await repo.use_totp_step(user, 9)  # and older ones
    assert await repo.use_totp_step(user, 11)
    assert await repo.enabled_user_ids([user, "nobody"]) == {user}
    assert not await repo.set_totp_secret(user, old="stale", new="x")  # changed meanwhile
    assert await repo.set_totp_secret(user, old="b", new="c")
    assert not await repo.set_totp_secret("nobody", old="b", new="x")
    assert [c.secret_ciphertext for c in await repo.list_totp()] == ["c"]
    assert await repo.delete_totp(user)
    assert not await repo.delete_totp(user)


async def check_recovery_codes(repo: TwoFactorRepository, user: str, other: str) -> None:
    codes = [
        RecoveryCode(id=f"{user}-{i}", user_id=user, code_hash=f"h{i}", created_at=NOW)
        for i in range(3)
    ]
    await repo.replace_recovery_codes(user, codes)
    await repo.replace_recovery_codes(
        other, [RecoveryCode(id=f"{other}-0", user_id=other, code_hash="h0", created_at=NOW)]
    )
    assert await repo.count_recovery_codes(user) == 3
    assert not await repo.use_recovery_code(user, "unknown", NOW)
    assert await repo.use_recovery_code(user, "h1", NOW)
    assert not await repo.use_recovery_code(user, "h1", NOW)  # once
    assert await repo.count_recovery_codes(user) == 2
    assert await repo.count_recovery_codes(other) == 1  # another user's same hash is apart
    await repo.replace_recovery_codes(user, codes[:1])  # replaces all, used or not
    assert await repo.count_recovery_codes(user) == 1
    await repo.delete_recovery_codes(user)
    assert await repo.count_recovery_codes(user) == 0
    await repo.delete_recovery_codes(other)


async def check_passkeys(repo: TwoFactorRepository, user: str, other: str) -> None:
    await repo.add_passkey(_passkey("p2", user, b"cred-2", offset=5))
    await repo.add_passkey(_passkey("p1", user, b"cred-1"))
    assert [p.id for p in await repo.list_passkeys(user)] == ["p1", "p2"]  # oldest first
    assert await repo.enabled_user_ids([user, other]) == {user}
    found = await repo.get_passkey_by_credential(b"cred-2")
    assert found is not None and found.transports == ("internal",)
    assert await repo.get_passkey_by_credential(b"nope") is None
    assert not await repo.record_passkey_use("p1", expected_count=2, new_count=9, now=NOW)
    assert not await repo.record_passkey_use("ghost", expected_count=3, new_count=9, now=NOW)
    assert await repo.record_passkey_use("p1", expected_count=3, new_count=9, now=NOW)
    used = await repo.get_passkey_by_credential(b"cred-1")
    assert used is not None and used.sign_count == 9 and used.last_used_at == NOW
    assert not await repo.delete_passkey(other, "p1")  # not theirs
    assert await repo.delete_passkey(user, "p1")
    assert not await repo.delete_passkey(user, "p1")


async def check_challenges(repo: TwoFactorRepository, user: str) -> None:
    await repo.add_challenge(_challenge("live", user, expires_in=timedelta(minutes=5)))
    await repo.add_challenge(_challenge("old", user, expires_in=timedelta(seconds=-1)))
    await repo.add_challenge(_challenge("used", user, expires_in=timedelta(minutes=5)))
    assert await repo.use_challenge("used", NOW)
    assert not await repo.use_challenge("used", NOW)
    assert not await repo.use_challenge("ghost", NOW)
    await repo.set_webauthn_challenge("live", b"\x01\x02")
    await repo.set_webauthn_challenge("ghost", b"\x01")  # no row: nothing happens
    live = await repo.get_challenge("hash-live")
    assert live is not None and live.webauthn_challenge == b"\x01\x02"
    assert live.is_live(NOW, ChallengePurpose.SIGN_IN)
    assert not await repo.take_webauthn_challenge("live", b"\x09")  # not the one issued
    assert not await repo.take_webauthn_challenge("ghost", b"\x01")
    assert await repo.take_webauthn_challenge("live", b"\x01\x02")
    assert not await repo.take_webauthn_challenge("live", b"\x01\x02")  # once
    cleared = await repo.get_challenge("hash-live")
    assert cleared is not None and cleared.webauthn_challenge is None
    await repo.set_webauthn_challenge("live", None)
    await repo.delete_stale_challenges(user, NOW)
    assert await repo.get_challenge("hash-old") is None
    assert await repo.get_challenge("hash-used") is None
    assert await repo.get_challenge("hash-live") is not None


async def check_delete_for_user(repo: TwoFactorRepository, user: str, other: str) -> None:
    await repo.save_totp(TotpCredential(user_id=user, secret_ciphertext="s", created_at=NOW))
    await repo.replace_recovery_codes(
        user, [RecoveryCode(id="gone", user_id=user, code_hash="g", created_at=NOW)]
    )
    await repo.add_passkey(_passkey("kept", other, b"cred-kept"))
    await repo.delete_for_user(user)
    assert await repo.get_totp(user) is None
    assert await repo.count_recovery_codes(user) == 0
    assert await repo.list_passkeys(user) == []
    assert await repo.get_challenge("hash-live") is None
    assert [p.id for p in await repo.list_passkeys(other)] == ["kept"]
    assert await repo.enabled_user_ids([]) == set()


async def check_repository(repo: TwoFactorRepository, user: str, other: str) -> None:
    await check_totp(repo, user)
    await check_recovery_codes(repo, user, other)
    await check_passkeys(repo, user, other)
    await check_challenges(repo, user)
    await check_delete_for_user(repo, user, other)
