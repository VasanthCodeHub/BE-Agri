from __future__ import annotations

from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_db
from app.modules.auth.dependencies import require_role
from app.modules.users.models import User, UserRole

log = get_logger(__name__)
router = APIRouter()


class ProfileUpdateIn(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    # future: email, location, etc.


@router.patch(
    "/me",
    response_model=dict,
    tags=["profile"],
    summary="Update my profile",
)
async def update_profile(
    payload: ProfileUpdateIn,
    user: User = Depends(require_role(UserRole.RENTER)),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if payload.full_name is not None:
        user.full_name = payload.full_name.strip()
        await db.flush()
        await db.refresh(user)
        log.info("profile_updated", user_id=str(user.id), full_name=user.full_name)
    return {"full_name": user.full_name, "message": "Profile updated successfully."}