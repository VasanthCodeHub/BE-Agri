"""Data access for authentication.

The repository layer owns every query. The service layer above it never writes
SQL, and this layer never makes decisions — it fetches, inserts and updates.

Why bother separating them: the service becomes testable without a database,
and when a query needs optimising (an index, an eager load, a rewrite) you
change it here without touching business rules.

Note none of these methods commit. Committing is the job of the `get_db`
dependency, which wraps the whole request in one transaction — so a request
that fails halfway leaves nothing half-written.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import utc_now
from app.modules.auth.models import OtpRequest, RefreshToken
from app.modules.users.models import User, UserRole, UserRoleAssignment


class AuthRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def commit(self) -> None:
        """Commit now, instead of leaving it to the end of the request.

        Normally committing is `get_db`'s job: one transaction per request,
        rolled back if the handler raises. That is correct for ordinary writes.

        But a few security writes happen *and then deliberately raise an error*
        — recording a failed OTP attempt, revoking a stolen token family. Those
        must survive the error response. Without this, the rollback discards
        them and the protection silently does nothing.

        Use this only for that case, and only right before raising.
        """
        await self.db.commit()

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------
    async def get_user_by_phone(self, phone_e164: str) -> User | None:
        result = await self.db.execute(select(User).where(User.phone_e164 == phone_e164))
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def create_user(self, *, phone_e164: str, full_name: str | None, role: UserRole) -> User:
        """Create a user with their first role.

        Called only after the OTP has been verified, so every row in `users`
        represents a phone number someone has proved they control.
        """
        user = User(
            phone_e164=phone_e164,
            full_name=full_name,
            phone_verified_at=utc_now(),
            last_login_at=utc_now(),
        )
        user.role_assignments.append(UserRoleAssignment(role=role))
        self.db.add(user)
        # flush (not commit) sends the INSERT now so the generated id and the
        # relationship are usable within this same request.
        await self.db.flush()
        return user

    async def grant_role(self, user: User, role: UserRole) -> None:
        """Give a user an additional role.

        This is what lets one phone number be both renter and provider. Holding
        the PROVIDER role grants nothing on its own — discovery still requires
        a provider profile and admin verification.
        """
        if user.has_role(role):
            return
        self.db.add(UserRoleAssignment(user_id=user.id, role=role))
        await self.db.flush()
        await self.db.refresh(user, attribute_names=["role_assignments"])

    async def touch_login(self, user: User) -> None:
        user.last_login_at = utc_now()
        if user.phone_verified_at is None:
            user.phone_verified_at = utc_now()
        await self.db.flush()

    async def set_name(self, user: User, full_name: str) -> None:
        user.full_name = full_name
        await self.db.flush()

    # -----------------------------------------------------------------------
    # OTP requests
    # -----------------------------------------------------------------------
    async def create_otp(
        self,
        *,
        phone_e164: str,
        code_hash: str,
        requested_role: UserRole,
        ttl_seconds: int,
    ) -> OtpRequest:
        otp = OtpRequest(
            phone_e164=phone_e164,
            code_hash=code_hash,
            requested_role=requested_role,
            expires_at=utc_now() + timedelta(seconds=ttl_seconds),
        )
        self.db.add(otp)
        await self.db.flush()
        return otp

    async def get_active_otp(self, phone_e164: str) -> OtpRequest | None:
        """The most recent unconsumed, unexpired OTP for this number.

        Ordered newest-first: if a user requested a second code, that is the one
        they are looking at, so an older code should not unlock the account.
        """
        result = await self.db.execute(
            select(OtpRequest)
            .where(
                OtpRequest.phone_e164 == phone_e164,
                OtpRequest.consumed_at.is_(None),
                OtpRequest.expires_at > utc_now(),
            )
            .order_by(OtpRequest.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_otps_since(self, phone_e164: str, since: datetime) -> int:
        """How many codes this number has requested since `since`.

        Not used for enforcement yet — rate limiting arrives with production
        (ADR-010) — but the data is recorded from day one so abuse is visible.
        """
        result = await self.db.execute(
            select(OtpRequest).where(
                OtpRequest.phone_e164 == phone_e164,
                OtpRequest.created_at >= since,
            )
        )
        return len(result.scalars().all())

    async def record_failed_attempt(self, otp: OtpRequest) -> None:
        otp.attempts += 1
        await self.db.flush()

    async def consume_otp(self, otp: OtpRequest) -> None:
        """Mark a code as used. A code works exactly once."""
        otp.consumed_at = utc_now()
        await self.db.flush()

    async def invalidate_otps_for_phone(self, phone_e164: str) -> None:
        """Burn every outstanding code for a number.

        Called when the attempt limit is hit, so an attacker cannot keep
        guessing against the same code.
        """
        await self.db.execute(
            update(OtpRequest)
            .where(
                OtpRequest.phone_e164 == phone_e164,
                OtpRequest.consumed_at.is_(None),
            )
            .values(consumed_at=utc_now())
        )
        await self.db.flush()

    # -----------------------------------------------------------------------
    # Refresh tokens
    # -----------------------------------------------------------------------
    async def create_refresh_token(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        family_id: uuid.UUID,
        active_role: UserRole,
        ttl_days: int,
        user_agent: str | None,
    ) -> RefreshToken:
        token = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            family_id=family_id,
            active_role=active_role,
            expires_at=utc_now() + timedelta(days=ttl_days),
            user_agent=(user_agent or "")[:255] or None,
        )
        self.db.add(token)
        await self.db.flush()
        return token

    async def get_refresh_token(self, token_hash: str) -> RefreshToken | None:
        result = await self.db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def revoke_token(self, token: RefreshToken) -> None:
        token.revoked_at = utc_now()
        await self.db.flush()

    async def revoke_family(self, family_id: uuid.UUID) -> int:
        """Revoke every token in a rotation chain.

        Called when a already-used refresh token is presented again — the sign
        that a token was copied. Killing the family forces a fresh login, which
        locks out whoever stole it.
        """
        result = await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )
        await self.db.flush()
        # An UPDATE returns a CursorResult, which carries rowcount. The declared
        # return type of execute() is the broader Result, hence the cast.
        return cast("CursorResult[Any]", result).rowcount or 0

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> int:
        """Log the user out of every device."""
        result = await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )
        await self.db.flush()
        # An UPDATE returns a CursorResult, which carries rowcount. The declared
        # return type of execute() is the broader Result, hence the cast.
        return cast("CursorResult[Any]", result).rowcount or 0
