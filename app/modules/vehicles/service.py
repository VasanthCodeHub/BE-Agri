"""Vehicle listing business logic.

The rules about listings live here: who may create one, what makes a listing
discoverable, and what a renter is allowed to see.

Note what this layer does *not* trust: the caller tells us which vehicle they
want, never who owns it. Ownership always comes from the authenticated user.
"""

from __future__ import annotations

import uuid

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.modules.users.models import User
from app.modules.vehicles.models import Vehicle
from app.modules.vehicles.repository import VehicleRepository
from app.modules.vehicles.schemas import (
    VehicleCardOut,
    VehicleCardPage,
    VehicleCreateIn,
    VehicleOut,
    VehiclePage,
    VehicleTypeOut,
)

log = get_logger(__name__)


class VehicleService:
    def __init__(self, *, repo: VehicleRepository) -> None:
        self.repo = repo

    # -----------------------------------------------------------------------
    # Taxonomy
    # -----------------------------------------------------------------------
    async def list_types(self) -> list[VehicleTypeOut]:
        types = await self.repo.list_active_types()
        return [VehicleTypeOut.from_model(t) for t in types]

    # -----------------------------------------------------------------------
    # Create
    # -----------------------------------------------------------------------
    async def create_vehicle(self, *, provider: User, payload: VehicleCreateIn) -> VehicleOut:
        """Add a listing for the authenticated provider."""
        vehicle_type = await self.repo.get_type_by_code(payload.vehicle_type_code)
        if vehicle_type is None:
            # 400 rather than 404: the request is wrong, the URL is fine.
            raise BadRequestError(
                f"Unknown vehicle type {payload.vehicle_type_code!r}. "
                "Fetch the valid codes from GET /vehicle-types.",
                code="VEHICLE_TYPE_UNKNOWN",
                details={"vehicle_type_code": payload.vehicle_type_code},
            )

        if await self.repo.registration_is_taken(payload.registration_number):
            # Deliberately does not say whose listing it is — that would leak
            # one provider's inventory to another.
            raise ConflictError(
                "A vehicle with this registration number is already listed.",
                code="REGISTRATION_ALREADY_LISTED",
                details={"registration_number": payload.registration_number},
            )

        vehicle = Vehicle(
            provider_user_id=provider.id,
            vehicle_type_id=vehicle_type.id,
            name=payload.name,
            brand=payload.brand,
            model=payload.model,
            manufacture_year=payload.manufacture_year,
            registration_number=payload.registration_number,
            note=payload.note,
            price_amount=payload.price_amount,
            price_unit=payload.price_unit,
            location_text=payload.location_text,
            latitude=payload.latitude,
            longitude=payload.longitude,
            fuel_type=payload.fuel_type,
            power_hp=payload.power_hp,
            transmission=payload.transmission,
        )
        vehicle = await self.repo.create(vehicle=vehicle, image_urls=payload.image_urls)

        log.info(
            "vehicle_created",
            vehicle_id=str(vehicle.id),
            provider_id=str(provider.id),
            vehicle_type=vehicle_type.code,
            images=len(payload.image_urls),
        )
        return VehicleOut.from_model(vehicle)

    # -----------------------------------------------------------------------
    # Read — the provider's own listings
    # -----------------------------------------------------------------------
    async def list_my_vehicles(self, *, provider: User, limit: int, offset: int) -> VehiclePage:
        vehicles, total = await self.repo.list_for_provider(
            provider_user_id=provider.id, limit=limit, offset=offset
        )
        return VehiclePage(
            items=[VehicleOut.from_model(v) for v in vehicles],
            total=total,
            limit=limit,
            offset=offset,
        )

    # -----------------------------------------------------------------------
    # Read — the public feed
    # -----------------------------------------------------------------------
    async def list_available_vehicles(
        self, *, limit: int, offset: int, type_code: str | None
    ) -> VehicleCardPage:
        vehicles, total = await self.repo.list_public(
            limit=limit, offset=offset, type_code=type_code
        )
        return VehicleCardPage(
            items=[VehicleCardOut.from_model(v) for v in vehicles],
            total=total,
            limit=limit,
            offset=offset,
        )

    # -----------------------------------------------------------------------
    # Availability and delete
    # -----------------------------------------------------------------------
    async def set_availability(
        self, *, provider: User, vehicle_id: uuid.UUID, is_available: bool
    ) -> VehicleOut:
        vehicle = await self._own_vehicle(provider=provider, vehicle_id=vehicle_id)
        await self.repo.set_availability(vehicle, is_available=is_available)
        log.info(
            "vehicle_availability_changed",
            vehicle_id=str(vehicle.id),
            is_available=is_available,
        )
        return VehicleOut.from_model(vehicle)

    async def delete_vehicle(self, *, provider: User, vehicle_id: uuid.UUID) -> None:
        vehicle = await self._own_vehicle(provider=provider, vehicle_id=vehicle_id)
        await self.repo.soft_delete(vehicle)
        log.info("vehicle_deleted", vehicle_id=str(vehicle.id), provider_id=str(provider.id))

    async def _own_vehicle(self, *, provider: User, vehicle_id: uuid.UUID) -> Vehicle:
        """Fetch a vehicle the caller owns, or 404.

        404 and not 403: telling a caller "this exists but is not yours" confirms
        the id is real, which is how an attacker enumerates other providers'
        inventory. Holding the PROVIDER role is not ownership.
        """
        vehicle = await self.repo.get_owned(vehicle_id=vehicle_id, provider_user_id=provider.id)
        if vehicle is None:
            raise NotFoundError("Vehicle not found.", code="VEHICLE_NOT_FOUND")
        return vehicle
