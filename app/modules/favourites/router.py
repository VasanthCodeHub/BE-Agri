"""Favourite endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.modules.auth.dependencies import require_role
from app.modules.favourites.repository import FavouriteRepository
from app.modules.favourites.schemas import FavouriteOut
from app.modules.favourites.service import FavouriteService
from app.modules.users.models import User, UserRole

log = get_logger(__name__)
router = APIRouter()


def get_favourite_service(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> FavouriteService:
    return FavouriteService(repo=FavouriteRepository(db), settings=settings)


@router.post(
    "/vehicles/{vehicle_id}/favourite",
    response_model=dict,
    tags=["favourites"],
    summary="Toggle favourite on a vehicle",
    operation_id="favourites_toggle",
)
async def toggle_favourite_api(
    vehicle_id: uuid.UUID,
    user: User = Depends(require_role(UserRole.USER)),
    service: FavouriteService = Depends(get_favourite_service),
) -> dict:
    return await service.toggle(user=user, vehicle_id=vehicle_id)


@router.get(
    "/favourites",
    response_model=list[FavouriteOut],
    tags=["favourites"],
    summary="My favourited vehicles",
    operation_id="favourites_list_mine",
)
async def list_favourites_api(
    user: User = Depends(require_role(UserRole.USER)),
    service: FavouriteService = Depends(get_favourite_service),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[FavouriteOut]:
    return await service.list_favourites(user=user, limit=limit, offset=offset)
