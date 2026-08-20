"""Response shapes for vehicle master data."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.modules.vehicles.models import FuelType


class VehicleVariantOut(BaseModel):
    id: uuid.UUID
    name: str
    manufacture_year: int | None
    power_hp: int | None


class VehicleModelOut(BaseModel):
    """One model, with everything the add-vehicle form needs to cascade."""

    id: uuid.UUID
    name: str
    vehicle_type_code: str = Field(
        description="The type this model belongs to — filters the type picker."
    )
    fuel_type: FuelType
    power_hp: int
    variants: list[VehicleVariantOut]


class VehicleManufacturerOut(BaseModel):
    id: uuid.UUID
    name: str
    models: list[VehicleModelOut]


class VehicleMastersOut(BaseModel):
    """The full dropdown tree: manufacturer → models → variants."""

    manufacturers: list[VehicleManufacturerOut]
