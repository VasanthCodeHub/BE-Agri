"""Notification repository — session-scoped queries, no commits."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import utc_now
from app.modules.notifications.models import Notification, NotificationType


class NotificationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        type: NotificationType,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> Notification:
        notification = Notification(
            user_id=user_id,
            type=type,
            title=title,
            body=body,
            data=data,
        )
        self.db.add(notification)
        await self.db.flush()
        await self.db.refresh(
            notification,
            attribute_names=[
                "id",
                "created_at",
                "updated_at",
                "is_read",
                "read_at",
            ],
        )
        return notification

    async def mark_read(self, *, notification_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await self.db.execute(
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=utc_now())
        )
        await self.db.flush()

    async def mark_all_read(self, *, user_id: uuid.UUID) -> int:
        result = await self.db.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=utc_now())
        )
        await self.db.flush()
        # result is a CursorResult-like object without a rowcount attribute on
        # the generic Result type, so we cast to access it.
        from sqlalchemy import CursorResult
        from typing import cast
        return cast("CursorResult[Any]", result).rowcount or 0

    async def list_for_user(
        self,
        *,
        user_id: uuid.UUID,
        is_read: bool | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Notification], int]:
        base = (
            select(Notification)
            .where(Notification.user_id == user_id)
        )
        if is_read is not None:
            base = base.where(Notification.is_read.is_(is_read))
        return await self._page(base, limit=limit, offset=offset)

    async def get_by_id(self, notification_id: uuid.UUID) -> Notification | None:
        result = await self.db.execute(
            select(Notification).where(Notification.id == notification_id)
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
    ) -> tuple[list[Notification], int]:
        count_subq = base.order_by(None).subquery()
        total = await self.db.scalar(select(1).select_from(count_subq).count())
        ordered = base.order_by(Notification.created_at.desc(), Notification.id)
        result = await self.db.execute(ordered.limit(limit).offset(offset))
        items = list(result.scalars().unique().all())
        return items, int(total or 0)