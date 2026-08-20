"""Notification business logic."""

from __future__ import annotations

from app.core.logging import get_logger
from app.modules.notifications.models import Notification, NotificationType
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.schemas import NotificationOut, NotificationPage
from app.modules.users.models import User

log = get_logger(__name__)


class NotificationService:
    def __init__(self, *, repo: NotificationRepository) -> None:
        self.repo = repo

    async def list_notifications(
        self,
        user: User,
        *,
        is_read: bool | None,
        limit: int,
        offset: int,
    ) -> NotificationPage:
        items, total = await self.repo.list_for_user(
            user_id=user.id,
            is_read=is_read,
            limit=limit,
            offset=offset,
        )
        return NotificationPage(
            items=[NotificationOut.model_validate(n) for n in items],
            total=total,
        )

    async def mark_read(self, user: User, *, notification_id) -> None:
        await self.repo.mark_read(notification_id=notification_id, user_id=user.id)
        log.info(
            "notification_marked_read",
            user_id=str(user.id),
            notification_id=str(notification_id),
        )

    async def mark_all_read(self, user: User) -> int:
        count = await self.repo.mark_all_read(user_id=user.id)
        log.info("notifications_mark_all_read", user_id=str(user.id), count=count)
        return count

    async def notify(
        self,
        user_id,
        *,
        type: NotificationType,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> Notification:
        """Create a notification for one user. Used by other modules.

        Safe to call from anywhere in a request: the surrounding transaction
        commits (or rolls back) the notification along with the change that
        triggered it.
        """
        return await self._notify(
            user_id,
            type=type,
            title=title,
            body=body,
            data=data,
        )

    async def _notify(
        self,
        user_id,
        *,
        type: NotificationType,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> Notification:
        notification = await self.repo.create(
            user_id=user_id,
            type=type,
            title=title,
            body=body,
            data=data,
        )
        log.info(
            "notification_created",
            user_id=str(user_id),
            type=type.value,
            notification_id=str(notification.id),
        )
        return notification
