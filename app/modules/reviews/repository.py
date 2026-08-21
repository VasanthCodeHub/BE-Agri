"""Review repository — session-scoped queries, no commits."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.reviews.models import Review


class ReviewRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, *, review: Review) -> Review:
        """Persist review and eagerly load its reviewer so the service can
        serialise the author's name without a second query."""
        self.db.add(review)
        await self.db.flush()
        await self.db.refresh(
            review,
            attribute_names=[
                "id",
                "created_at",
                "updated_at",
                "vehicle",
                "reviewer",
            ],
        )
        return review

    async def get_for_vehicle(
        self,
        *,
        vehicle_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> tuple[list[Review], int]:
        base = (
            select(Review)
            .options(selectinload(Review.reviewer))
            .where(Review.vehicle_id == vehicle_id)
        )
        return await self._page(base, limit=limit, offset=offset)

    async def get_by_id(self, review_id: uuid.UUID) -> Review | None:
        result = await self.db.execute(
            select(Review).options(selectinload(Review.reviewer)).where(Review.id == review_id)
        )
        return result.scalar_one_or_none()

    async def user_reviewed_vehicle(
        self,
        *,
        user_id: uuid.UUID,
        vehicle_id: uuid.UUID,
    ) -> Review | None:
        result = await self.db.execute(
            select(Review).where(
                Review.vehicle_id == vehicle_id,
                Review.reviewer_user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    # -----------------------------------------------------------------------
    # Pagination
    # -----------------------------------------------------------------------
    async def _page(
        self,
        base: Select[Any],
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[Review], int]:
        count_subq = base.order_by(None).subquery()
        total = await self.db.scalar(select(func.count()).select_from(count_subq))
        ordered = base.order_by(Review.created_at.desc(), Review.id).limit(limit).offset(offset)
        result = await self.db.execute(ordered)
        items = list(result.scalars().unique().all())
        return items, int(total or 0)
