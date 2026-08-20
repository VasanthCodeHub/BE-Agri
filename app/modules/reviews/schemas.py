"""Review schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.modules.reviews.models import Review


class ReviewCreateIn(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    comment: str | None = Field(default=None, max_length=500)

    @field_validator("comment")
    @classmethod
    def _strip_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        return cleaned[:500]


class ReviewOut(BaseModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID
    reviewer_id: uuid.UUID
    reviewer_name: str | None
    rating: int
    comment: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, review: Review) -> ReviewOut:
        return cls(
            id=review.id,
            vehicle_id=review.vehicle_id,
            reviewer_id=review.reviewer_user_id,
            reviewer_name=review.reviewer.full_name if review.reviewer else None,
            rating=review.rating,
            comment=review.comment,
            created_at=review.created_at,
        )


class ReviewPage(BaseModel):
    items: list[ReviewOut]
    total: int
    limit: int
    offset: int
