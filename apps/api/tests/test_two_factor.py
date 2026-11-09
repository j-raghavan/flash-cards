# SPDX-License-Identifier: AGPL-3.0-only
"""Two-factor sign-in: services, adapters and the in-memory repository (F-AUTH-3)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from soft_authenticator import SoftAuthenticator, b64url, unb64url
from two_factor_contract import check_repository

from flashcards.adapters.memory import (
    InMemoryAuditRepository,
    InMemoryLoginThrottleRepository,
    InMemorySessionRepository,
    InMemoryTwoFactorRepository,
    InMemoryUserRepository,
)
from flashcards.adapters.two_factor import (
    FernetSecretBox,
    PyOtpPasswords,
    WebAuthnCeremonies,
    parse_keys,
    relying_party,
)
from flashcards.application.audit_service import AuditService
from flashcards.application.auth_service import AccountSuspended, AuthService, IssuedSession
from flashcards.application.login_attempts import LoginAttempts
from flashcards.application.second_factor_sign_in_service import SecondFactorSignInService
from flashcards.application.sign_in_service import SignInService, throttle_key
from flashcards.application.two_factor_service import Proof, TwoFactorService
from flashcards.domain.audit import AuditAction
from flashcards.domain.invites import Role, hash_token
from flashcards.domain.login_throttle import MAX_FAILED_LOGINS, LoginLocked
from flashcards.domain.two_factor import (
    CHALLENGE_TTL,
    MAX_PASSKEYS,
    RECOVERY_CODE_COUNT,
    TOTP_SETUP_TTL,
    ChallengeExpired,
    InvalidPasskey,
    InvalidProof,
    InvalidSecondFactor,
    Passkey,
    PasskeyNotFound,
    SecondFactor,
    SecondFactorRequired,
    SecretUnreadable,
    TooManyPasskeys,
    TotpAlreadyEnabled,
    TotpNotPending,
    TotpUnavailable,
    TwoFactorNotEnabled,
    compact_code,
    counter_regressed,
    is_totp_code,
    new_recovery_code,
    passkey_name,
)
from flashcards.domain.users import User, UserNotFound

START = datetime(2026, 11, 8, 12, 0, 5, tzinfo=UTC)
PASSWORD = "correct-horse-battery"
EMAIL = "person@example.com"


class _Clock:
    def __init__(self) -> None:
        self.value = START

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class _Hasher:
    async def hash(self, password: str) -> str:
        return f"hashed:{password}"

    async def verify(self, password_hash: str, password: str) -> bool:
        return password_hash == f"hashed:{password}"


class _RacingRepo(InMemoryTwoFactorRepository):
    """Loses every conditional write, as if a concurrent request won it."""

    lose = False

    async def confirm_totp(self, user_id: str, step: int, now: datetime) -> bool:
        return False if self.lose else await super().confirm_totp(user_id, step, now)

    async def use_challenge(self, challenge_id: str, now: datetime) -> bool:
        return False if self.lose else await super().use_challenge(challenge_id, now)

    async def record_passkey_use(
        self, passkey_id: str, *, expected_count: int, new_count: int, now: datetime
    ) -> bool:
        if self.lose:
            return False
        return await super().record_passkey_use(
            passkey_id, expected_count=expected_count, new_count=new_count, now=now
        )


class World:
    def __init__(self, *, box: FernetSecretBox | None = None, with_box: bool = True) -> None:
        self.clock = _Clock()
        self.users = InMemoryUserRepository()
        self.repo = _RacingRepo()
        self.throttle = InMemoryLoginThrottleRepository()
        self.audit_events = InMemoryAuditRepository()
        audit = AuditService(self.audit_events, self.clock)
        self.auth = AuthService(self.users, InMemorySessionRepository(), _Hasher(), self.clock)
        self.ceremonies = WebAuthnCeremonies(public_base_url="http://localhost:5173")
        self.box = box or FernetSecretBox.ephemeral()
        attempts = LoginAttempts(self.throttle, audit, self.clock)
        self.two_factor = TwoFactorService(
            self.repo,
            self.users,
            _Hasher(),
            PyOtpPasswords(),
            self.ceremonies,
            attempts,
            audit,
            self.clock,
            box=self.box if with_box else None,
        )
        self.sign_in = SignInService(
            self.auth, self.throttle, self.users, audit, self.clock, two_factor=self.two_factor
        )
        self.second = SecondFactorSignInService(
            self.auth,
            self.two_factor,
            self.repo,
            self.users,
            self.ceremonies,
            attempts,
            audit,
            self.clock,
        )
        self.user = User(
            id="u1", email=EMAIL, password_hash=f"hashed:{PASSWORD}", role=Role.MEMBER,
            created_at=START,
        )
        self.secret = ""

    async def add_user(self) -> User:
        await self.users.add(self.user)
        return self.user

    def code(self, offset: int = 0) -> str:
        return pyotp.TOTP(self.secret).at(self.clock.now(), counter_offset=offset)

    async def enable_totp(self) -> tuple[str, ...] | None:
        setup = await self.two_factor.begin_totp(self.user, Proof(password=PASSWORD))
        self.secret = setup.secret
        return await self.two_factor.confirm_totp(self.user, self.code())

    async def add_passkey(self, device: SoftAuthenticator) -> Passkey:
        registration = await self.two_factor.begin_passkey(self.user, Proof(password=PASSWORD))
        options = json.loads(registration.options_json)
        added = await self.two_factor.finish_passkey(
            self.user,
            token=registration.token,
            response_json=json.dumps(device.register(options)),
            name="  My   laptop ",
        )
        return added.passkey

    async def pending_token(self) -> str:
        with pytest.raises(SecondFactorRequired) as raised:
            await self.sign_in.login(email=EMAIL, password=PASSWORD)
        return raised.value.pending.token

    def actions(self) -> list[AuditAction]:
        return [e.action for e in self.audit_events._events]  # in recording order


@pytest.fixture
async def world() -> World:
    w = World()
    await w.add_user()
    return w


# --- domain -------------------------------------------------------------------


def test_codes_and_names() -> None:
    assert is_totp_code(" 123 456 ") and not is_totp_code("12345a")
    assert compact_code("AB-cd ef") == "abcdef"
    code = new_recovery_code()
    assert len(code) == 19 and code.count("-") == 3
    assert new_recovery_code() != code
    assert passkey_name(None) == "Passkey"
    assert passkey_name("  a   b ") == "a b"
    assert len(passkey_name("x" * 500)) == 60
    assert counter_regressed(5, 5) and counter_regressed(5, 4)
    assert not counter_regressed(0, 0) and not counter_regressed(5, 6)


# --- adapters -----------------------------------------------------------------


def test_secret_box_encrypts_and_rotates() -> None:
    old = FernetSecretBox.ephemeral()
    sealed = old.seal("JBSWY3DPEHPK3PXP")
    assert sealed != "JBSWY3DPEHPK3PXP"
    assert old.open(sealed) == "JBSWY3DPEHPK3PXP"
    new_key = FernetSecretBox.ephemeral()
    with pytest.raises(SecretUnreadable):
        new_key.open(sealed)
    with pytest.raises(SecretUnreadable):
        new_key.reseal("garbage")
    with pytest.raises(ValueError, match="at least one key"):
        FernetSecretBox([])
    with pytest.raises(ValueError):
        FernetSecretBox(["not-a-key"])


def test_key_rotation_keeps_old_secrets_readable() -> None:
    from cryptography.fernet import Fernet

    first, second = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    sealed = FernetSecretBox([first]).seal("secret")
    both = FernetSecretBox(parse_keys(f" {second} , {first},"))
    assert both.open(sealed) == "secret"
    resealed = both.reseal(sealed)
    assert FernetSecretBox([second]).open(resealed) == "secret"
    assert parse_keys("") == []


def test_totp_matches_within_one_step_of_drift() -> None:
    otp = PyOtpPasswords()
    secret = otp.new_secret()
    assert len(secret) == 32
    totp = pyotp.TOTP(secret)
    step = totp.timecode(START)
    for offset in (-1, 0, 1):
        assert otp.matching_step(secret, totp.at(START, counter_offset=offset), START) == (
            step + offset
        )
    assert otp.matching_step(secret, totp.at(START, counter_offset=2), START) is None
    assert otp.matching_step(secret, "12345", START) is None
    assert otp.matching_step(secret, "１２３４５６", START) is None  # not ASCII digits
    uri = otp.provisioning_uri(secret, account=EMAIL, issuer="Flash-Cards")
    assert uri.startswith("otpauth://totp/Flash-Cards:person%40example.com?secret=")


def test_relying_party_comes_from_the_public_url() -> None:
    assert relying_party("https://Cards.Example.com/app") == (
        "cards.example.com",
        "https://cards.example.com",
    )
    assert relying_party("https://cards.example.com:443") == (
        "cards.example.com",
        "https://cards.example.com",
    )
    assert relying_party("http://localhost:5173") == ("localhost", "http://localhost:5173")
    with pytest.raises(ValueError):
        relying_party("not-a-url")


def test_a_bad_public_url_makes_passkeys_unusable() -> None:
    ceremonies = WebAuthnCeremonies(public_base_url="ftp://nowhere")
    options = json.loads(
        ceremonies.registration_options(user_handle=b"u", user_name="a", exclude=[]).options_json
    )
    assert options["rp"]["id"] == "invalid.invalid"


def test_ceremonies_refuse_malformed_and_foreign_responses() -> None:
    ceremonies = WebAuthnCeremonies(public_base_url="http://localhost:5173")
    with pytest.raises(InvalidPasskey):
        ceremonies.verify_registration("{}", b"challenge")
    with pytest.raises(InvalidPasskey):
        ceremonies.credential_id("not json")
    options = ceremonies.registration_options(user_handle=b"u", user_name="a", exclude=[b"x"])
    device = SoftAuthenticator()
    parsed = json.loads(options.options_json)
    assert parsed["excludeCredentials"][0]["id"] == b64url(b"x")
    # Another origin (say, a phishing page) is refused.
    phished = device.register(parsed, origin="https://evil.example")
    with pytest.raises(InvalidPasskey):
        ceremonies.verify_registration(json.dumps(phished), options.challenge)
    # The right origin with another challenge is refused too.
    with pytest.raises(InvalidPasskey):
        ceremonies.verify_registration(json.dumps(device.register(parsed)), b"other")
    new = ceremonies.verify_registration(json.dumps(device.register(parsed)), options.challenge)
    assert new.transports == ("internal", "hybrid")
    auth = ceremonies.authentication_options([(new.credential_id, ("internal", "warp-drive"))])
    allow = json.loads(auth.options_json)["allowCredentials"]
    assert allow[0]["transports"] == ["internal"]  # unknown transports are dropped
    bare = ceremonies.authentication_options([(new.credential_id, ("warp-drive",))])
    assert "transports" not in json.loads(bare.options_json)["allowCredentials"][0]
    answer = device.assert_(json.loads(auth.options_json))
    assert ceremonies.credential_id(json.dumps(answer)) == new.credential_id
    assert ceremonies.verify_authentication(json.dumps(answer), auth.challenge, new.public_key) == 1
    forged = device.assert_(json.loads(auth.options_json), origin="https://evil.example")
    with pytest.raises(InvalidPasskey):
        ceremonies.verify_authentication(json.dumps(forged), auth.challenge, new.public_key)
    assert unb64url(b64url(b"\x00\xff")) == b"\x00\xff"


async def test_memory_repository_contract() -> None:
    await check_repository(InMemoryTwoFactorRepository(), "u1", "u2")


# --- sign-in ------------------------------------------------------------------


async def test_without_a_second_factor_sign_in_is_one_step(world: World) -> None:
    issued = await world.sign_in.login(email=EMAIL, password=PASSWORD)
    assert isinstance(issued, IssuedSession)
    assert (await world.two_factor.status(world.user.id)).enabled is False


async def test_totp_sign_in_needs_a_fresh_code(world: World) -> None:
    codes = await world.enable_totp()
    assert codes is not None and len(codes) == RECOVERY_CODE_COUNT
    assert world.actions()[-2:] == [AuditAction.TOTP_ENABLED, AuditAction.RECOVERY_CODES_GENERATED]
    with pytest.raises(SecondFactorRequired) as raised:
        await world.sign_in.login(email=EMAIL, password=PASSWORD)
    pending = raised.value.pending
    assert pending.methods == (SecondFactor.TOTP, SecondFactor.RECOVERY_CODE)
    # The password alone did not clear the lockout count.
    assert await world.throttle.get(throttle_key(EMAIL)) is not None
    # The code that confirmed the app is spent; the next step's code works once.
    with pytest.raises(InvalidSecondFactor):
        await world.second.complete_with_code(token=pending.token, code=world.code())
    issued = await world.second.complete_with_code(token=pending.token, code=world.code(1))
    assert issued.session.user_id == world.user.id
    assert await world.throttle.get(throttle_key(EMAIL)) is None
    # The pending sign-in works once.
    with pytest.raises(ChallengeExpired):
        await world.second.complete_with_code(token=pending.token, code=world.code(1))
    token = await world.pending_token()
    with pytest.raises(InvalidSecondFactor):  # the same code cannot be replayed
        await world.second.complete_with_code(token=token, code=world.code(1))


async def test_recovery_codes_work_once(world: World) -> None:
    codes = await world.enable_totp()
    assert codes is not None
    token = await world.pending_token()
    await world.second.complete_with_code(token=token, code=codes[0].upper().replace("-", " "))
    assert AuditAction.RECOVERY_CODE_USED in world.actions()
    token = await world.pending_token()
    with pytest.raises(InvalidSecondFactor):
        await world.second.complete_with_code(token=token, code=codes[0])
    status = await world.two_factor.status(world.user.id)
    assert status.recovery_codes_left == RECOVERY_CODE_COUNT - 1


async def test_failed_codes_count_towards_the_lockout(world: World) -> None:
    await world.enable_totp()
    token = await world.pending_token()  # the password attempt counts as one
    for _ in range(MAX_FAILED_LOGINS - 1):
        with pytest.raises(InvalidSecondFactor):
            await world.second.complete_with_code(token=token, code="000000")
    assert world.actions()[-1] is AuditAction.LOGIN_LOCKED
    with pytest.raises(LoginLocked):
        await world.second.complete_with_code(token=token, code=world.code(1))
    with pytest.raises(LoginLocked):
        await world.sign_in.login(email=EMAIL, password=PASSWORD)


async def test_clearing_the_email_lock_gives_no_fresh_code_guesses(world: World) -> None:
    # A password reset clears the per-email count; the per-account second-factor
    # count stays, so resetting again and again does not reopen code guessing.
    await world.enable_totp()
    token = await world.pending_token()
    for _ in range(MAX_FAILED_LOGINS - 1):
        with pytest.raises(InvalidSecondFactor):
            await world.second.complete_with_code(token=token, code="000000")
    await world.throttle.delete(throttle_key(EMAIL))  # what a password reset does
    token = await world.pending_token()
    with pytest.raises(InvalidSecondFactor):  # the fifth code attempt starts the lock
        await world.second.complete_with_code(token=token, code="000000")
    with pytest.raises(LoginLocked):
        await world.second.complete_with_code(token=token, code=world.code(1))


async def test_confirming_an_app_is_counted(world: World) -> None:
    setup = await world.two_factor.begin_totp(world.user, Proof(password=PASSWORD))
    world.secret = setup.secret
    wrong = "000000" if world.code() != "000000" else "111111"
    for _ in range(MAX_FAILED_LOGINS):
        with pytest.raises(InvalidSecondFactor):
            await world.two_factor.confirm_totp(world.user, wrong)
    with pytest.raises(LoginLocked):
        await world.two_factor.confirm_totp(world.user, world.code())


async def test_racing_passkey_answers_check_one(world: World) -> None:
    device = SoftAuthenticator()
    await world.add_passkey(device)
    token = await world.pending_token()
    options = json.loads(await world.second.passkey_options(token))
    challenge = await world.repo.get_challenge(hash_token(token))
    assert challenge is not None and challenge.webauthn_challenge is not None
    # Another answer took the challenge first.
    assert await world.repo.take_webauthn_challenge(challenge.id, challenge.webauthn_challenge)
    await world.repo.set_webauthn_challenge(challenge.id, b"someone else's")
    with pytest.raises(InvalidSecondFactor):
        await world.second.complete_with_passkey(
            token=token, response_json=json.dumps(device.assert_(options))
        )


async def test_a_pending_sign_in_expires(world: World) -> None:
    await world.enable_totp()
    token = await world.pending_token()
    world.clock.advance(CHALLENGE_TTL)
    with pytest.raises(ChallengeExpired):
        await world.second.complete_with_code(token=token, code=world.code())
    with pytest.raises(ChallengeExpired):
        await world.second.complete_with_code(token="unknown", code="123456")


async def test_a_pending_sign_in_ends_with_the_account(world: World) -> None:
    await world.enable_totp()
    token = await world.pending_token()
    await world.users.delete(world.user.id)
    with pytest.raises(ChallengeExpired):
        await world.second.complete_with_code(token=token, code=world.code(1))


async def test_a_suspended_account_cannot_finish(world: World) -> None:
    await world.enable_totp()
    token = await world.pending_token()
    await world.users.update(world.user.with_suspended(START))
    with pytest.raises(AccountSuspended):
        await world.second.complete_with_code(token=token, code=world.code(1))


async def test_racing_answers_issue_one_session(world: World) -> None:
    await world.enable_totp()
    token = await world.pending_token()
    world.repo.lose = True
    with pytest.raises(ChallengeExpired):
        await world.second.complete_with_code(token=token, code=world.code(1))


# --- passkeys -----------------------------------------------------------------


async def test_passkey_sign_in(world: World) -> None:
    device = SoftAuthenticator()
    passkey = await world.add_passkey(device)
    assert passkey.name == "My laptop"
    token = await world.pending_token()
    with pytest.raises(InvalidSecondFactor):  # no authenticator app to take a code
        await world.second.complete_with_code(token=token, code="123456")
    options = json.loads(await world.second.passkey_options(token))
    assert options["rpId"] == "localhost"
    issued = await world.second.complete_with_passkey(
        token=token, response_json=json.dumps(device.assert_(options))
    )
    assert issued.session.user_id == world.user.id
    stored = await world.repo.get_passkey_by_credential(device.credential_id)
    assert stored is not None and stored.sign_count == 1 and stored.last_used_at is not None


async def test_passkey_answers_are_checked(world: World) -> None:
    device = SoftAuthenticator()
    await world.add_passkey(device)
    token = await world.pending_token()
    # No options asked for yet: there is no challenge to answer.
    with pytest.raises(InvalidSecondFactor):
        await world.second.complete_with_passkey(token=token, response_json="{}")
    options = json.loads(await world.second.passkey_options(token))
    answer = json.dumps(device.assert_(options))
    with pytest.raises(InvalidSecondFactor):  # a malformed answer
        await world.second.complete_with_passkey(token=token, response_json="{}")
    with pytest.raises(InvalidSecondFactor):  # the challenge answered once already
        await world.second.complete_with_passkey(token=token, response_json=answer)
    options = json.loads(await world.second.passkey_options(token))
    stranger = SoftAuthenticator()
    with pytest.raises(InvalidSecondFactor):  # a passkey nobody registered
        await world.second.complete_with_passkey(
            token=token, response_json=json.dumps(stranger.assert_(options))
        )
    await world.throttle.delete(throttle_key(EMAIL))  # four failures so far: stay unlocked
    options = json.loads(await world.second.passkey_options(token))
    with pytest.raises(InvalidSecondFactor):  # signed for another origin
        await world.second.complete_with_passkey(
            token=token,
            response_json=json.dumps(device.assert_(options, origin="https://evil.example")),
        )


async def test_a_passkey_of_another_account_is_refused(world: World) -> None:
    device = SoftAuthenticator()
    await world.add_passkey(device)
    other = User(
        id="u2", email="other@example.com", password_hash=f"hashed:{PASSWORD}",
        role=Role.MEMBER, created_at=START,
    )
    await world.users.add(other)
    world.user = other
    await world.add_passkey(SoftAuthenticator())
    with pytest.raises(SecondFactorRequired) as raised:
        await world.sign_in.login(email="other@example.com", password=PASSWORD)
    token = raised.value.pending.token
    options = json.loads(await world.second.passkey_options(token))
    with pytest.raises(InvalidSecondFactor):
        await world.second.complete_with_passkey(
            token=token, response_json=json.dumps(device.assert_(options))
        )


async def test_a_counter_that_goes_back_is_refused_and_audited(world: World) -> None:
    device = SoftAuthenticator()
    await world.add_passkey(device)
    token = await world.pending_token()
    options = json.loads(await world.second.passkey_options(token))
    await world.second.complete_with_passkey(
        token=token, response_json=json.dumps(device.assert_(options, count=7))
    )
    token = await world.pending_token()
    options = json.loads(await world.second.passkey_options(token))
    with pytest.raises(InvalidSecondFactor):
        await world.second.complete_with_passkey(
            token=token, response_json=json.dumps(device.assert_(options, count=7))
        )
    assert AuditAction.PASSKEY_COUNTER_REGRESSED in world.actions()


async def test_a_counter_raced_by_another_request_is_refused(world: World) -> None:
    device = SoftAuthenticator()
    await world.add_passkey(device)
    token = await world.pending_token()
    options = json.loads(await world.second.passkey_options(token))
    world.repo.lose = True
    with pytest.raises(InvalidSecondFactor):
        await world.second.complete_with_passkey(
            token=token, response_json=json.dumps(device.assert_(options))
        )


async def test_passkey_options_need_a_passkey(world: World) -> None:
    await world.enable_totp()
    token = await world.pending_token()
    with pytest.raises(PasskeyNotFound):
        await world.second.passkey_options(token)


async def test_both_factors_and_recovery_codes(world: World) -> None:
    await world.add_passkey(SoftAuthenticator())
    assert await world.enable_totp() is None  # codes were issued with the passkey
    with pytest.raises(SecondFactorRequired) as raised:
        await world.sign_in.login(email=EMAIL, password=PASSWORD)
    assert raised.value.pending.methods == (
        SecondFactor.TOTP,
        SecondFactor.PASSKEY,
        SecondFactor.RECOVERY_CODE,
    )
    await world.repo.delete_recovery_codes(world.user.id)
    with pytest.raises(SecondFactorRequired) as raised:
        await world.sign_in.login(email=EMAIL, password=PASSWORD)
    assert raised.value.pending.methods == (SecondFactor.TOTP, SecondFactor.PASSKEY)


async def test_passkey_registration_rules(world: World) -> None:
    device = SoftAuthenticator()
    registration = await world.two_factor.begin_passkey(world.user, Proof(password=PASSWORD))
    response = json.dumps(device.register(json.loads(registration.options_json)))
    added = await world.two_factor.finish_passkey(
        world.user, token=registration.token, response_json=response, name=None
    )
    assert added.recovery_codes is not None and added.passkey.name == "Passkey"
    with pytest.raises(ChallengeExpired):  # a registration works once
        await world.two_factor.finish_passkey(
            world.user, token=registration.token, response_json=response, name=None
        )
    # The same authenticator cannot be registered twice.
    again = await world.two_factor.begin_passkey(world.user, Proof(password=PASSWORD))
    with pytest.raises(InvalidPasskey):
        await world.two_factor.finish_passkey(
            world.user,
            token=again.token,
            response_json=json.dumps(device.register(json.loads(again.options_json))),
            name=None,
        )
    # Another account cannot finish this account's registration.
    third = await world.two_factor.begin_passkey(world.user, Proof(password=PASSWORD))
    stranger = User(
        id="u9", email="x@example.com", password_hash="h", role=Role.MEMBER, created_at=START
    )
    with pytest.raises(ChallengeExpired):
        await world.two_factor.finish_passkey(
            stranger, token=third.token, response_json=response, name=None
        )
    world.clock.advance(CHALLENGE_TTL)
    with pytest.raises(ChallengeExpired):
        await world.two_factor.finish_passkey(
            world.user, token=third.token, response_json=response, name=None
        )
    fourth = await world.two_factor.begin_passkey(world.user, Proof(password=PASSWORD))
    world.repo.lose = True
    with pytest.raises(ChallengeExpired):
        await world.two_factor.finish_passkey(
            world.user, token=fourth.token, response_json=response, name=None
        )


async def test_passkeys_are_limited(world: World) -> None:
    for _ in range(MAX_PASSKEYS):
        await world.add_passkey(SoftAuthenticator())
    with pytest.raises(TooManyPasskeys):
        await world.two_factor.begin_passkey(world.user, Proof(password=PASSWORD))


async def test_removing_the_last_factor_forgets_recovery_codes(world: World) -> None:
    passkey = await world.add_passkey(SoftAuthenticator())
    with pytest.raises(PasskeyNotFound):
        await world.two_factor.remove_passkey(world.user, "ghost", Proof(password=PASSWORD))
    with pytest.raises(InvalidProof):
        await world.two_factor.remove_passkey(world.user, passkey.id, Proof(password="wrong"))
    await world.two_factor.remove_passkey(world.user, passkey.id, Proof(password=PASSWORD))
    status = await world.two_factor.status(world.user.id)
    assert not status.enabled and status.recovery_codes_left == 0
    assert AuditAction.PASSKEY_REMOVED in world.actions()


# --- managing the authenticator app ---------------------------------------------


async def test_totp_setup_rules(world: World) -> None:
    with pytest.raises(TotpNotPending):
        await world.two_factor.confirm_totp(world.user, "123456")
    with pytest.raises(InvalidProof):
        await world.two_factor.begin_totp(world.user, Proof())
    setup = await world.two_factor.begin_totp(world.user, Proof(password=PASSWORD))
    world.secret = setup.secret
    assert setup.uri.startswith("otpauth://totp/")
    stored = await world.repo.get_totp(world.user.id)
    assert stored is not None and setup.secret not in stored.secret_ciphertext
    wrong = "000000" if world.code() != "000000" else "111111"
    with pytest.raises(InvalidSecondFactor):
        await world.two_factor.confirm_totp(world.user, wrong)
    world.clock.advance(TOTP_SETUP_TTL)
    with pytest.raises(TotpNotPending):  # too slow: start again
        await world.two_factor.confirm_totp(world.user, world.code())
    await world.enable_totp()
    with pytest.raises(TotpAlreadyEnabled):
        await world.two_factor.begin_totp(world.user, Proof(password=PASSWORD))
    with pytest.raises(TotpNotPending):
        await world.two_factor.confirm_totp(world.user, world.code())


async def test_a_confirmation_raced_by_another_request(world: World) -> None:
    setup = await world.two_factor.begin_totp(world.user, Proof(password=PASSWORD))
    world.secret = setup.secret
    world.repo.lose = True
    with pytest.raises(TotpNotPending):
        await world.two_factor.confirm_totp(world.user, world.code())


async def test_an_unreadable_secret_is_never_accepted(world: World) -> None:
    setup = await world.two_factor.begin_totp(world.user, Proof(password=PASSWORD))
    world.secret = setup.secret
    stored = await world.repo.get_totp(world.user.id)
    assert stored is not None
    await world.repo.set_totp_secret(world.user.id, old=stored.secret_ciphertext, new="garbage")
    with pytest.raises(TotpNotPending):
        await world.two_factor.confirm_totp(world.user, world.code())
    await world.repo.confirm_totp(world.user.id, 0, START)
    assert not await world.two_factor.verify_code(world.user, world.code())


async def test_without_a_key_there_are_no_authenticator_apps() -> None:
    w = World(with_box=False)
    await w.add_user()
    with pytest.raises(TotpUnavailable):
        await w.two_factor.begin_totp(w.user, Proof(password=PASSWORD))
    assert (await w.two_factor.status(w.user.id)).totp_available is False
    # An app set up while a key existed cannot be used without it.
    with_key = World()
    await with_key.add_user()
    await with_key.enable_totp()
    confirmed = await with_key.repo.get_totp(with_key.user.id)
    assert confirmed is not None
    await w.repo.save_totp(confirmed)
    assert not await w.two_factor.verify_code(w.user, with_key.code(1))
    # Nor can one waiting to be confirmed.
    await w.repo.save_totp(replace(confirmed, confirmed_at=None))
    with pytest.raises(TotpNotPending):
        await w.two_factor.confirm_totp(w.user, with_key.code(1))


async def test_disable_and_regenerate_need_proof(world: World) -> None:
    with pytest.raises(TwoFactorNotEnabled):
        await world.two_factor.disable_totp(world.user, Proof(password=PASSWORD))
    with pytest.raises(TwoFactorNotEnabled):
        await world.two_factor.regenerate_recovery_codes(world.user, Proof(password=PASSWORD))
    await world.enable_totp()
    with pytest.raises(InvalidProof):
        await world.two_factor.regenerate_recovery_codes(world.user, Proof(code="000000"))
    codes = await world.two_factor.regenerate_recovery_codes(
        world.user, Proof(code=world.code(1), password="wrong")
    )
    assert len(codes) == RECOVERY_CODE_COUNT
    await world.two_factor.disable_totp(world.user, Proof(code="bad", password=PASSWORD))
    status = await world.two_factor.status(world.user.id)
    assert not status.totp and status.recovery_codes_left == 0
    assert AuditAction.TOTP_DISABLED in world.actions()


async def test_disabling_totp_keeps_codes_while_a_passkey_remains(world: World) -> None:
    await world.add_passkey(SoftAuthenticator())
    await world.enable_totp()
    await world.two_factor.disable_totp(world.user, Proof(password=PASSWORD))
    assert (await world.two_factor.status(world.user.id)).recovery_codes_left == RECOVERY_CODE_COUNT


async def test_wrong_proofs_lock_the_account(world: World) -> None:
    for _ in range(MAX_FAILED_LOGINS):
        with pytest.raises(InvalidProof):
            await world.two_factor.begin_totp(world.user, Proof(password="wrong"))
    assert world.actions()[-1] is AuditAction.LOGIN_LOCKED
    with pytest.raises(LoginLocked):
        await world.two_factor.begin_totp(world.user, Proof(password=PASSWORD))


async def test_an_admin_can_reset_a_users_second_factor(world: World) -> None:
    await world.enable_totp()
    await world.add_passkey(SoftAuthenticator())
    assert await world.two_factor.enabled_among([world.user.id, "nobody"]) == {world.user.id}
    with pytest.raises(UserNotFound):
        await world.two_factor.reset(admin_id="admin", user_id="nobody")
    await world.two_factor.reset(admin_id="admin", user_id=world.user.id)
    assert not (await world.two_factor.status(world.user.id)).enabled
    event = world.audit_events._events[-1]
    assert event.action is AuditAction.TWO_FACTOR_RESET and event.actor_id == "admin"
    issued = await world.sign_in.login(email=EMAIL, password=PASSWORD)
    assert issued.session.user_id == world.user.id
