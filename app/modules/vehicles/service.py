"""Vehicle listing business logic."""

from __future__ import annotations

import uuid

from app.core.config import Settings
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.integrations.cloudinary import is_in_our_folder
from app.modules.masters.service import MasterService
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
    def __init__(
        self,
        *,
        repo: VehicleRepository,
        settings: Settings,
        masters: MasterService | None = None,
    ) -> None:
        self.repo = repo
        self.settings = settings
        #: Built lazily from the same session when a handler needs it — the
        #: router wires it explicitly, but tests may construct the service
        #: without one.
        self.masters = masters

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
        self._check_images_are_ours(payload.image_public_ids)

        if await self.repo.registration_is_taken(payload.registration_number):
            raise ConflictError(
                "A vehicle with this registration number is already listed.",
                code="REGISTRATION_ALREADY_LISTED",
                details={"registration_number": payload.registration_number},
            )

        brand, model = payload.brand, payload.model
        manufacturer_id = payload.manufacturer_id
        model_id = payload.model_id
        variant_id = payload.variant_id
        # The free-text path submits these; the master path derives them. The
        # model row is the source of truth: a conflicting client-sent type is
        # rejected by resolve_references, and the master's values are stored.
        vehicle_type_code = payload.vehicle_type_code
        fuel_type = payload.fuel_type
        power_hp = payload.power_hp
        if self.masters is not None and any((manufacturer_id, model_id, variant_id)):
            resolved = await self.masters.resolve_references(
                manufacturer_id=manufacturer_id,
                model_id=model_id,
                variant_id=variant_id,
                vehicle_type_code=vehicle_type_code,
            )
            # The master rows are the source of truth for the display names.
            brand = resolved.brand or brand
            model = resolved.model_name or model
            manufacturer_id = resolved.manufacturer_id
            model_id = resolved.model_id
            variant_id = resolved.variant_id
            if resolved.vehicle_type_code is not None:
                vehicle_type_code = resolved.vehicle_type_code
            if resolved.fuel_type is not None:
                fuel_type = resolved.fuel_type
            if resolved.power_hp is not None:
                power_hp = resolved.power_hp

        if vehicle_type_code is None:
            raise BadRequestError(
                "A vehicle type could not be determined. Choose a model from "
                "GET /vehicle-masters or send vehicle_type_code.",
                code="VEHICLE_TYPE_UNKNOWN",
            )

        vehicle_type = await self.repo.get_type_by_code(vehicle_type_code)
        if vehicle_type is None:
            raise BadRequestError(
                f"Unknown vehicle type {vehicle_type_code!r}. "
                "Fetch the valid codes from GET /vehicle-types.",
                code="VEHICLE_TYPE_UNKNOWN",
                details={"vehicle_type_code": vehicle_type_code},
            )

        vehicle = Vehicle(
            provider_user_id=provider.id,
            vehicle_type_id=vehicle_type.id,
            name=payload.name,
            brand=brand,
            model=model,
            manufacture_year=payload.manufacture_year,
            registration_number=payload.registration_number,
            rc_number=payload.rc_number,
            rc_document_public_id=payload.rc_document_public_id,
            engine_number=payload.engine_number,
            chassis_number=payload.chassis_number,
            note=payload.note,
            price_amount=payload.price_amount,
            price_unit=payload.price_unit,
            location_text=payload.location_text,
            latitude=payload.latitude,
            longitude=payload.longitude,
            fuel_type=fuel_type,
            power_hp=power_hp,
            transmission=payload.transmission,
            manufacturer_id=manufacturer_id,
            model_id=model_id,
            variant_id=variant_id,
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

        stats = await self.repo.review_stats([vehicle.id for vehicle, _distance in rows])
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

        new_registration = changes.get("registration_number")
        if (
            new_registration is not None
            and new_registration != vehicle.registration_number
            and await self.repo.registration_is_taken(
                new_registration, exclude_vehicle_id=vehicle.id
            )
        ):
            raise ConflictError(
                "A vehicle with this registration number is already listed.",
                code="REGISTRATION_ALREADY_LISTED",
                details={"registration_number": new_registration},
            )

        await self._resolve_master_changes(vehicle=vehicle, changes=changes, type_code=type_code)

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

    async def _resolve_master_changes(
        self,
        *,
        vehicle: Vehicle,
        changes: dict,
        type_code: str | None,
    ) -> None:
        """Re-validate master references after an edit.

        Only runs when the request actually touched the master fields. Values
        not in the request keep the vehicle's current ones, so editing the
        price can never accidentally break a valid model link.

        When the model or variant changes, the new model defines the vehicle
        type, fuel and power — the client never submits those independently
        (and if it did, a conflicting type is rejected exactly as on create).
        """
        if self.masters is None:
            return
        if not any(field in changes for field in ("manufacturer_id", "model_id", "variant_id")):
            return

        resolved = await self.masters.resolve_references(
            manufacturer_id=changes.get("manufacturer_id", vehicle.manufacturer_id),
            model_id=changes.get("model_id", vehicle.model_id),
            variant_id=changes.get("variant_id", vehicle.variant_id),
            vehicle_type_code=type_code,
        )

        changes["manufacturer_id"] = resolved.manufacturer_id
        changes["model_id"] = resolved.model_id
        changes["variant_id"] = resolved.variant_id
        if resolved.brand is not None:
            changes["brand"] = resolved.brand
        if resolved.model_name is not None:
            changes["model"] = resolved.model_name
        if resolved.vehicle_type_id is not None:
            # The master model defines type/fuel/power — overwrite any
            # client-submitted values for them.
            changes["vehicle_type_id"] = resolved.vehicle_type_id
            changes["fuel_type"] = resolved.fuel_type
            changes["power_hp"] = resolved.power_hp
