"""User identity models.

Two tables:

- `users` — one row per phone number. The phone number is the identity.
- `user_roles` — which roles that user holds. A separate table, not a column
  on `users`, because one person can be both a renter and a provider.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class UserRole(StrEnum):
    """Roles a user can hold.

    `USER` is the normal consumer of the app — browsing vehicles, favouriting,
    reviewing, calling providers. `PROVIDER` is a vehicle owner. One phone
    number can hold both roles.

    ADMIN is deliberately absent for now. When it returns (Phase 5, provider
    verification) admins will be created by script — never assignable through
    the public login endpoint, which would be privilege escalation.
    """

    USER = "USER"
    PROVIDER = "PROVIDER"


class UserStatus(StrEnum):
    ACTIVE = "ACTIVE"
    #: Blocked by an admin. Kept rather than deleted, so history and audit
    #: trails survive and the phone number cannot be re-registered.
    SUSPENDED = "SUSPENDED"


#: Postgres enum types, bound to the metadata so each is created exactly once
#: in the migration even though several tables reference them.
user_role_enum = Enum(UserRole, name="user_role", metadata=Base.metadata)
user_status_enum = Enum(UserStatus, name="user_status", metadata=Base.metadata)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    #: E.164, e.g. +919876543210. Unique — this is the identity.
    phone_e164: Mapped[str] = mapped_column(String(16), unique=True, index=True)

    #: Captured at first OTP verification. Nullable because a user exists the
    #: moment their phone is verified, which is before any profile work.
    full_name: Mapped[str | None] = mapped_column(String(120))

    #: Registration/profile details the app collects but which are not the
    #: identity — the phone number is. All nullable: a user exists after OTP
    #: verification alone.
    email: Mapped[str | None] = mapped_column(String(254))
    address: Mapped[str | None] = mapped_column(String(255))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    status: Mapped[UserStatus] = mapped_column(
        user_status_enum, default=UserStatus.ACTIVE, server_default=UserStatus.ACTIVE.value
    )

    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: lazy="selectin" loads the roles in a second query whenever a User is
    #: loaded. This matters in async code: the default ("lazy load on first
    #: access") would try to hit the database while rendering a response, which
    #: raises MissingGreenlet. Eager-loading here avoids that entirely and also
    #: prevents N+1 queries.
    role_assignments: Mapped[list[UserRoleAssignment]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def roles(self) -> list[str]:
        """Role names, sorted for stable output."""
        return sorted(assignment.role.value for assignment in self.role_assignments)

    def has_role(self, role: UserRole) -> bool:
        return any(assignment.role is role for assignment in self.role_assignments)

    @property
    def is_active(self) -> bool:
        return self.status is UserStatus.ACTIVE


class UserRoleAssignment(UUIDPrimaryKeyMixin, Base):
    """One row per role a user holds."""

    __tablename__ = "user_roles"
    __table_args__ = (
        # The database guarantees a user cannot hold the same role twice —
        # rather than trusting every code path to remember to check.
        UniqueConstraint("user_id", "role"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[UserRole] = mapped_column(user_role_enum)

    #: When the role was granted — useful for support ("when did they become a
    #: provider?") and for auditing.
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="role_assignments")
