"""Data access for contact calls."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.contact.models import ContactCall


class ContactCallRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self, *, caller_user_id: uuid.UUID, provider_user_id: uuid.UUID, vehicle_id: uuid.UUID
    ) -> ContactCall:
        call = ContactCall(
            caller_user_id=caller_user_id,
            provider_user_id=provider_user_id,
            vehicle_id=vehicle_id,
        )
        self.db.add(call)
        await self.db.flush()
        await self.db.refresh(call, attribute_names=["id", "created_at", "provider", "vehicle"])
        return call

    async def count_for_provider(self, provider_user_id: uuid.UUID) -> int:
        """How many calls this provider's listings have received."""
        result = await self.db.execute(
            select(func.count())
            .select_from(ContactCall)
            .where(ContactCall.provider_user_id == provider_user_id)
        )
        return int(result.scalar() or 0)

    async def count_for_provider_vehicles(
        self, *, provider_user_id: uuid.UUID, vehicle_ids: list[uuid.UUID]
    ) -> int:
        if not vehicle_ids:
            return 0
        result = await self.db.execute(
            select(func.count())
            .select_from(ContactCall)
            .where(
                ContactCall.provider_user_id == provider_user_id,
                ContactCall.vehicle_id.in_(vehicle_ids),
            )
        )
        return int(result.scalar() or 0)
