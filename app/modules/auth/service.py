"""Authentication business logic.

Every rule about logging in lives here: when a user is created, when a role is
granted, how many wrong codes are tolerated, when a session is revoked.

The router above knows only HTTP. The repository below knows only SQL. This
layer is the part worth reading to understand how login actually works — and
the part worth testing hardest.
"""

from __future__ import annotations

import uuid

from app.core.config import Settings
from app.core.exceptions import (
    BadRequestError,
    ForbiddenError,
    UnauthorizedError,
)
from app.core.logging import get_logger
from app.core.phone import mask_phone
from app.core.security import (
    create_access_token,
    generate_otp,
    generate_refresh_token,
    hash_otp,
    hash_refresh_token,
    matches_dev_bypass,
    utc_now,
    verify_otp,
)
from app.integrations.sms.base import SmsProvider
from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import (
    LoginOut,
    OtpRequestOut,
    TokenPairOut,
    UserOut,
)
from app.modules.users.models import User, UserRole

log = get_logger(__name__)


class AuthService:
    def __init__(
        self,
        *,
        repo: AuthRepository,
        sms: SmsProvider,
        settings: Settings,
    ) -> None:
        self.repo = repo
        self.sms = sms
        self.settings = settings

    # -----------------------------------------------------------------------
    # Step 1 — request an OTP
    # -----------------------------------------------------------------------
    async def request_otp(self, *, phone_e164: str, role: UserRole) -> OtpRequestOut:
        """Issue and send a one-time code.

        Also tells the app whether this is a new number, so it knows whether to
        collect a name before verifying.
        """
        user = await self.repo.get_user_by_phone(phone_e164)
        is_new_user = user is None

        if user is not None and not user.is_active:
            # A suspended user should not be able to start a session at all.
            raise ForbiddenError(
                "This account has been suspended. Please contact support.",
                code="ACCOUNT_SUSPENDED",
            )

        code = generate_otp(self.settings.otp_length)
        await self.repo.create_otp(
            phone_e164=phone_e164,
            code_hash=hash_otp(code),
            requested_role=role,
            ttl_seconds=self.settings.otp_ttl_seconds,
        )

        # Delivery is the adapter's problem. Locally this prints to the
        # terminal; in production it will hit a real gateway.
        await self.sms.send_otp(phone_e164=phone_e164, code=code)

        log.info(
            "otp_requested",
            phone=phone_e164,
            role=role.value,
            is_new_user=is_new_user,
        )

        return OtpRequestOut(
            phone=mask_phone(phone_e164),
            is_new_user=is_new_user,
            # Ask for a name if we don't have one yet — covers both a brand new
            # number and an older account that never supplied one.
            name_required=is_new_user or not (user and user.full_name),
            otp_sent=True,
            expires_in=self.settings.otp_ttl_seconds,
        )

    # -----------------------------------------------------------------------
    # Step 2 — verify the OTP and open a session
    # -----------------------------------------------------------------------
    async def verify_otp(
        self,
        *,
        phone_e164: str,
        code: str,
        name: str | None,
        user_agent: str | None,
    ) -> LoginOut:
        otp = await self.repo.get_active_otp(phone_e164)
        if otp is None:
            raise BadRequestError(
                "No active code for this number. Please request a new one.",
                code="OTP_NOT_FOUND",
            )

        used_bypass = matches_dev_bypass(code, self.settings.otp_dev_bypass_code)

        if used_bypass:
            # Local testing only. Config refuses to start production with a
            # bypass code set, so this branch cannot exist there.
            log.warning(
                "otp_dev_bypass_used",
                phone=phone_e164,
                note="Development bypass code accepted. Never enabled in production.",
            )
        else:
            if otp.attempts >= self.settings.otp_max_attempts:
                await self.repo.invalidate_otps_for_phone(phone_e164)
                # Commit before raising — the error response would otherwise
                # roll this back. See AuthRepository.commit().
                await self.repo.commit()
                raise BadRequestError(
                    "Too many incorrect attempts. Please request a new code.",
                    code="OTP_ATTEMPTS_EXCEEDED",
                )

            if not verify_otp(code, otp.code_hash):
                await self.repo.record_failed_attempt(otp)
                remaining = max(self.settings.otp_max_attempts - otp.attempts, 0)
                if remaining == 0:
                    await self.repo.invalidate_otps_for_phone(phone_e164)
                # The whole point of counting attempts is that the count
                # persists. Without this commit the rollback discards it and an
                # attacker gets unlimited guesses at a 6-digit code.
                await self.repo.commit()
                log.info("otp_verify_failed", phone=phone_e164, remaining_attempts=remaining)
                raise BadRequestError(
                    "That code is not correct.",
                    code="OTP_INVALID",
                    details={"remaining_attempts": remaining},
                )

        # A code works exactly once.
        await self.repo.consume_otp(otp)

        # The role comes from the OTP record, NOT from this request — so a
        # client cannot request a code as RENTER and verify as PROVIDER.
        role = otp.requested_role

        user = await self.repo.get_user_by_phone(phone_e164)
        is_new_user = user is None

        if user is None:
            if not name:
                raise BadRequestError(
                    "Please provide your name to complete registration.",
                    code="NAME_REQUIRED",
                )
            user = await self.repo.create_user(phone_e164=phone_e164, full_name=name, role=role)
            log.info("user_registered", user_id=str(user.id), role=role.value)
        else:
            if not user.is_active:
                raise ForbiddenError(
                    "This account has been suspended. Please contact support.",
                    code="ACCOUNT_SUSPENDED",
                )
            # One phone number can hold both roles. Selecting a role the user
            # does not have yet simply grants it — the role alone confers no
            # privileges, since being a discoverable provider still requires a
            # profile and admin verification.
            if not user.has_role(role):
                await self.repo.grant_role(user, role)
                log.info("role_granted", user_id=str(user.id), role=role.value)
            if name and not user.full_name:
                await self.repo.set_name(user, name)
            await self.repo.touch_login(user)

        tokens = await self._issue_session(user=user, role=role, user_agent=user_agent)

        log.info(
            "login_succeeded",
            user_id=str(user.id),
            role=role.value,
            is_new_user=is_new_user,
            dev_bypass=used_bypass,
        )

        return LoginOut(
            **tokens.model_dump(),
            is_new_user=is_new_user,
            user=UserOut.from_user(user, active_role=role),
        )

    # -----------------------------------------------------------------------
    # Refresh — trade a refresh token for a fresh pair
    # -----------------------------------------------------------------------
    async def refresh_session(self, *, refresh_token: str, user_agent: str | None) -> TokenPairOut:
        stored = await self.repo.get_refresh_token(hash_refresh_token(refresh_token))

        if stored is None:
            raise UnauthorizedError("Invalid session. Please log in again.", code="TOKEN_INVALID")

        if stored.is_revoked:
            # This token was already exchanged. Presenting it again means a copy
            # exists, so we kill the entire rotation chain — whoever holds the
            # stolen copy is locked out, and the real user simply logs in again.
            revoked = await self.repo.revoke_family(stored.family_id)
            # Commit before raising: this revocation is the entire security
            # response to a stolen token, and the error response would roll it
            # back. See AuthRepository.commit().
            await self.repo.commit()
            log.warning(
                "refresh_token_reuse_detected",
                user_id=str(stored.user_id),
                family_id=str(stored.family_id),
                sessions_revoked=revoked,
            )
            raise UnauthorizedError(
                "This session is no longer valid. Please log in again.",
                code="TOKEN_REUSED",
            )

        if stored.expires_at <= utc_now():
            raise UnauthorizedError(
                "Your session has expired. Please log in again.", code="TOKEN_EXPIRED"
            )

        user = await self.repo.get_user_by_id(stored.user_id)
        if user is None or not user.is_active:
            raise UnauthorizedError("Invalid session. Please log in again.", code="TOKEN_INVALID")

        # Rotation: retire the old token, issue a new one in the same family.
        await self.repo.revoke_token(stored)
        tokens = await self._issue_session(
            user=user,
            role=stored.active_role,
            user_agent=user_agent,
            family_id=stored.family_id,
        )
        log.info("session_refreshed", user_id=str(user.id))
        return tokens

    # -----------------------------------------------------------------------
    # Logout
    # -----------------------------------------------------------------------
    async def logout(self, *, refresh_token: str, all_devices: bool) -> None:
        """Revoke the session.

        Deliberately silent about whether the token existed: responding
        differently would let a caller probe which tokens are valid.

        The access token stays usable for its remaining few minutes. Killing it
        instantly would require a database check on every request, which is the
        cost the short expiry exists to avoid.
        """
        stored = await self.repo.get_refresh_token(hash_refresh_token(refresh_token))
        if stored is None:
            return

        if all_devices:
            count = await self.repo.revoke_all_for_user(stored.user_id)
            log.info("logout_all_devices", user_id=str(stored.user_id), sessions_revoked=count)
            return

        if not stored.is_revoked:
            await self.repo.revoke_token(stored)
        log.info("logout", user_id=str(stored.user_id))

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------
    async def _issue_session(
        self,
        *,
        user: User,
        role: UserRole,
        user_agent: str | None,
        family_id: uuid.UUID | None = None,
    ) -> TokenPairOut:
        """Mint an access token and a stored refresh token."""
        access_token, expires_in = create_access_token(
            user_id=user.id,
            roles=user.roles,
            active_role=role.value,
            settings=self.settings,
        )

        raw_refresh = generate_refresh_token()
        await self.repo.create_refresh_token(
            user_id=user.id,
            token_hash=hash_refresh_token(raw_refresh),
            family_id=family_id or uuid.uuid4(),
            active_role=role,
            ttl_days=self.settings.refresh_token_ttl_days,
            user_agent=user_agent,
        )

        return TokenPairOut(
            access_token=access_token,
            refresh_token=raw_refresh,
            expires_in=expires_in,
            refresh_expires_in=self.settings.refresh_token_ttl_days * 24 * 60 * 60,
        )
