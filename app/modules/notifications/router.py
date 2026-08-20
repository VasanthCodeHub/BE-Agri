"""Notification endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.schemas import NotificationOut, NotificationPage
from app.modules.notifications.service import NotificationService
from app.modules.users.models import User

log = get_logger(__name__)
router = APIRouter()


def get_notification_service(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> NotificationService:
    return NotificationService(repo=NotificationRepository(db))


@router.get(
    "/notifications",
    response_model=NotificationPage,
    tags=["notifications"],
    summary="My notifications",
    operation_id="notifications_list_mine",
)
async def list_notifications(
    user: User = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
    is_read: bool | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> NotificationPage:
    return await service.list_notifications(user=user, is_read=is_read, limit=limit, offset=offset)


@router.patch(
    "/notifications/{notification_id}/read",
    response_model=NotificationOut,
    tags=["notifications"],
    summary="Mark notification as read",
    operation_id="notifications_mark_read",
)
async def mark_read(
    notification_id: uuid.UUID,
    user: User = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
) -> NotificationOut:
    await service.mark_read(user=user, notification_id=notification_id)
    notif = await service.repo.get_by_id(notification_id)
    if notif is None:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("Notification not found.", code="NOTIFICATION_NOT_FOUND")
    return NotificationOut.model_validate(notif)


@router.patch(
    "/notifications/read-all",
    response_model=dict,
    tags=["notifications"],
    summary="Mark all notifications as read",
    operation_id="notifications_mark_all_read",
)
async def mark_all_read(
    user: User = Depends(get_current_user),
    service: NotificationService = Depends(get_notification_service),
) -> dict:
    count = await service.mark_all_read(user=user)
    return {"marked_read": count}
