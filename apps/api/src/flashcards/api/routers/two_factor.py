# SPDX-License-Identifier: AGPL-3.0-only
"""Two-factor sign-in routes (F-AUTH-3, ADR 0018).

The second step of a sign-in lives under /auth/login/..., next to the first.
Management lives under /auth/two-factor and needs a session; changes that add
or remove a factor also need the password or a current code. Wrong codes and
wrong proofs are returned, not raised: in SQL mode a raised error rolls back
the request, which would discard the attempt counted against the lockout.
"""

from __future__ import annotations

import json
import math

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from flashcards.api.container import Container
from flashcards.api.deps import (
    CurrentUser,
    current_user,
    get_container,
    get_settings,
    require_admin,
    set_session_cookie,
)
from flashcards.api.schemas import AdminUserOut, UserOut
from flashcards.api.two_factor_schemas import (
    CodeIn,
    PasskeyAddedOut,
    PasskeyOptionsOut,
    PasskeyOut,
    PasskeyRegisterIn,
    PasskeyRegistrationOut,
    PasskeySignInIn,
    ProofIn,
    RecoveryCodesOut,
    SecondFactorCodeIn,
    SignInTokenIn,
    TotpSetupOut,
    TwoFactorStatusOut,
)
from flashcards.application.auth_service import AccountSuspended, IssuedSession
from flashcards.config import Settings
from flashcards.domain.login_throttle import LoginLocked
from flashcards.domain.sessions import device_label
from flashcards.domain.two_factor import (
    ChallengeExpired,
    InvalidPasskey,
    InvalidProof,
    InvalidSecondFactor,
    PasskeyNotFound,
    TooManyPasskeys,
    TotpAlreadyEnabled,
    TotpNotPending,
    TotpUnavailable,
    TwoFactorNotEnabled,
)

router = APIRouter(prefix="/auth", tags=["two-factor"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])

_WRONG_CODE = "That code is not right."
_WRONG_PASSKEY = "The passkey could not be verified."
_WRONG_PROOF = "That password or code is not right."
_SIGN_IN_EXPIRED = "This sign-in has expired. Sign in again."


def _locked(exc: LoginLocked) -> HTTPException:
    # Rounded up, so a client that waits exactly this long is never early.
    retry_after = str(max(1, math.ceil(exc.retry_after.total_seconds())))
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "Too many failed sign-in attempts. Try again later.",
        headers={"Retry-After": retry_after},
    )


def _detail(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail)


async def _signed_in(
    issued: IssuedSession, response: Response, container: Container, settings: Settings
) -> UserOut:
    user = await container.user_repo.get(issued.session.user_id)
    if user is None:  # pragma: no cover - the session was issued for this user just now
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _SIGN_IN_EXPIRED)
    set_session_cookie(response, issued.raw_token, settings)
    return UserOut.from_user(user)


# --- second step of sign-in ----------------------------------------------------


@router.post("/login/second-factor", response_model=UserOut)
async def sign_in_with_code(
    body: SecondFactorCodeIn,
    request: Request,
    response: Response,
    container: Container = Depends(get_container, scope="function"),
    settings: Settings = Depends(get_settings),
) -> UserOut | JSONResponse:
    """Finish a sign-in with an authenticator code or a recovery code."""
    try:
        issued = await container.second_factor.complete_with_code(
            token=body.token,
            code=body.code,
            device=device_label(request.headers.get("user-agent")),
        )
    except InvalidSecondFactor:
        return _detail(status.HTTP_401_UNAUTHORIZED, _WRONG_CODE)
    except ChallengeExpired as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _SIGN_IN_EXPIRED) from exc
    except LoginLocked as exc:
        raise _locked(exc) from exc
    except AccountSuspended as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is suspended.") from exc
    return await _signed_in(issued, response, container, settings)


@router.post("/login/passkey/options", response_model=PasskeyOptionsOut)
async def sign_in_passkey_options(
    body: SignInTokenIn,
    container: Container = Depends(get_container, scope="function"),
) -> PasskeyOptionsOut:
    try:
        options_json = await container.second_factor.passkey_options(body.token)
    except ChallengeExpired as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _SIGN_IN_EXPIRED) from exc
    except PasskeyNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This account has no passkey.") from exc
    return PasskeyOptionsOut.from_json(options_json)


@router.post("/login/passkey", response_model=UserOut)
async def sign_in_with_passkey(
    body: PasskeySignInIn,
    request: Request,
    response: Response,
    container: Container = Depends(get_container, scope="function"),
    settings: Settings = Depends(get_settings),
) -> UserOut | JSONResponse:
    """Finish a sign-in with a passkey assertion from navigator.credentials.get()."""
    try:
        issued = await container.second_factor.complete_with_passkey(
            token=body.token,
            response_json=json.dumps(body.credential),
            device=device_label(request.headers.get("user-agent")),
        )
    except InvalidSecondFactor:
        return _detail(status.HTTP_401_UNAUTHORIZED, _WRONG_PASSKEY)
    except ChallengeExpired as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, _SIGN_IN_EXPIRED) from exc
    except LoginLocked as exc:
        raise _locked(exc) from exc
    except AccountSuspended as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is suspended.") from exc
    return await _signed_in(issued, response, container, settings)


# --- management ----------------------------------------------------------------


