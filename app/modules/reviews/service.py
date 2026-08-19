"""Review business logic."""

from __future__ import annotations

from app.core.config import Settings
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.modules.reviews.models import Review
from app.modules.reviews.repository import ReviewRepository
from app.modules.reviews.schemas import ReviewCreateIn, ReviewOut, ReviewPage
from app.modules.vehicles.models import Vehicle
from app.modules.vehicles.repository import VehicleRepository

log = get_logger(__name__)


class ReviewService:
    def __init__(self, *, repo: ReviewRepository, settings: Settings) -> None:
        self.repo = repo
        self.settings = settings

    async def create_review(self, *, reviewer, vehicle_id, payload: ReviewCreateIn) -> ReviewOut:
        vehicle_repo = VehicleRepository(self.repo.db)
        vehicle = await vehicle_repo.get_public_by_id(vehicle_id)
        if vehicle is None:
            raise NotFoundError("Vehicle not found.", code="VEHICLE_NOT_FOUND")

        if vehicle.provider_user_id == reviewer.id:
            raise BadRequestError(
                "You cannot review your own vehicle.",
                code="CANNOT_REVIEW_OWN_VEHICLE",
            )

        existing = await self.repo.user_reviewed_vehicle(
            user_id=reviewer.id, vehicle_id=vehicle_id
        )
        if existing is not None:
            raise ConflictError(
                "You have already reviewed this vehicle.",
                code="REVIEW_ALREADY_EXISTS",
            )

        review = Review(
            vehicle_id=vehicle_id,
            reviewer_user_id=reviewer.id,
            rating=payload.rating,
            comment=payload.comment,
        )
        review = await self.repo.create(review=review)

        log.info(
            "review_created",
            review_id=str(review.id),
            vehicle_id=str(vehicle_id),
            reviewer_id=str(reviewer.id),
            rating=review.rating,
        )

        await self._recalc_vehicle_rating(vehicle_id)

        return ReviewOut.from_model(review)

    async def list_reviews(
        self, *, vehicle_id, limit: int, offset: int
    ) -> ReviewPage:
        items, total = await self.repo.get_for_vehicle(
            vehicle_id=vehicle_id,
            limit=limit,
            offset=offset,
        )
        return ReviewPage(
            items=[ReviewOut.from_model(r) for r in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def _recalc_vehicle_rating(self, vehicle_id) -> None:
        """Recalculate average rating for a vehicle.

        MVP: log the new average. When a dedicated vehicle_stats table or
        materialised view is added, persist here instead.
        """
        result = await self.repo.db.execute(
            select(
                Review.vehicle_id,
                Review.rating,
            ).where(Review.vehicle_id == vehicle_id)
        )
        rows = result.all()
        if not rows:
            log.info("rating_recalc", vehicle_id=str(vehicle_id), avg=None, count=0)
            return
        avg = sum(row.rating for row in rows) / len(rows)
        log.info(
            "rating_recalc",
            vehicle_id=str(vehicle_id),
            avg=round(avg, 2),
            count=len(rows),
        )