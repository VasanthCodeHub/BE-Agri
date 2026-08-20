"""Booking business logic."""

from __future__ import annotations

import random
import uuid
from datetime import date, datetime as dt, timezone
from typing import TYPE_CHECKING, Protocol

from app.core.config import Settings
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.core.logging import get_logger
from app.modules.bookings.models import (
    BookingStatus,
    SessionBlock,
)
from app.modules.bookings.repository import BookingRepository
from app.modules.bookings.schemas import (
    AvailabilityResponse,
    BookingCreateIn,
    BookingOut,
    BookingPage,
    BookingUpdateIn,
)
from app.modules.notifications.models import NotificationType
from app.modules.vehicles.models import ListingStatus, PriceUnit, Vehicle
from app.modules.vehicles.repository import VehicleRepository

if TYPE_CHECKING:
    from app.modules.users.models import User


class Notifier(Protocol):
    """Anything that can create a notification.

    A Protocol keeps BookingService decoupled from the notifications module:
    tests can pass a stub, and the production wiring passes a
    NotificationService that shares this request's database session.
    """

    async def notify(
        self,
        user_id: uuid.UUID,
        *,
        type: NotificationType,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> object:
        ...

log = get_logger(__name__)

# State transitions allowed in the MVP
_ALLOWED_TRANSITIONS: dict[BookingStatus, set[BookingStatus]] = {
    BookingStatus.PENDING: {BookingStatus.ACCEPTED, BookingStatus.REJECTED, BookingStatus.CANCELLED},
    BookingStatus.ACCEPTED: {BookingStatus.ACTIVE, BookingStatus.CANCELLED},
    BookingStatus.ACTIVE: {BookingStatus.COMPLETED, BookingStatus.CANCELLED},
    BookingStatus.REJECTED: set(),
    BookingStatus.COMPLETED: set(),
    BookingStatus.CANCELLED: set(),
}

#: A "day" of rental is 8 working hours — the FULL_DAY session. Shorter
#: sessions are billed as a fraction of that day.
_DAY_WORKING_HOURS = 8


def _status_notification_type(status: BookingStatus) -> NotificationType:
    return {
        BookingStatus.ACCEPTED: NotificationType.BOOKING_ACCEPTED,
        BookingStatus.REJECTED: NotificationType.BOOKING_REJECTED,
        BookingStatus.ACTIVE: NotificationType.BOOKING_ACCEPTED,
        BookingStatus.COMPLETED: NotificationType.BOOKING_ACCEPTED,
    }.get(status, NotificationType.BOOKING_ACCEPTED)


def _status_notification_text(
    old_status: BookingStatus, new_status: BookingStatus, reference: str
) -> tuple[str | None, str]:
    """(title, body) for a booking status change, or (None, ...) to skip."""
    if new_status is BookingStatus.ACCEPTED:
        return f"Booking {reference} accepted", "The provider accepted your booking."
    if new_status is BookingStatus.REJECTED:
        return f"Booking {reference} rejected", "The provider could not accept your booking."
    if new_status is BookingStatus.ACTIVE:
        return f"Booking {reference} started", "Your rental is now active."
    if new_status is BookingStatus.COMPLETED:
        return f"Booking {reference} completed", "Your rental is complete. Rate the vehicle."
    return (None, "")


def _compute_amount(
    *, price_amount: int, price_unit: PriceUnit, duration_hours: int
) -> int:
    """The booking charge, derived from the vehicle's price unit.

    The old formula divided by the session's own default duration, so
    `price × 4h / 4h` always cancelled back to the bare price — every
    session cost the same regardless of length. Now:

    - **HOUR** vehicles charge per hour: 4h costs 4× the hourly rate.
    - **DAY** vehicles charge a fraction of the daily rate.
    - **ACRE / TRIP** are per unit of work, not per hour, so the session is a
      single charge at the advertised rate.
    """
    if price_unit is PriceUnit.HOUR:
        return price_amount * duration_hours
    if price_unit is PriceUnit.DAY:
        return int(round(price_amount * duration_hours / _DAY_WORKING_HOURS))
    return price_amount


class BookingService:
    def __init__(
        self,
        *,
        repo: BookingRepository,
        repo_vehicles: VehicleRepository,
        settings: Settings,
        notifier: Notifier | None = None,
    ) -> None:
        self.repo = repo
        self.repo_vehicles = repo_vehicles
        self.settings = settings
        self.notifier = notifier

    # -----------------------------------------------------------------------
    # Availability
    # -----------------------------------------------------------------------
    async def check_availability(self, *, vehicle_id: uuid.UUID, date_: date) -> AvailabilityResponse:
        vehicle = await self.repo_vehicles.get_public_by_id(vehicle_id)
        if vehicle is None:
            raise NotFoundError("Vehicle not found or not available.", code="VEHICLE_NOT_FOUND")

        existing = await self.repo.get_bookings_for_vehicle_on_date(
            vehicle_id=vehicle_id,
            booking_date=date_,
            exclude_statuses=[BookingStatus.CANCELLED],
        )
        booked_sessions = {b.session for b in existing}

        slots = []
        for block in SessionBlock:
            slots.append({
                "date": date_.isoformat(),
                "session": block.value,
                "label": self._session_label(block),
                "available": block not in booked_sessions,
                "booking_id": next(
                    (str(b.id) for b in existing if b.session == block), None
                ),
            })

        return AvailabilityResponse(
            vehicle_id=vehicle_id,
            date=date_,
            slots=slots,
        )

    # -----------------------------------------------------------------------
    # Create booking
    # -----------------------------------------------------------------------
    async def create_booking(
        self, *, renter: "User", payload: BookingCreateIn
    ) -> BookingOut:
        vehicle = await self.repo_vehicles.get_public_by_id(payload.vehicle_id)
        if vehicle is None:
            raise NotFoundError("Vehicle not found or not available.", code="VEHICLE_NOT_FOUND")
        if not vehicle.is_available:
            raise BadRequestError(
                "This vehicle is currently marked unavailable.",
                code="VEHICLE_NOT_AVAILABLE",
            )

        # Double-booking guard: same vehicle, same date, same session, not cancelled
        existing = await self.repo.get_bookings_for_vehicle_on_date(
            vehicle_id=payload.vehicle_id,
            booking_date=payload.booking_date,
            exclude_statuses=[BookingStatus.CANCELLED],
        )
        conflict = [b for b in existing if b.session == payload.session]
        if conflict:
            raise ConflictError(
                f"This vehicle is already booked for {payload.session.value} on {payload.booking_date}.",
                code="SLOT_ALREADY_BOOKED",
                details={"booking_id": str(conflict[0].id)},
            )

        # Reference: AGR-XXXXX (5 random digits)
        reference = self._generate_reference()

        amount_paise = _compute_amount(
            price_amount=vehicle.price_amount,
            price_unit=vehicle.price_unit,
            duration_hours=payload.duration_hours,
        )

        booking = Booking(
            vehicle_id=payload.vehicle_id,
            renter_user_id=renter.id,
            provider_user_id=vehicle.provider_user_id,
            booking_date=dt.combine(payload.booking_date, dt.min.time()).replace(tzinfo=timezone.utc),
            session=payload.session,
            duration_hours=payload.duration_hours,
            amount_paise=amount_paise,
            renter_note=payload.renter_note,
            reference=reference,
        )
        booking = await self.repo.create(booking=booking)

        log.info(
            "booking_created",
            booking_id=str(booking.id),
            vehicle_id=str(vehicle.id),
            renter_id=str(renter.id),
            reference=reference,
        )

        if self.notifier is not None:
            await self.notifier.notify(
                vehicle.provider_user_id,
                type=NotificationType.BOOKING_REQUEST,
                title="New booking request",
                body=f"{renter.full_name or 'A renter'} wants to book "
                f"{vehicle.name} for {payload.booking_date} ({payload.session.value}).",
                data={
                    "booking_id": str(booking.id),
                    "vehicle_id": str(vehicle.id),
                    "reference": reference,
                },
            )
        return BookingOut.from_model(booking)

    # -----------------------------------------------------------------------
    # Read bookings
    # -----------------------------------------------------------------------
    async def get_booking(self, *, booking_id: uuid.UUID, user: "User") -> BookingOut:
        booking = await self.repo.get_by_id(booking_id)
        if booking is None:
            raise NotFoundError("Booking not found.", code="BOOKING_NOT_FOUND")
        self._check_access(booking, user)
        return BookingOut.from_model(booking)

    async def list_my_bookings(
        self, *, renter: "User", status: str | None, limit: int, offset: int
    ) -> BookingPage:
        rows, total = await self.repo.get_renter_bookings(
            renter_user_id=renter.id, status=status, limit=limit, offset=offset
        )
        return BookingPage(
            items=[BookingOut.from_model(b) for b in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def list_incoming_bookings(
        self, *, provider: "User", status: str | None, limit: int, offset: int
    ) -> BookingPage:
        rows, total = await self.repo.get_provider_bookings(
            provider_user_id=provider.id, status=status, limit=limit, offset=offset
        )
        return BookingPage(
            items=[BookingOut.from_model(b) for b in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    # -----------------------------------------------------------------------
    # Status transitions (provider)
    # -----------------------------------------------------------------------
    async def update_booking_status(
        self,
        *,
        booking_id: uuid.UUID,
        provider: "User",
        payload: BookingUpdateIn,
    ) -> BookingOut:
        booking = await self.repo.get_provider_booking(
            booking_id=booking_id, provider_user_id=provider.id
        )
        if booking is None:
            raise NotFoundError("Booking not found or not yours.", code="BOOKING_NOT_FOUND")

        old_status = booking.status
        new_status = payload.status

        allowed = _ALLOWED_TRANSITIONS.get(old_status, set())
        if new_status not in allowed:
            raise BadRequestError(
                f"Cannot transition from {old_status.value} to {new_status.value}. "
                f"Allowed: {sorted(s.value for s in allowed)}.",
                code="INVALID_STATUS_TRANSITION",
                details={"current": old_status.value, "requested": new_status.value},
            )

        booking = await self.repo.update_status(
            booking=booking, status=new_status, provider_note=payload.provider_note
        )
        log.info(
            "booking_status_changed",
            booking_id=str(booking.id),
            old=old_status.value,
            new=new_status.value,
        )

        if self.notifier is not None:
            title, body = _status_notification_text(
                old_status, new_status, booking.reference
            )
            if title:
                await self.notifier.notify(
                    booking.renter_user_id,
                    type=_status_notification_type(new_status),
                    title=title,
                    body=body,
                    data={"booking_id": str(booking.id), "reference": booking.reference},
                )
        return BookingOut.from_model(booking)

    # -----------------------------------------------------------------------
    # Cancel (renter)
    # -----------------------------------------------------------------------
    async def cancel_booking(self, *, booking_id: uuid.UUID, renter: "User") -> None:
        booking = await self.repo.get_renter_booking(
            booking_id=booking_id, renter_user_id=renter.id
        )
        if booking is None:
            raise NotFoundError("Booking not found or not yours.", code="BOOKING_NOT_FOUND")
        if booking.status not in (BookingStatus.PENDING, BookingStatus.ACCEPTED):
            raise ForbiddenError(
                f"Cannot cancel a booking with status {booking.status.value}. "
                "Only PENDING or ACCEPTED bookings can be cancelled.",
                code="CANCEL_NOT_ALLOWED",
            )
        await self.repo.cancel(booking=booking)
        log.info("booking_cancelled", booking_id=str(booking.id))

        if self.notifier is not None:
            await self.notifier.notify(
                booking.provider_user_id,
                type=NotificationType.BOOKING_CANCELLED,
                title="Booking cancelled",
                body=f"Booking {booking.reference} was cancelled by the renter.",
                data={"booking_id": str(booking.id), "reference": booking.reference},
            )

    async def _own_renter_booking(
        self, *, renter: "User", booking_id: uuid.UUID
    ) -> Booking:
        booking = await self.repo.get_renter_booking(
            booking_id=booking_id, renter_user_id=renter.id
        )
        if booking is None:
            raise NotFoundError("Booking not found or not yours.", code="BOOKING_NOT_FOUND")
        return booking

    async def _own_provider_booking(
        self, *, provider: "User", booking_id: uuid.UUID
    ) -> Booking:
        booking = await self.repo.get_provider_booking(
            booking_id=booking_id, provider_user_id=provider.id
        )
        if booking is None:
            raise NotFoundError("Booking not found or not yours.", code="BOOKING_NOT_FOUND")
        return booking

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------
    @staticmethod
    def _check_access(booking: Booking, user: "User") -> None:
        if user.id not in (booking.renter_user_id, booking.provider_user_id):
            raise ForbiddenError(
                "You are not a party to this booking.",
                code="BOOKING_ACCESS_DENIED",
            )

    @staticmethod
    def _session_label(block: SessionBlock) -> str:
        return {
            SessionBlock.MORNING: "Morning (06:00–12:00)",
            SessionBlock.AFTERNOON: "Afternoon (12:00–16:00)",
            SessionBlock.EVENING: "Evening (16:00–20:00)",
            SessionBlock.FULL_DAY: "Full day (06:00–20:00)",
        }.get(block, block.value)

    @staticmethod
    def _generate_reference() -> str:
        for _ in range(10):
            ref = f"AGR-{random.randint(0, 99999):05d}"
            return ref  # probationary: collisions improbable at low volume
        raise ConflictError("Could not generate a unique booking reference.", code="REFERENCE_COLLISION")