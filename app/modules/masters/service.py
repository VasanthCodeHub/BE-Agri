"""Vehicle master data business logic."""

from __future__ import annotations

import uuid
from typing import NamedTuple

from app.core.exceptions import BadRequestError
from app.modules.masters.models import VehicleManufacturer, VehicleModel
from app.modules.masters.repository import MasterRepository
from app.modules.masters.schemas import (
    VehicleManufacturerOut,
    VehicleMastersOut,
    VehicleModelOut,
    VehicleVariantOut,
)
from app.modules.vehicles.models import FuelType


#: The master-data reference a listing may carry, plus the canonical names it
#: implies. `brand`/`model` on the vehicle come FROM here, so the free-text
#: columns and the master rows can never disagree. `vehicle_type_code` /
#: `vehicle_type_id` / `fuel_type` / `power_hp` come from the resolved model:
#: the backend is the source of truth for what a model IS, the client never
#: submits those independently.
class ResolvedMaster(NamedTuple):
    manufacturer_id: uuid.UUID | None
    model_id: uuid.UUID | None
    variant_id: uuid.UUID | None
    brand: str | None
    model_name: str | None
    vehicle_type_id: uuid.UUID | None
    vehicle_type_code: str | None
    fuel_type: FuelType | None
    power_hp: int | None


class MasterService:
    def __init__(self, *, repo: MasterRepository) -> None:
        self.repo = repo

    # -----------------------------------------------------------------------
    # The dropdown tree
    # -----------------------------------------------------------------------
    async def list_masters(self, *, vehicle_type_code: str | None = None) -> VehicleMastersOut:
        manufacturers = await self.repo.list_manufacturers(vehicle_type_code=vehicle_type_code)
        return VehicleMastersOut(
            manufacturers=[
                VehicleManufacturerOut(
                    id=m.id,
                    name=m.name,
                    models=[
                        VehicleModelOut(
                            id=model.id,
                            name=model.name,
                            vehicle_type_code=model.vehicle_type.code,
                            fuel_type=model.fuel_type,
                            power_hp=model.power_hp,
                            variants=[
                                VehicleVariantOut(
                                    id=v.id,
                                    name=v.name,
                                    manufacture_year=v.manufacture_year,
                                    power_hp=v.power_hp,
                                )
                                for v in model.variants
                                if v.is_active
                            ],
                        )
                        for model in m.models
                        if model.is_active
                    ],
                )
                for m in manufacturers
                if m.models
            ],
        )

    # -----------------------------------------------------------------------
    # Validation — used by the vehicle service when a listing references masters
    # -----------------------------------------------------------------------
    async def resolve_references(
        self,
        *,
        manufacturer_id: uuid.UUID | None,
        model_id: uuid.UUID | None,
        variant_id: uuid.UUID | None,
        vehicle_type_code: str | None,
    ) -> ResolvedMaster:
        """Validate master references and return the canonical names they imply.

        Rules, each enforced with a 400 the client can fix:

        - a referenced row must exist (inactive rows count as missing);
        - a model must belong to the given manufacturer (or supply its own);
        - a variant must belong to the given model;
        - when `vehicle_type_code` is given, a model's vehicle type must match
          it. None means "the model defines the type" — the master path.

        Inactive master rows are rejected here as missing: the pickers only
        ever offer active rows, so a stale id is a client bug worth surfacing.
        """
        manufacturer: VehicleManufacturer | None = None
        model: VehicleModel | None = None

        if manufacturer_id is not None:
            manufacturer = await self.repo.get_manufacturer_by_id(manufacturer_id)
            if manufacturer is None:
                raise BadRequestError(
                    "Unknown manufacturer. Fetch valid values from GET /vehicle-masters.",
                    code="MANUFACTURER_NOT_FOUND",
                    details={"manufacturer_id": str(manufacturer_id)},
                )

        if model_id is not None:
            model = await self.repo.get_model_by_id(model_id)
            if model is None:
                raise BadRequestError(
                    "Unknown vehicle model. Fetch valid values from GET /vehicle-masters.",
                    code="VEHICLE_MODEL_NOT_FOUND",
                    details={"model_id": str(model_id)},
                )
            if manufacturer is not None and model.manufacturer_id != manufacturer.id:
                raise BadRequestError(
                    f"Model {model.name!r} does not belong to "
                    f"{manufacturer.name!r}. Fetch valid combinations from "
                    "GET /vehicle-masters.",
                    code="INVALID_MASTER_COMBINATION",
                    details={
                        "manufacturer_id": str(manufacturer.id),
                        "model_id": str(model.id),
                    },
                )
            if vehicle_type_code is not None and model.vehicle_type.code != vehicle_type_code:
                raise BadRequestError(
                    f"Model {model.name!r} is a {model.vehicle_type.name_en}, "
                    f"not a {vehicle_type_code}. Pick a matching vehicle type.",
                    code="INVALID_MASTER_COMBINATION",
                    details={
                        "model_id": str(model.id),
                        "model_vehicle_type_code": model.vehicle_type.code,
                        "vehicle_type_code": vehicle_type_code,
                    },
                )
            manufacturer = model.manufacturer

        if variant_id is not None:
            variant = await self.repo.get_variant_by_id(variant_id)
            if variant is None:
                raise BadRequestError(
                    "Unknown vehicle variant. Fetch valid values from GET /vehicle-masters.",
                    code="VEHICLE_VARIANT_NOT_FOUND",
                    details={"variant_id": str(variant_id)},
                )
            if model is not None and variant.model_id != model.id:
                raise BadRequestError(
                    f"Variant {variant.name!r} does not belong to model "
                    f"{model.name!r}. Fetch valid combinations from "
                    "GET /vehicle-masters.",
                    code="INVALID_MASTER_COMBINATION",
                    details={"model_id": str(model.id), "variant_id": str(variant.id)},
                )
            if model is None:
                # A variant implies its model, which in turn implies everything
                # else — resolve it so the caller never stores a dangling chain.
                model = variant.model
                if vehicle_type_code is not None and model.vehicle_type.code != vehicle_type_code:
                    raise BadRequestError(
                        f"Variant {variant.name!r} belongs to a "
                        f"{model.vehicle_type.name_en}, not a {vehicle_type_code}.",
                        code="INVALID_MASTER_COMBINATION",
                        details={
                            "variant_id": str(variant.id),
                            "vehicle_type_code": vehicle_type_code,
                        },
                    )
                manufacturer = model.manufacturer

        return ResolvedMaster(
            manufacturer_id=manufacturer.id if manufacturer else None,
            model_id=model.id if model else None,
            variant_id=variant_id,
            brand=manufacturer.name if manufacturer else None,
            model_name=model.name if model else None,
            vehicle_type_id=model.vehicle_type.id if model else None,
            vehicle_type_code=model.vehicle_type.code if model else None,
            fuel_type=model.fuel_type if model else None,
            power_hp=model.power_hp if model else None,
        )
