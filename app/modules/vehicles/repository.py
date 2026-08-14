"""Data access for vehicle listings.

Every query lives here. As in the auth module, nothing in this file commits —
`get_db` owns the transaction, so a request that fails halfway leaves no
half-created listing with three of its six photos.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import utc_now
from app.modules.users.models import User, UserStatus
from app.modules.vehicles.models import (
    ListingStatus,
    Vehicle,
    VehicleImage,
    VehicleType,
)


class VehicleRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # -----------------------------------------------------------------------
    # Vehicle types
    # -----------------------------------------------------------------------
    async def get_type_by_code(self, code: str) -> VehicleType | None:
        result = await self.db.execute(select(VehicleType).where(VehicleType.code == code))
        return result.scalar_one_or_none()

    async def list_active_types(self) -> list[VehicleType]:
        result = await self.db.execute(
            select(VehicleType)
            .where(VehicleType.is_active.is_(True))
            .order_by(VehicleType.sort_order, VehicleType.name_en)
        )
        return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Writes
    # -----------------------------------------------------------------------
    async def registration_is_taken(self, registration_number: str) -> bool:
        """Is this number already on a live listing?

        The database enforces this too (partial unique index). Checking here as
        well lets the API answer with a clear 409 instead of a raw
        IntegrityError, while the index remains the actual guarantee against two
        concurrent requests.
        """
        result = await self.db.execute(
            select(Vehicle.id).where(
                Vehicle.registration_number == registration_number,
                Vehicle.deleted_at.is_(None),
            )
        )
        return result.first() is not None

    async def create(self, *, vehicle: Vehicle, image_urls: list[str]) -> Vehicle:
        vehicle.images = [
            VehicleImage(url=url, sort_order=index) for index, url in enumerate(image_urls)
        ]
        self.db.add(vehicle)
        # flush, not commit: assigns the id and makes the relationships usable
        # within this request while staying inside the transaction.
        await self.db.flush()
        # Load vehicle_type / provider so the response can be built without a
        # lazy load during serialisation (which would raise MissingGreenlet).
        await self.db.refresh(vehicle, attribute_names=["vehicle_type", "provider", "images"])
        return vehicle

    # -----------------------------------------------------------------------
    # Reads
    # -----------------------------------------------------------------------
    async def get_owned(
        self, *, vehicle_id: uuid.UUID, provider_user_id: uuid.UUID
    ) -> Vehicle | None:
        """One of *this* provider's vehicles.

        Ownership is part of the WHERE clause rather than a check afterwards, so
        a mistyped id and someone else's id are indistinguishable to the caller
        — no probing for which listings exist.
        """
        result = await self.db.execute(
            select(Vehicle).where(
                Vehicle.id == vehicle_id,
                Vehicle.provider_user_id == provider_user_id,
                Vehicle.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def list_for_provider(
        self, *, provider_user_id: uuid.UUID, limit: int, offset: int
    ) -> tuple[list[Vehicle], int]:
        """This provider's listings, newest first — including unavailable ones."""
        base = select(Vehicle).where(
            Vehicle.provider_user_id == provider_user_id,
            Vehicle.deleted_at.is_(None),
        )
        return await self._page(base, limit=limit, offset=offset)

    @staticmethod
    def _discoverable() -> Select[Any]:
        """The base query for anything a renter is allowed to see.

        Four conditions decide discoverability, and all four matter:
          - not soft-deleted
          - the owner marked it available
          - moderation approved it (R9)
          - the owner's account is not suspended — otherwise suspending a
            provider would leave their listings on the feed

        Defined once so the feed and the detail endpoint cannot disagree. If they
        did, a listing hidden from the feed would still be readable by id.
        """
        return (
            select(Vehicle)
            .join(User, User.id == Vehicle.provider_user_id)
            .where(
                Vehicle.deleted_at.is_(None),
                Vehicle.is_available.is_(True),
                Vehicle.listing_status == ListingStatus.APPROVED,
                User.status == UserStatus.ACTIVE,
            )
        )

    async def get_public_by_id(self, vehicle_id: uuid.UUID) -> Vehicle | None:
        """One listing, but only if a renter is allowed to see it."""
        result = await self.db.execute(self._discoverable().where(Vehicle.id == vehicle_id))
        return result.scalars().unique().one_or_none()

    async def list_public(
        self, *, limit: int, offset: int, type_code: str | None = None
    ) -> tuple[list[Vehicle], int]:
        """Every discoverable listing, from every provider."""
        base = self._discoverable()
        if type_code:
            base = base.join(VehicleType, VehicleType.id == Vehicle.vehicle_type_id).where(
                VehicleType.code == type_code
            )
        return await self._page(base, limit=limit, offset=offset)

    async def _page(
        self, base: Select[Any], *, limit: int, offset: int
    ) -> tuple[list[Vehicle], int]:
        """Run a paginated query plus its total count.

        The count reuses the same filters via a subquery, so the two can never
        drift apart when a filter is added.
        """
        total = await self.db.scalar(
            select(func.count()).select_from(base.order_by(None).subquery())
        )
        result = await self.db.execute(
            base.order_by(Vehicle.created_at.desc(), Vehicle.id).limit(limit).offset(offset)
        )
        return list(result.scalars().unique().all()), int(total or 0)

    # -----------------------------------------------------------------------
    # Update
    # -----------------------------------------------------------------------
    async def apply_updates(
        self,
        vehicle: Vehicle,
        *,
        fields: dict[str, Any],
        image_urls: list[str] | None,
    ) -> Vehicle:
        """Write the changed columns, and replace the photos if new ones came."""
        for column, value in fields.items():
            setattr(vehicle, column, value)

        if image_urls is not None:
            # Two flushes on purpose. `sort_order` is unique per vehicle, so
            # inserting the new photos before the old ones are gone would
            # violate that constraint. Clearing and flushing first sends the
            # DELETEs, then the INSERTs land on a clean slate.
            vehicle.images.clear()
            await self.db.flush()
            vehicle.images.extend(
                VehicleImage(url=url, sort_order=index) for index, url in enumerate(image_urls)
            )

        await self.db.flush()
        # `updated_at` carries onupdate=func.now(), so it is expired after the
        # UPDATE — see the note in set_availability. Reload it, and the relations
        # the response needs, inside the async context.
        await self.db.refresh(
            vehicle, attribute_names=["updated_at", "images", "vehicle_type", "provider"]
        )
        return vehicle

    # -----------------------------------------------------------------------
    # Availability / delete
    # -----------------------------------------------------------------------
    async def set_availability(self, vehicle: Vehicle, *, is_available: bool) -> None:
        vehicle.is_available = is_available
        await self.db.flush()
        # `updated_at` carries onupdate=func.now(), so the UPDATE leaves it
        # expired — PostgreSQL returns server defaults for INSERTs via RETURNING
        # but not for UPDATEs. Reading it while building the response would then
        # lazy-load, and a lazy load during serialisation raises MissingGreenlet
        # in async SQLAlchemy. Fetch it here, inside the async context.
        await self.db.refresh(vehicle, attribute_names=["updated_at"])

    async def soft_delete(self, vehicle: Vehicle) -> None:
        """Mark deleted. Frees the registration number for re-listing."""
        vehicle.deleted_at = utc_now()
        await self.db.flush()
