"""Data access for vehicle listings."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import utc_now
from app.modules.reviews.models import Review
from app.modules.users.models import User, UserStatus
from app.modules.vehicles.models import (
    ListingStatus,
    Vehicle,
    VehicleImage,
    VehicleType,
)
from app.modules.vehicles.registration import normalise_registration_number


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
    async def registration_is_taken(
        self, registration_number: str, *, exclude_vehicle_id: uuid.UUID | None = None
    ) -> bool:
        # Normalise here, not just in the schema: the unique index compares the
        # canonical form, so "TN 38 AB 1234" and "TN38AB1234" must both hit it.
        normalised = normalise_registration_number(registration_number)
        stmt = select(Vehicle.id).where(
            Vehicle.registration_number == normalised,
            Vehicle.deleted_at.is_(None),
        )
        if exclude_vehicle_id is not None:
            stmt = stmt.where(Vehicle.id != exclude_vehicle_id)
        result = await self.db.execute(stmt)
        return result.first() is not None

    async def create(self, *, vehicle: Vehicle, public_ids: list[str]) -> Vehicle:
        vehicle.images = [
            VehicleImage(public_id=public_id, sort_order=index)
            for index, public_id in enumerate(public_ids)
        ]
        self.db.add(vehicle)
        await self.db.flush()
        await self.db.refresh(vehicle, attribute_names=["vehicle_type", "provider", "images"])
        return vehicle

    # -----------------------------------------------------------------------
    # Counts
    # -----------------------------------------------------------------------
    async def count_for_provider(self, *, provider_user_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(Vehicle)
            .where(
                Vehicle.provider_user_id == provider_user_id,
                Vehicle.deleted_at.is_(None),
            )
        )
        return int(result.scalar() or 0)

    async def count_available_for_provider(self, *, provider_user_id: uuid.UUID) -> int:
        result = await self.db.execute(
            select(func.count())
            .select_from(Vehicle)
            .where(
                Vehicle.provider_user_id == provider_user_id,
                Vehicle.deleted_at.is_(None),
                Vehicle.is_available.is_(True),
            )
        )
        return int(result.scalar() or 0)

    async def list_owned_ids(self, *, provider_user_id: uuid.UUID) -> list[uuid.UUID]:
        """Live vehicle ids for a provider — the scope for dashboard stats."""
        result = await self.db.execute(
            select(Vehicle.id).where(
                Vehicle.provider_user_id == provider_user_id,
                Vehicle.deleted_at.is_(None),
            )
        )
        return list(result.scalars().all())

    # -----------------------------------------------------------------------
    # Reads — owner
    # -----------------------------------------------------------------------
    async def get_owned(
        self, *, vehicle_id: uuid.UUID, provider_user_id: uuid.UUID
    ) -> Vehicle | None:
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
        base = select(Vehicle).where(
            Vehicle.provider_user_id == provider_user_id,
            Vehicle.deleted_at.is_(None),
        )
        return await self._page(base, limit=limit, offset=offset)

    # -----------------------------------------------------------------------
    # Reads — public feed
    # -----------------------------------------------------------------------
    @staticmethod
    def _discoverable() -> Select[Any]:
        """Base query for anything a renter is allowed to see."""
        return (
            select(Vehicle)
            .join(User, User.id == Vehicle.provider_user_id)
            .options(selectinload(Vehicle.provider), selectinload(Vehicle.vehicle_type))
            .where(
                Vehicle.deleted_at.is_(None),
                Vehicle.is_available.is_(True),
                Vehicle.listing_status == ListingStatus.APPROVED,
                User.status == UserStatus.ACTIVE,
            )
        )

    async def get_public_by_id(self, vehicle_id: uuid.UUID) -> Vehicle | None:
        result = await self.db.execute(self._discoverable().where(Vehicle.id == vehicle_id))
        return result.scalars().unique().one_or_none()

    async def review_stats(
        self, vehicle_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[float, int]]:
        """Average rating and review count per vehicle, one query.

        Returns {vehicle_id: (avg_rating, count)}. Vehicles with no reviews
        are simply absent from the dict — callers default to (None, 0).
        """
        if not vehicle_ids:
            return {}
        rows = await self.db.execute(
            select(
                Review.vehicle_id,
                func.avg(Review.rating),
                func.count(Review.id),
            )
            .where(Review.vehicle_id.in_(vehicle_ids))
            .group_by(Review.vehicle_id)
        )
        return {
            vehicle_id: (round(float(avg), 2), int(count)) for vehicle_id, avg, count in rows.all()
        }

    async def list_public(
        self,
        *,
        limit: int,
        offset: int,
        type_code: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
        radius_km: float | None = None,
        q: str | None = None,
        max_price: int | None = None,
        sort: str = "newest",
    ) -> tuple[list[tuple[Vehicle, float | None]], int]:
        """Discoverable listings, optionally geo-filtered.

        Returns a list of (vehicle, distance_km) tuples so the router can
        attach distance to each card without another query.
        """
        base = self._discoverable()

        # Vehicle type filter
        if type_code:
            base = base.join(VehicleType, VehicleType.id == Vehicle.vehicle_type_id).where(
                VehicleType.code == type_code
            )

        # Text search on name, brand, note, location_text
        if q:
            pattern = f"%{q.strip().lower()}%"
            base = base.where(
                func.lower(Vehicle.name).like(pattern)
                | func.lower(Vehicle.brand).like(pattern)
                | func.lower(Vehicle.note).like(pattern)
                | func.lower(Vehicle.location_text).like(pattern)
            )

        # Price filter
        if max_price is not None:
            base = base.where(Vehicle.price_amount <= max_price)

        # --- ordering (before distance so we can still count) ---
        if sort == "price_asc":
            order = [Vehicle.price_amount.asc()]
        elif sort == "price_desc":
            order = [Vehicle.price_amount.desc()]
        elif sort == "distance":
            order = [Vehicle.created_at.desc()]  # fallback; distance needs geo
        else:
            order = [Vehicle.created_at.desc(), Vehicle.id]

        # Total count (before adding geo SELECT)
        count_subq = base.order_by(None).subquery()
        total = await self.db.scalar(select(func.count()).select_from(count_subq))

        # --- Geo filter ---
        # Use ST_Simplify or a plain point check. We store lat/lng separately,
        # so ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) is the geo point.
        # ST_DWithin uses geography distance in metres when both operands are geography.
        if lat is not None and lng is not None:
            base = base.where(
                Vehicle.latitude.is_not(None),
                Vehicle.longitude.is_not(None),
            )
            if radius_km is not None:
                radius_m = radius_km * 1000
                base = base.where(
                    text(
                        "ST_DWithin("
                        "  ST_SetSRID(ST_MakePoint(vehicles.longitude, vehicles.latitude), "
                        "4326)::geography,"
                        "  ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography,"
                        f" {radius_m}"
                        ")"
                    )
                )

            # Add distance to SELECT
            if sort == "distance":
                dist_expr = text(
                    "ST_Distance("
                    "  ST_SetSRID(ST_MakePoint(vehicles.longitude, vehicles.latitude), "
                    "4326)::geography,"
                    "  ST_SetSRID(ST_MakePoint(:lng, :lat), 4326)::geography"
                    ") / 1000.0"
                )
            else:
                dist_expr = None
        else:
            dist_expr = None

        # Build column list
        select_cols: list[Any]
        if dist_expr is not None:
            select_cols = [Vehicle, dist_expr.label("distance_km")]
        else:
            select_cols = [Vehicle]

        # Use base.with_only_columns to preserve joins, not select_from(base)
        stmt = base.with_only_columns(*select_cols).order_by(None)  # type: ignore[arg-type]

        if sort == "distance" and dist_expr is not None:
            stmt = stmt.order_by(text("distance_km NULLS LAST"), Vehicle.created_at.desc())
        else:
            stmt = stmt.order_by(*order)

        stmt = stmt.limit(limit).offset(offset)

        params = {"lat": lat, "lng": lng}
        result = await self.db.execute(stmt, params)
        rows = result.unique().all()

        vehicles: list[tuple[Vehicle, float | None]] = []
        for row in rows:
            if dist_expr is not None:
                vehicle = row[0]
                dist_val = row.distance_km  # type: ignore[attr-defined]
                vehicles.append(
                    (vehicle, round(float(dist_val), 2) if dist_val is not None else None)
                )
            else:
                vehicles.append((row[0], None))

        return vehicles, int(total or 0)

    # -----------------------------------------------------------------------
    # Pagination
    # -----------------------------------------------------------------------
    async def _page(
        self, base: Select[Any], *, limit: int, offset: int
    ) -> tuple[list[Vehicle], int]:
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
        public_ids: list[str] | None,
    ) -> Vehicle:
        for column, value in fields.items():
            setattr(vehicle, column, value)

        if public_ids is not None:
            vehicle.images.clear()
            await self.db.flush()
            vehicle.images.extend(
                VehicleImage(public_id=public_id, sort_order=index)
                for index, public_id in enumerate(public_ids)
            )

        await self.db.flush()
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
        await self.db.refresh(vehicle, attribute_names=["updated_at"])

    async def soft_delete(self, vehicle: Vehicle) -> None:
        vehicle.deleted_at = utc_now()
        await self.db.flush()
