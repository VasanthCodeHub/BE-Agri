"""Favourite repository — session-scoped queries, no commits."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.favourites.models import Favourite


class FavouriteRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def add(self, *, user_id: uuid.UUID, vehicle_id: uuid.UUID) -> Favourite:
        existing = await self.is_favourited(user_id=user_id, vehicle_id=vehicle_id)
        if existing:
            raise RuntimeError(
                "Favourite already exists — call is_favourited before add."
            )

        favourite = Favourite(user_id=user_id, vehicle_id=vehicle_id)
        self.db.add(favourite)
        await self.db.flush()
        await self.db.refresh(
            favourite,
            attribute_names=["id", "created_at", "vehicle", "user"],
        )
        return favourite

    async def remove(self, *, user_id: uuid.UUID, vehicle_id: uuid.UUID) -> None:
        result = await self.db.execute(
            select(Favourite).where(
                Favourite.user_id == user_id,
                Favourite.vehicle_id == vehicle_id,
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            await self.db.delete(row)
            await self.db.flush()

    async def is_favourited(self, *, user_id: uuid.UUID, vehicle_id: uuid.UUID) -> bool:
        result = await self.db.execute(
            select(Favourite.id).where(
                Favourite.user_id == user_id,
                Favourite.vehicle_id == vehicle_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def list_for_user(
        self,
        *,
        user_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> tuple[list[Favourite], int]:
        base = (
            select(Favourite)
            .options(selectinload(Favourite.vehicle))
            .where(Favourite.user_id == user_id)
        )
        return await self._page(base, limit=limit, offset=offset)

    # -----------------------------------------------------------------------
    # Pagination
    # -----------------------------------------------------------------------
    async def _page(
        self,
        base: Select[Any],
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[Favourite], int]:
        count_subq = base.order_by(None).subquery()
        total = await self.db.scalar(select(1).select_from(count_subq).count())
        # Re-run with order and pagination
        ordered = base.order_by(Favourite.created_at.desc(), Favourite.id)
        result = await self.db.execute(ordered.limit(limit).offset(offset))
        items = list(result.scalars().unique().all())
        return items, int(total or 0)