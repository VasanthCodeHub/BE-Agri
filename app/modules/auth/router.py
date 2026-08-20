"""Authentication endpoints.

All login-related routes live here, so they appear as a single "auth" group in
Swagger at /docs.

Notice how thin each handler is: parse the request, call the service, return the
result. No business rules, no SQL. If a handler ever grows an `if`, that logic
probably belongs in the service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.modules.auth.dependencies import (
    get_active_role,
    get_auth_service,
    get_current_user,
    get_user_agent,
)
from app.modules.auth.schemas import (
    LoginOut,
    LogoutIn,
    OtpRequestIn,
    OtpRequestOut,
    OtpVerifyIn,
    RefreshIn,
    TokenPairOut,
    UserOut,
)
from app.modules.auth.service import AuthService
from app.modules.users.models import User, UserRole

router = APIRouter()


@router.post(
    "/otp/request",
    response_model=OtpRequestOut,
    summary="Step 1 — request a login code",
    responses={
        403: {"description": "Account suspended"},
        422: {"description": "Invalid phone number, or no role selected"},
        503: {"description": "The SMS gateway could not be reached — safe to retry"},
    },
)
async def request_otp(
    payload: OtpRequestIn,
    service: AuthService = Depends(get_auth_service),
) -> OtpRequestOut:
    """Send a one-time code to a phone number.

    A role (`RENTER` or `PROVIDER`) must be chosen here. It is stored against
    the code, so it cannot be changed at verification time.

    The phone number is accepted in any common form — `9876543210`,
    `09876543210`, `+919876543210`, `91 98765 43210` — and normalised to E.164,
    so one person typing it four ways is still one account.

    The response tells you whether this is a new number, and whether to collect
    a name before verifying. The number comes back **masked**, for display.

    **Local development:** no SMS is sent — the code is printed in the server
    terminal as `fake_sms_otp ... otp=1234`, and `OTP_DEV_BYPASS_CODE` works for
    any number. **Production:** delivered by SMS through Twilio; a gateway
    failure returns `503 OTP_SEND_FAILED` and is safe to retry.
    """
    return await service.request_otp(phone_e164=payload.phone, role=payload.role)


@router.post(
    "/otp/verify",
    response_model=LoginOut,
    summary="Step 2 — verify the code and log in",
    responses={
        400: {"description": "Code wrong, expired, already used, or name missing"},
        403: {"description": "Account suspended"},
    },
)
async def verify_otp(
    payload: OtpVerifyIn,
    service: AuthService = Depends(get_auth_service),
    user_agent: str | None = Depends(get_user_agent),
) -> LoginOut:
    """Verify the code, create the user if new, and return a session.

    The user row is created **here**, only after the phone number has been
    proved — so unverified numbers never enter the database.

    A code works exactly once and expires. Wrong guesses are counted: after
    `OTP_MAX_ATTEMPTS` the code is burned and a new one must be requested.
    `details.remaining_attempts` on a `400 OTP_INVALID` says how many are left.

    If the number already exists but lacks the requested role, the role is
    granted. One phone number can be both a renter and a provider.

    Returns an access token (short-lived, sent with every request) and a refresh
    token (long-lived, used only to get a new access token).
    """
    return await service.verify_otp(
        phone_e164=payload.phone,
        code=payload.code,
        name=payload.name,
        user_agent=user_agent,
        email=payload.email,
        address=payload.address,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )


@router.post(
    "/refresh",
    response_model=TokenPairOut,
    summary="Get a new access token",
    responses={401: {"description": "Refresh token invalid, expired, or reused"}},
)
async def refresh(
    payload: RefreshIn,
    service: AuthService = Depends(get_auth_service),
    user_agent: str | None = Depends(get_user_agent),
) -> TokenPairOut:
    """Exchange a refresh token for a fresh token pair.

    Call this when a request returns `401 TOKEN_EXPIRED`, then retry the original
    request. The user notices nothing.

    The old refresh token is invalidated and a new one issued (rotation). If a
    token that was already exchanged is presented again, every session in that
    chain is revoked — that pattern means the token was copied.
    """
    return await service.refresh_session(refresh_token=payload.refresh_token, user_agent=user_agent)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End the session",
)
async def logout(
    payload: LogoutIn,
    service: AuthService = Depends(get_auth_service),
) -> Response:
    """Revoke the refresh token. Pass `all_devices: true` to log out everywhere.

    Always returns 204, whether or not the token existed — reporting the
    difference would let a caller test which tokens are valid.

    The access token remains usable for its final few minutes; that is the
    trade-off that keeps every other request fast.
    """
    await service.logout(refresh_token=payload.refresh_token, all_devices=payload.all_devices)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me",
    response_model=UserOut,
    summary="Who am I? (session check)",
    responses={401: {"description": "Missing, invalid or expired token"}},
)
async def me(
    user: User = Depends(get_current_user),
    active_role: UserRole = Depends(get_active_role),
) -> UserOut:
    """Return the current user — the app's session check on startup.

    - `200` → session valid, go to the home screen
    - `401 TOKEN_EXPIRED` → call `/auth/refresh`, then retry
    - `401` again → show the login screen

    `profile_complete` tells the app whether to route the user to a
    profile-completion screen first.

    Call this on app start and cache the result; it is not meant for every
    screen transition.
    """
    return UserOut.from_user(user, active_role=active_role)
