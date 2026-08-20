"""Contact/call business logic."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol

from app.core.exceptions import NotFoundError, ServiceUnavailableError
from app.core.logging import get_logger
from app.modules.contact.repository import ContactCallRepository
from app.modules.contact.schemas import ContactCallOut
from app.modules.notifications.models import NotificationType
from app.modules.vehicles.repository import VehicleRepository

if TYPE_CHECKING:
    from app.modules.users.models import User

log = get_logger(__name__)


class Notifier(Protocol):
    """Anything that can create a notification for a user."""

    async def notify(
        self,
        user_id: uuid.UUID,
        *,
        type: NotificationType,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> object: ...


class ContactService:
    def __init__(
        self,
        *,
        repo: ContactCallRepository,
        vehicles: VehicleRepository,
        notifier: Notifier | None = None,
    ) -> None:
        self.repo = repo
        self.vehicles = vehicles
        self.notifier = notifier

    async def initiate_call(self, *, caller: User, vehicle_id: uuid.UUID) -> ContactCallOut:
        """Record the caller's intent and hand them the provider's number.

        The vehicle must be on the public feed — a caller cannot reach a
        provider whose listing is hidden. The number is returned only here,
        never on the feed, and only after the call is recorded.
        """
        vehicle = await self.vehicles.get_public_by_id(vehicle_id)
        if vehicle is None:
            raise NotFoundError("Vehicle not found or not available.", code="VEHICLE_NOT_FOUND")

        provider = vehicle.provider
        if provider is None or not provider.phone_e164:
            raise ServiceUnavailableError(
                "The provider has no contact number on file.",
                code="PROVIDER_NOT_CONTACTABLE",
            )

        call = await self.repo.create(
            caller_user_id=caller.id,
            provider_user_id=provider.id,
            vehicle_id=vehicle_id,
        )

        log.info(
            "call_initiated",
            call_id=str(call.id),
            caller_id=str(caller.id),
            provider_id=str(provider.id),
            vehicle_id=str(vehicle_id),
        )

        if self.notifier is not None:
            await self.notifier.notify(
                provider.id,
                type=NotificationType.CALL_INITIATED,
                title="Someone wants to call you",
                body=f"{caller.full_name or 'A user'} wants to call about "
                f"{vehicle.name} ({vehicle.vehicle_type.name_en}).",
                data={"call_id": str(call.id), "vehicle_id": str(vehicle_id)},
            )

        return ContactCallOut(
            call_id=call.id,
            provider_id=provider.id,
            provider_name=provider.full_name,
            provider_phone=provider.phone_e164,
            vehicle_id=vehicle_id,
            created_at=call.created_at,
            message="Call the provider directly to negotiate terms.",
        )
