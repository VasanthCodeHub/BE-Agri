"""Booking endpoints."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.core.logging import get_logger
from app.db.session import get_db
from app.modules.auth.dependencies import get_current_user, require_role
from app.modules.users.models import User, UserRole
from app.modules.vehicles.repository import VehicleRepository
from app.modules.bookings.models import Booking, BookingStatus, SessionBlock
from app.modules.bookings.repository import BookingRepository
from app.modules.bookings.schemas import (
    AvailabilityResponse,
    BookingCreateIn,
    BookingOut,
    BookingPage,
    BookingUpdateIn,
)
from app.modules.bookings.service import BookingService
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.service import NotificationService

log = get_logger(__name__)
router = APIRouter()


def get_booking_service(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BookingService:
    return BookingService(
        repo=BookingRepository(db),
        repo_vehicles=VehicleRepository(db),
        settings=settings,
        notifier=NotificationService(repo=NotificationRepository(db)),
    )


# ---------------------------------------------------------------------------
# Availability check (public — anyone can check slot availability)
# ---------------------------------------------------------------------------
@router.get(
    "/vehicles/{vehicle_id}/availability",
    response_model=AvailabilityResponse,
    tags=["bookings"],
    summary="Check available time slots for a vehicle",
    responses={404: {"description": "Vehicle not found"}},
    operation_id="bookings_check_availability",
)
async def check_availability(
    vehicle_id: uuid.UUID,
    booking_date: Annotated[
        date,
        Query(..., description="Date to check, YYYY-MM-DD.", examples=["2025-08-25"]),
    ],
    service: BookingService = Depends(get_booking_service),
) -> AvailabilityResponse:
    """See which time slots are free on a given date.

    Returns all four session blocks with their availability status.
    A slot is **booked** if there is a non-cancelled booking for that session.
    """
    return await service.check_availability(vehicle_id=vehicle_id, date_=booking_date)


# ---------------------------------------------------------------------------
# Create booking (renter only)
# ---------------------------------------------------------------------------
@router.post(
    "/bookings",
    response_model=BookingOut,
    status_code=status.HTTP_201_CREATED,
    tags=["bookings"],
    summary="Request to book a vehicle",
    responses={
        400: {"description": "Vehicle unavailable"},
        401: {"description": "Missing or invalid token"},
        403: {"description": "RENTER role required"},
        404: {"description": "Vehicle not found"},
        409: {"description": "This session is already booked"},
    },
    operation_id="bookings_create",
)
async def create_booking(
    payload: BookingCreateIn,
    renter: User = Depends(require_role(UserRole.RENTER)),
    service: BookingService = Depends(get_booking_service),
) -> BookingOut:
    """Submit a booking request.

    - `duration_hours` must match the session block (4h for morning/afternoon/evening, 8h for full_day)
    - If the slot is already taken you get a 409 with the existing booking id
    - Amount is calculated from the vehicle's current price and the session duration
    """
    return await service.create_booking(renter=renter, payload=payload)


# ---------------------------------------------------------------------------
# Renter's own bookings
# ---------------------------------------------------------------------------
@router.get(
    "/bookings",
    response_model=BookingPage,
    tags=["bookings"],
    summary="My bookings (renter view)",
    operation_id="bookings_list_renter",
)
async def list_my_bookings(
    renter: User = Depends(require_role(UserRole.RENTER)),
    service: BookingService = Depends(get_booking_service),
    status: str | None = Query(
        default=None,
        description="Filter by status.",
        examples=["PENDING"],
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> BookingPage:
    return await service.list_my_bookings(
        renter=renter, status=status, limit=limit, offset=offset
    )


@router.get(
    "/bookings/{booking_id}",
    response_model=BookingOut,
    tags=["bookings"],
    summary="One booking (renter or provider)",
    responses={404: {"description": "Not found, or not involved"}, 403: {"description": "Not involved in this booking"}},
)
async def get_booking(
    booking_id: uuid.UUID,
    user: User = Depends(get_current_user),
    service: BookingService = Depends(get_booking_service),
) -> BookingOut:
    """Detail for any booking you are a party to (renter or provider)."""
    return await service.get_booking(booking_id=booking_id, user=user)


# ---------------------------------------------------------------------------
# Renter: cancel
# ---------------------------------------------------------------------------
@router.patch(
    "/bookings/{booking_id}/cancel",
    response_model=BookingOut,
    tags=["bookings"],
    summary="Cancel a booking (renter only)",
    responses={403: {"description": "Cannot cancel — wrong status"}, 404: {"description": "Not found or not yours"}},
)
async def cancel_booking(
    booking_id: uuid.UUID,
    renter: User = Depends(require_role(UserRole.RENTER)),
    service: BookingService = Depends(get_booking_service),
) -> BookingOut:
    """Cancel a PENDING or ACCEPTED booking. Sets status to CANCELLED."""
    await service.cancel_booking(booking_id=booking_id, renter=renter)
    booking = await service.repo.get_by_id(booking_id)
    if booking is None:
        raise NotFoundError("Booking not found.", code="BOOKING_NOT_FOUND")
    return BookingOut.from_model(booking)


# ---------------------------------------------------------------------------
# Provider: incoming requests
# ---------------------------------------------------------------------------
@router.get(
    "/provider/bookings",
    response_model=BookingPage,
    tags=["bookings"],
    summary="Incoming booking requests (provider view)",
)
async def list_incoming_bookings(
    provider: User = Depends(require_role(UserRole.PROVIDER)),
    service: BookingService = Depends(get_booking_service),
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> BookingPage:
    return await service.list_incoming_bookings(
        provider=provider, status=status, limit=limit, offset=offset
    )


@router.get(
    "/provider/bookings/{booking_id}",
    response_model=BookingOut,
    tags=["bookings"],
    summary="One booking (provider view)",
    responses={404: {"description": "Not found or not yours"}},
)
async def get_provider_booking(
    booking_id: uuid.UUID,
    provider: User = Depends(require_role(UserRole.PROVIDER)),
    service: BookingService = Depends(get_booking_service),
) -> BookingOut:
    booking = await service._own_provider_booking(provider=provider, booking_id=booking_id)
    return BookingOut.from_model(booking)


@router.patch(
    "/provider/bookings/{booking_id}",
    response_model=BookingOut,
    tags=["bookings"],
    summary="Accept or reject a booking (provider only)",
    responses={403: {"description": "Invalid state transition"}, 404: {"description": "Not found or not yours"}},
)
async def update_booking(
    booking_id: uuid.UUID,
    payload: BookingUpdateIn,
    provider: User = Depends(require_role(UserRole.PROVIDER)),
    service: BookingService = Depends(get_booking_service),
) -> BookingOut:
    """Accept (ACCEPTED) or reject (REJECTED) a PENDING booking.

    Use `POST /bookings/{id}/start` to move ACCEPTED → ACTIVE when the
    renter collects the vehicle.
    """
    return await service.update_booking_status(
        booking_id=booking_id, provider=provider, payload=payload
    )