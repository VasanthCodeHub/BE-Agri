"""Authentication models: OTP requests and refresh tokens."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.modules.users.models import User, UserRole, user_role_enum


class OtpRequest(UUIDPrimaryKeyMixin, Base):
    """One row per OTP we issue.

    Not keyed to a user, because at request time the user may not exist yet —
    it is keyed to a phone number. This is also our record for rate limiting
    and for spotting abuse.
    """

    __tablename__ = "otp_requests"

    phone_e164: Mapped[str] = mapped_column(String(16), index=True)

    #: Argon2 hash of the code. The plain code is never stored: if this table
    #: leaked, the hashes are useless for logging in.
    code_hash: Mapped[str] = mapped_column(String(255))

    #: The role the user chose before the code was sent. Verification reads the
    #: role from HERE rather than from the verify request, so a client cannot
    #: request a code as USER and then verify as PROVIDER.
    requested_role: Mapped[UserRole] = mapped_column(user_role_enum)

    #: Wrong guesses so far. Once this hits the configured maximum the record is
    #: burned, so an attacker gets a handful of tries, not a million.
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    #: Set when the code is successfully used. A code works exactly once —
    #: without this, an OTP read over someone's shoulder stays valid until it
    #: expires.
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    @property
    def is_usable(self) -> bool:
        return self.consumed_at is None


class RefreshToken(UUIDPrimaryKeyMixin, Base):
    """A long-lived session token, stored hashed.

    Refresh tokens live in the database precisely so they CAN be revoked —
    that is what makes logout meaningful. A purely stateless token cannot be
    taken back once issued.
    """

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    #: SHA-256 of the token. Deterministic so we can look the row up by hash;
    #: safe because the token itself has ~384 bits of entropy.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    #: Rotation chain id. Every refresh issues a new token with the SAME
    #: family_id and revokes the old one. If a revoked token is ever presented
    #: again, someone copied it — so we revoke the whole family and force a
    #: fresh login. That turns silent token theft into a detectable event.
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)

    #: The role this session was opened as. Stored because /auth/refresh must
    #: mint a new access token with the same active role — otherwise a provider
    #: refreshing their session would silently land in the renter experience.
    active_role: Mapped[UserRole] = mapped_column(user_role_enum)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Helps a future "your logged-in devices" screen, and helps support
    #: recognise suspicious sessions.
    user_agent: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship()

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None
