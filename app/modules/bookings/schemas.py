from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.bookings.models import BookingStatus, SessionBlock


class SessionBlockOut(BaseModel):
    block: SessionBlock
    label: str
    available: bool


class BookingCreateIn(BaseModel):
    vehicle_id: uuid.UUID
    booking_date: date
    session: SessionBlock
    duration_hours: int = Field(gt=0, le=24)
    renter_note: str | None = Field(default=None, max_length=500)

    @field_validator("duration_hours")
    @classmethod
    def set_duration_defaults(cls, value: int, info) -> int:
        session = info.data.get("session")
        if session is None:
            return value
        defaults = {
            SessionBlock.MORNING: 4,
            SessionBlock.AFTERNOON: 4,
            SessionBlock.EVENING: 4,
            SessionBlock.FULL_DAY: 8,
        }
        expected = defaults.get(session, 4)
        if value != expected:
            raise ValueError(
                f"Expected duration of {expected} hours for {session.value} session."
            )
        return value


class BookingUpdateIn(BaseModel):
    status: BookingStatus
    provider_note: str | None = Field(default=None, max_length=300)


class BookingOut(BaseModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID
    renter_user_id: uuid.UUID
    provider_user_id: uuid.UUID
    status: BookingStatus
    booking_date: date
    session: SessionBlock
    duration_hours: int
    duration_label: str
    amount_paise: int
    amount_rupees: float
    renter_note: str | None
    provider_note: str | None
    reference: str
    created_at: datetime
    updated_at: datetime
    cancelled_at: datetime | None

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "3f2a...",
                "vehicle_id": "abc...",
                "renter_user_id": "def...",
                "provider_user_id": "ghi...",
                "status": "PENDING",
                "booking_date": "2025-01-15",
                "session": "MORNING",
                "duration_hours": 4,
                "duration_label": "4 hours",
                "amount_paise": 20000,
                "amount_rupees": 200.0,
                "renter_note": "Need it by 8am.",
                "provider_note": None,
                "reference": "AGR-12345",
                "created_at": "2025-01-10T10:00:00Z",
                "updated_at": "2025-01-10T10:00:00Z",
                "cancelled_at": None,
            }
        },
    )

    @classmethod
    def from_model(cls, booking: Booking) -> BookingOut:
        return cls(
            id=booking.id,
            vehicle_id=booking.vehicle_id,
            renter_user_id=booking.renter_user_id,
            provider_user_id=booking.provider_user_id,
            status=booking.status,
            booking_date=booking.booking_date,
            session=booking.session,
            duration_hours=booking.duration_hours,
            duration_label=booking.duration_label,
            amount_paise=booking.amount_paise,
            amount_rupees=booking.amount_rupees,
            renter_note=booking.renter_note,
            provider_note=booking.provider_note,
            reference=booking.reference,
            created_at=booking.created_at,
            updated_at=booking.updated_at,
            cancelled_at=booking.cancelled_at,
        )


class BookingPage(BaseModel):
    items: list[BookingOut]
    total: int
    limit: int
    offset: int


class AvailabilitySlot(BaseModel):
    date: date
    session: SessionBlock
    label: str
    is_booked: bool
    booking_id: uuid.UUID | None


class AvailabilityResponse(BaseModel):
    vehicle_id: uuid.UUID
    date: date
    slots: list[AvailabilitySlot]