@router.get("/two-factor", response_model=TwoFactorStatusOut)
async def two_factor_status(
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> TwoFactorStatusOut:
    return TwoFactorStatusOut.from_status(await container.two_factor.status(caller.user.id))


@router.post("/two-factor/totp", response_model=TotpSetupOut)
async def begin_totp(
    body: ProofIn,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> TotpSetupOut | JSONResponse:
    """Start setting up an authenticator app: the secret is shown this once."""
    try:
        setup = await container.two_factor.begin_totp(caller.user, body.to_proof())
    except InvalidProof:
        return _detail(status.HTTP_403_FORBIDDEN, _WRONG_PROOF)
    except LoginLocked as exc:
        raise _locked(exc) from exc
    except TotpUnavailable as exc:
        raise _conflict("Authenticator apps are not available on this instance.") from exc
    except TotpAlreadyEnabled as exc:
        raise _conflict("An authenticator app is already set up.") from exc
    return TotpSetupOut(secret=setup.secret, uri=setup.uri)


@router.post("/two-factor/totp/confirm", response_model=RecoveryCodesOut)
async def confirm_totp(
    body: CodeIn,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> RecoveryCodesOut | JSONResponse:
    """Turn the authenticator app on with its first code."""
    try:
        codes = await container.two_factor.confirm_totp(caller.user, body.code)
    except InvalidSecondFactor:
        return _detail(status.HTTP_422_UNPROCESSABLE_CONTENT, _WRONG_CODE)
    except LoginLocked as exc:
        raise _locked(exc) from exc
    except TotpNotPending as exc:
        raise _conflict("Start setting up the authenticator app again.") from exc
    return RecoveryCodesOut(recovery_codes=list(codes) if codes is not None else None)


@router.delete("/two-factor/totp", status_code=status.HTTP_204_NO_CONTENT)
async def disable_totp(
    body: ProofIn,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    try:
        await container.two_factor.disable_totp(caller.user, body.to_proof())
    except InvalidProof:
        return _detail(status.HTTP_403_FORBIDDEN, _WRONG_PROOF)
    except LoginLocked as exc:
        raise _locked(exc) from exc
    except TwoFactorNotEnabled as exc:
        raise _conflict("No authenticator app is set up.") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/two-factor/recovery-codes", response_model=RecoveryCodesOut)
async def regenerate_recovery_codes(
    body: ProofIn,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> RecoveryCodesOut | JSONResponse:
    """New recovery codes; the old ones stop working."""
    try:
        codes = await container.two_factor.regenerate_recovery_codes(
            caller.user, body.to_proof()
        )
    except InvalidProof:
        return _detail(status.HTTP_403_FORBIDDEN, _WRONG_PROOF)
    except LoginLocked as exc:
        raise _locked(exc) from exc
    except TwoFactorNotEnabled as exc:
        raise _conflict("Two-factor sign-in is not on.") from exc
    return RecoveryCodesOut(recovery_codes=list(codes))


@router.post("/two-factor/passkeys/options", response_model=PasskeyRegistrationOut)
async def begin_passkey(
    body: ProofIn,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> PasskeyRegistrationOut | JSONResponse:
    """Options for navigator.credentials.create(), and the token to finish with."""
    try:
        registration = await container.two_factor.begin_passkey(caller.user, body.to_proof())
    except InvalidProof:
        return _detail(status.HTTP_403_FORBIDDEN, _WRONG_PROOF)
    except LoginLocked as exc:
        raise _locked(exc) from exc
    except TooManyPasskeys as exc:
        raise _conflict("Remove a passkey before adding another.") from exc
    return PasskeyRegistrationOut(
        token=registration.token, options=json.loads(registration.options_json)
    )


@router.post(
    "/two-factor/passkeys", status_code=status.HTTP_201_CREATED, response_model=PasskeyAddedOut
)
async def finish_passkey(
    body: PasskeyRegisterIn,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> PasskeyAddedOut | JSONResponse:
    try:
        added = await container.two_factor.finish_passkey(
            caller.user,
            token=body.token,
            response_json=json.dumps(body.credential),
            name=body.name,
        )
    except ChallengeExpired as exc:
        raise _conflict("This passkey registration has expired. Try again.") from exc
    except InvalidPasskey:
        # Returned, so the registration stays used up.
        return _detail(status.HTTP_422_UNPROCESSABLE_CONTENT, _WRONG_PASSKEY)
    codes = added.recovery_codes
    return PasskeyAddedOut(
        passkey=PasskeyOut.from_passkey(added.passkey),
        recovery_codes=list(codes) if codes is not None else None,
    )


@router.delete("/two-factor/passkeys/{passkey_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_passkey(
    passkey_id: str,
    body: ProofIn,
    caller: CurrentUser = Depends(current_user),
    container: Container = Depends(get_container, scope="function"),
) -> Response:
    try:
        await container.two_factor.remove_passkey(caller.user, passkey_id, body.to_proof())
    except InvalidProof:
        return _detail(status.HTTP_403_FORBIDDEN, _WRONG_PROOF)
    except LoginLocked as exc:
        raise _locked(exc) from exc
    except PasskeyNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such passkey.") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- administration ------------------------------------------------------------


@admin_router.post("/users/{user_id}/two-factor/reset", response_model=AdminUserOut)
async def reset_two_factor(
    user_id: str,
    admin: CurrentUser = Depends(require_admin),
    container: Container = Depends(get_container, scope="function"),
) -> AdminUserOut:
    """Remove a user's second factors, e.g. when they lost their device (audited)."""
    user = await container.two_factor.reset(admin_id=admin.user.id, user_id=user_id)
    return AdminUserOut.from_user(user, two_factor=False)
