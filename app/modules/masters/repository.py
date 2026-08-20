"""Data access for vehicle master data."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.masters.models import (
    VehicleManufacturer,
    VehicleModel,
    VehicleVariant,
)
from app.modules.vehicles.models import VehicleType


class MasterRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # -----------------------------------------------------------------------
    # Reads — the dropdown tree
    # -----------------------------------------------------------------------
    async def list_manufacturers(
        self, *, vehicle_type_code: str | None = None
    ) -> list[VehicleManufacturer]:
        """All active manufacturers, optionally narrowed to one vehicle type.

        Filtering by type happens at the model level, so manufacturers with no
        matching models are dropped from the response entirely.
        """
        stmt = (
            select(VehicleManufacturer)
            .options(
                selectinload(VehicleManufacturer.models).selectinload(VehicleModel.variants),
                selectinload(VehicleManufacturer.models).selectinload(VehicleModel.vehicle_type),
            )
            .join(VehicleManufacturer.models)
            .where(VehicleManufacturer.is_active.is_(True))
        )
        if vehicle_type_code:
            stmt = stmt.join(VehicleModel.vehicle_type).where(VehicleType.code == vehicle_type_code)
        result = await self.db.execute(stmt)
        return list(result.scalars().unique().all())

    async def get_manufacturer_by_id(
        self, manufacturer_id: uuid.UUID
    ) -> VehicleManufacturer | None:
        result = await self.db.execute(
            select(VehicleManufacturer).where(
                VehicleManufacturer.id == manufacturer_id,
                VehicleManufacturer.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_model_by_id(self, model_id: uuid.UUID) -> VehicleModel | None:
        result = await self.db.execute(
            select(VehicleModel)
            .options(
                selectinload(VehicleModel.vehicle_type),
                selectinload(VehicleModel.manufacturer),
            )
            .where(
                VehicleModel.id == model_id,
                VehicleModel.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_variant_by_id(self, variant_id: uuid.UUID) -> VehicleVariant | None:
        result = await self.db.execute(
            select(VehicleVariant)
            .options(
                selectinload(VehicleVariant.model).selectinload(VehicleModel.vehicle_type),
                selectinload(VehicleVariant.model).selectinload(VehicleModel.manufacturer),
            )
            .where(
                VehicleVariant.id == variant_id,
                VehicleVariant.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()
