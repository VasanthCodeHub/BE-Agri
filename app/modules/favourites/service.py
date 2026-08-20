"""Favourite business logic."""

from __future__ import annotations

from app.core.config import Settings
from app.core.logging import get_logger
from app.modules.favourites.repository import FavouriteRepository
from app.modules.favourites.schemas import FavouriteOut

log = get_logger(__name__)


class FavouriteService:
    def __init__(self, *, repo: FavouriteRepository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings

    async def toggle(self, user, *, vehicle_id) -> dict:
        already = await self.repo.is_favourited(user_id=user.id, vehicle_id=vehicle_id)
        if already:
            await self.repo.remove(user_id=user.id, vehicle_id=vehicle_id)
            log.info(
                "favourite_removed",
                user_id=str(user.id),
                vehicle_id=str(vehicle_id),
            )
            return {"favourited": False}
        await self.repo.add(user_id=user.id, vehicle_id=vehicle_id)
        log.info(
            "favourite_added",
            user_id=str(user.id),
            vehicle_id=str(vehicle_id),
        )
        return {"favourited": True}

    async def list_favourites(self, user, *, limit: int, offset: int) -> list[FavouriteOut]:
        items, _total = await self.repo.list_for_user(
            user_id=user.id,
            limit=limit,
            offset=offset,
        )
        return [FavouriteOut.model_validate(fav) for fav in items]
