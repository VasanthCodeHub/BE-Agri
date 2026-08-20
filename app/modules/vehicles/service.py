"""Vehicle listing business logic."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.core.config import Settings
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.integrations.cloudinary import is_in_our_folder
from app.modules.users.models import User
from app.modules.vehicles.models import MAX_IMAGES, Vehicle
from app.modules.vehicles.repository import VehicleRepository
from app.modules.vehicles.schemas import (
    VehicleCardOut,
    VehicleCardPage,
    VehicleCreateIn,
    VehicleOut,
    VehiclePage,
    VehicleSearchParams,
    VehicleTypeOut,
    VehicleUpdateIn,
)

log = get_logger(__name__)


class VehicleService:
    def __init__(self, *, repo: VehicleRepository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings

    def _check_images_are_ours(self, public_ids: list[str]) -> None:
        foreign = [pid for pid in public_ids if not is_in_our_folder(pid, self.settings)]
        if foreign:
            raise BadRequestError(
                "These images did not come from this app's upload endpoint. "
                "Upload via POST /provider/uploads/signature and send the "
                "public_id it returns.",
                code="IMAGE_NOT_RECOGNISED",
                details={"public_ids": foreign[:MAX_IMAGES]},
            )

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
        vehicle_type = await self.repo.get_type_by_code(payload.vehicle_type_code)
        if vehicle_type is None:
            raise BadRequestError(
                f"Unknown vehicle type {payload.vehicle_type_code!r}. "
                "Fetch the valid codes from GET /vehicle-types.",
                code="VEHICLE_TYPE_UNKNOWN",
                details={"vehicle_type_code": payload.vehicle_type_code},
            )

        self._check_images_are_ours(payload.image_public_ids)

        if await self.repo.registration_is_taken(payload.registration_number):
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
        vehicle = await self.repo.create(vehicle=vehicle, public_ids=payload.image_public_ids)

        log.info(
            "vehicle_created",
            vehicle_id=str(vehicle.id),
            provider_id=str(provider.id),
            vehicle_type=vehicle_type.code,
            images=len(payload.image_public_ids),
        )
        return VehicleOut.from_model(vehicle, settings=self.settings)

    # -----------------------------------------------------------------------
    # Read — the provider's own listings
    # -----------------------------------------------------------------------
    async def list_my_vehicles(self, *, provider: User, limit: int, offset: int) -> VehiclePage:
        vehicles, total = await self.repo.list_for_provider(
            provider_user_id=provider.id, limit=limit, offset=offset
        )
        return VehiclePage(
            items=[VehicleOut.from_model(v, settings=self.settings) for v in vehicles],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_my_vehicle(self, *, provider: User, vehicle_id: uuid.UUID) -> VehicleOut:
        vehicle = await self._own_vehicle(provider=provider, vehicle_id=vehicle_id)
        return VehicleOut.from_model(vehicle, settings=self.settings)

    # -----------------------------------------------------------------------
    # Read — the public feed
    # -----------------------------------------------------------------------
    async def list_available_vehicles(
        self,
        *,
        params: VehicleSearchParams,
    ) -> VehicleCardPage:
        """Search the public feed with optional geo, text, price, and sorting."""
        rows, total = await self.repo.list_public(
            limit=params.limit,
            offset=params.offset,
            type_code=params.type_code,
            lat=params.lat,
            lng=params.lng,
            radius_km=params.radius_km,
            q=params.q,
            max_price=params.max_price,
            sort=params.sort,
        )

        stats = await self.repo.review_stats(
            [vehicle.id for vehicle, _distance in rows]
        )
        items = [
            VehicleCardOut.from_model(
                vehicle,
                settings=self.settings,
                distance_km=distance_km,
                rating=stats.get(vehicle.id, (None, 0))[0],
                review_count=stats.get(vehicle.id, (None, 0))[1],
            )
            for vehicle, distance_km in rows
        ]

        if params.available_only:
            pass  # already enforced by _discoverable()

        return VehicleCardPage(items=items, total=total, limit=params.limit, offset=params.offset)

    async def get_public_vehicle(self, *, vehicle_id: uuid.UUID) -> VehicleCardOut:
        """One listing, for the renter who tapped its card."""
        vehicle = await self.repo.get_public_by_id(vehicle_id)
        if vehicle is None:
            raise NotFoundError("Vehicle not found.", code="VEHICLE_NOT_FOUND")
        stats = await self.repo.review_stats([vehicle.id])
        rating, review_count = stats.get(vehicle.id, (None, 0))
        return VehicleCardOut.from_model(
            vehicle,
            settings=self.settings,
            rating=rating,
            review_count=review_count,
        )

    # -----------------------------------------------------------------------
    # Update
    # -----------------------------------------------------------------------
    async def update_vehicle(
        self, *, provider: User, vehicle_id: uuid.UUID, payload: VehicleUpdateIn
    ) -> VehicleOut:
        vehicle = await self._own_vehicle(provider=provider, vehicle_id=vehicle_id)
        changes = payload.model_dump(exclude_unset=True)
        public_ids = changes.pop("image_public_ids", None)
        if public_ids is not None:
            self._check_images_are_ours(public_ids)
        type_code = changes.pop("vehicle_type_code", None)

        if type_code is not None:
            vehicle_type = await self.repo.get_type_by_code(type_code)
            if vehicle_type is None:
                raise BadRequestError(
                    f"Unknown vehicle type {type_code!r}. "
                    "Fetch the valid codes from GET /vehicle-types.",
                    code="VEHICLE_TYPE_UNKNOWN",
                    details={"vehicle_type_code": type_code},
                )
            changes["vehicle_type_id"] = vehicle_type.id

        vehicle = await self.repo.apply_updates(vehicle, fields=changes, public_ids=public_ids)

        log.info(
            "vehicle_updated",
            vehicle_id=str(vehicle.id),
            provider_id=str(provider.id),
            changed=sorted(changes),
            images_replaced=public_ids is not None,
        )
        return VehicleOut.from_model(vehicle, settings=self.settings)

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
        return VehicleOut.from_model(vehicle, settings=self.settings)

    async def delete_vehicle(self, *, provider: User, vehicle_id: uuid.UUID) -> None:
        vehicle = await self._own_vehicle(provider=provider, vehicle_id=vehicle_id)
        await self.repo.soft_delete(vehicle)
        log.info("vehicle_deleted", vehicle_id=str(vehicle.id), provider_id=str(provider.id))

    async def _own_vehicle(self, *, provider: User, vehicle_id: uuid.UUID) -> Vehicle:
        vehicle = await self.repo.get_owned(vehicle_id=vehicle_id, provider_user_id=provider.id)
        if vehicle is None:
            raise NotFoundError("Vehicle not found.", code="VEHICLE_NOT_FOUND")
        return vehicle