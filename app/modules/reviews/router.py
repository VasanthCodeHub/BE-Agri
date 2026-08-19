"""Review endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.db.session import get_db
from app.modules.auth.dependencies import require_role
from app.modules.reviews.models import Review
from app.modules.reviews.repository import ReviewRepository
from app.modules.reviews.schemas import ReviewCreateIn, ReviewOut, ReviewPage
from app.modules.reviews.service import ReviewService
from app.modules.users.models import User, UserRole

log = get_logger(__name__)
router = APIRouter()


def get_review_service(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ReviewService:
    return ReviewService(repo=ReviewRepository(db), settings=settings)


@router.get(
    "/vehicles/{vehicle_id}/reviews",
    response_model=ReviewPage,
    tags=["reviews"],
    summary="Reviews for a vehicle",
    include_in_schema=True,
    operation_id="reviews_list_for_vehicle",
)
async def list_reviews(
    vehicle_id: uuid.UUID,
    service: ReviewService = Depends(get_review_service),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ReviewPage:
    return await service.list_reviews(vehicle_id=vehicle_id, limit=limit, offset=offset)


@router.post(
    "/vehicles/{vehicle_id}/reviews",
    response_model=ReviewOut,
    status_code=status.HTTP_201_CREATED,
    tags=["reviews"],
    summary="Write a review for a vehicle",
    operation_id="reviews_create",
)
async def create_review(
    vehicle_id: uuid.UUID,
    payload: ReviewCreateIn,
    renter: User = Depends(require_role(UserRole.RENTER)),
    service: ReviewService = Depends(get_review_service),
) -> ReviewOut:
    return await service.create_review(reviewer=renter, vehicle_id=vehicle_id, payload=payload)