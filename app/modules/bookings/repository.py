"""Booking data access."""

from __future__ import annotations

import uuid
from typing import Any
from datetime import date, datetime, timezone

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.bookings.models import Booking, BookingStatus, SessionBlock
from app.modules.vehicles.models import Vehicle


class BookingRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # -----------------------------------------------------------------------
    # Writes
    # -----------------------------------------------------------------------
    async def create(self, *, booking: Booking) -> Booking:
        self.db.add(booking)
        await self.db.flush()
        await self.db.refresh(
            booking,
            attribute_names=["vehicle", "renter", "provider"],
        )
        return booking

    async def get_by_id(self, booking_id: uuid.UUID) -> Booking | None:
        result = await self.db.execute(
            select(Booking)
            .options(selectinload(Booking.vehicle), selectinload(Booking.renter))
            .where(Booking.id == booking_id)
        )
        return result.scalars().unique().one_or_none()

    async def get_by_reference(self, reference: str) -> Booking | None:
        result = await self.db.execute(
            select(Booking).where(Booking.reference == reference)
        )
        return result.scalar_one_or_none()

    async def reference_exists(self, reference: str) -> bool:
        return await self.get_by_reference(reference) is not None

    # -----------------------------------------------------------------------
    # Availability check
    # -----------------------------------------------------------------------
    async def get_bookings_for_vehicle_on_date(
        self, *, vehicle_id: uuid.UUID, booking_date: date, exclude_statuses: list[BookingStatus] | None = None
    ) -> list[Booking]:
        stmt = (
            select(Booking)
            .where(
                Booking.vehicle_id == vehicle_id,
                func.date(Booking.booking_date) == booking_date,
            )
        )
        if exclude_statuses:
            stmt = stmt.where(Booking.status.notin_(exclude_statuses))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Renter queries
    # -----------------------------------------------------------------------
    async def get_renter_bookings(
        self,
        *,
        renter_user_id: uuid.UUID,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Booking], int]:
        base = (
            select(Booking)
            .options(selectinload(Booking.vehicle), selectinload(Booking.provider))
            .where(Booking.renter_user_id == renter_user_id)
        )
        if status:
            base = base.where(Booking.status == status)
        return await self._page(base, limit=limit, offset=offset)

    async def get_renter_booking(
        self, *, booking_id: uuid.UUID, renter_user_id: uuid.UUID
    ) -> Booking | None:
        result = await self.db.execute(
            select(Booking)
            .options(selectinload(Booking.vehicle), selectinload(Booking.provider))
            .where(Booking.id == booking_id, Booking.renter_user_id == renter_user_id)
        )
        return result.scalars().unique().one_or_none()

    # -----------------------------------------------------------------------
    # Provider queries
    # -----------------------------------------------------------------------
    async def get_provider_bookings(
        self,
        *,
        provider_user_id: uuid.UUID,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[Booking], int]:
        base = (
            select(Booking)
            .options(selectinload(Booking.vehicle), selectinload(Booking.renter))
            .where(Booking.provider_user_id == provider_user_id)
        )
        if status:
            base = base.where(Booking.status == status)
        return await self._page(base, limit=limit, offset=offset)

    async def get_provider_booking(
        self, *, booking_id: uuid.UUID, provider_user_id: uuid.UUID
    ) -> Booking | None:
        result = await self.db.execute(
            select(Booking)
            .options(selectinload(Booking.vehicle), selectinload(Booking.renter))
            .where(Booking.id == booking_id, Booking.provider_user_id == provider_user_id)
        )
        return result.scalars().unique().one_or_none()

    async def count_for_provider(
        self, provider_user_id: uuid.UUID, *, status: str | None = None
    ) -> int:
        base = select(Booking).where(Booking.provider_user_id == provider_user_id)
        if status:
            base = base.where(Booking.status == status)
        subq = base.order_by(None).subquery()
        return int(await self.db.scalar(select(func.count()).select_from(subq)) or 0)

    async def earnings_for_provider(self, provider_user_id: uuid.UUID) -> int:
        subq = (
            select(func.coalesce(func.sum(Booking.amount_paise), 0))
            .where(
                Booking.provider_user_id == provider_user_id,
                Booking.status == BookingStatus.COMPLETED,
            )
            .subquery()
        )
        return int(await self.db.scalar(select(subq)) or 0)

    # -----------------------------------------------------------------------
    # Reviews helper
    # -----------------------------------------------------------------------
    async def user_has_completed_booking(
        self, *, user_id: uuid.UUID, vehicle_id: uuid.UUID
    ) -> bool:
        result = await self.db.execute(
            select(Booking.id)
            .where(
                Booking.renter_user_id == user_id,
                Booking.vehicle_id == vehicle_id,
                Booking.status == BookingStatus.COMPLETED,
            )
            .limit(1)
        )
        return result.first() is not None

    # -----------------------------------------------------------------------
    # Pagination helper
    # -----------------------------------------------------------------------
    async def _page(
        self, base: Select[Any], *, limit: int, offset: int
    ) -> tuple[list[Booking], int]:
        total = await self.db.scalar(
            select(func.count()).select_from(base.order_by(None).subquery())
        )
        result = await self.db.execute(
            base.order_by(Booking.created_at.desc(), Booking.id).limit(limit).offset(offset)
        )
        return list(result.scalars().unique().all()), int(total or 0)

    # -----------------------------------------------------------------------
    # Status update
    # -----------------------------------------------------------------------
    async def update_status(
        self, *, booking: Booking, status: BookingStatus, provider_note: str | None = None
    ) -> Booking:
        booking.status = status
        if provider_note is not None:
            booking.provider_note = provider_note
        if status == BookingStatus.CANCELLED:
            booking.cancelled_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(
            booking,
            attribute_names=[
                "status",
                "provider_note",
                "cancelled_at",
                "updated_at",
                "vehicle",
                "renter",
            ],
        )
        return booking

    async def cancel(self, *, booking: Booking) -> Booking:
        booking.status = BookingStatus.CANCELLED
        booking.cancelled_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.refresh(
            booking,
            attribute_names=["status", "cancelled_at", "updated_at"],
        )
        return booking