"""Booking models."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.users.models import User, UserRoleAssignment
from app.modules.vehicles.models import Vehicle


class BookingStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class SessionBlock(StrEnum):
    MORNING = "MORNING"       # 06:00–12:00
    AFTERNOON = "AFTERNOON"   # 12:00–16:00
    EVENING = "EVENING"       # 16:00–20:00
    FULL_DAY = "FULL_DAY"     # 06:00–20:00


booking_status_enum = Enum(BookingStatus, name="booking_status", metadata=Base.metadata)
session_block_enum = Enum(SessionBlock, name="session_block", metadata=Base.metadata)


_SESSION_DEFAULTS = {
    SessionBlock.MORNING: 4,
    SessionBlock.AFTERNOON: 4,
    SessionBlock.EVENING: 4,
    SessionBlock.FULL_DAY: 8,
}


class Booking(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (
        Index("ix_bookings_provider", "provider_user_id", "created_at"),
        Index("ix_bookings_renter", "renter_user_id", "created_at"),
        CheckConstraint("duration_hours BETWEEN 1 AND 24", name="duration_hours_valid"),
        CheckConstraint("amount_paise > 0", name="amount_paise_positive"),
    )

    # The vehicle being booked
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), index=True
    )
    # The renter making the request
    renter_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Provider (denormalised for fast listing queries without a join)
    provider_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    status: Mapped[BookingStatus] = mapped_column(
        booking_status_enum, default=BookingStatus.PENDING, server_default=BookingStatus.PENDING.value
    )
    booking_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    session: Mapped[SessionBlock] = mapped_column(session_block_enum)
    duration_hours: Mapped[int] = mapped_column(Integer)
    amount_paise: Mapped[int] = mapped_column(Integer)
    renter_note: Mapped[str | None] = mapped_column(Text, default=None)
    provider_note: Mapped[str | None] = mapped_column(Text, default=None)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    reference: Mapped[str] = mapped_column(String(20), unique=True, index=True)

    vehicle: Mapped[Vehicle] = relationship(lazy="selectin")
    renter: Mapped[User] = relationship(foreign_keys=[renter_user_id], lazy="selectin")
    provider: Mapped[User] = relationship(foreign_keys=[provider_user_id], lazy="selectin")

    @property
    def duration_label(self) -> str:
        if self.session == SessionBlock.FULL_DAY:
            return "Full day"
        hours = self.duration_hours
        if hours == 1:
            return "1 hour"
        return f"{hours} hours"

    @property
    def amount_rupees(self) -> float:
        return round(self.amount_paise / 100.0, 2)