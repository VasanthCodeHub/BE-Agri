from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_db
from app.modules.auth.dependencies import get_active_role, get_current_user
from app.modules.auth.schemas import UserOut
from app.modules.users.models import User, UserRole

log = get_logger(__name__)
router = APIRouter()


@router.get(
    "/me",
    response_model=UserOut,
    tags=["profile"],
    summary="Who am I? (session check)",
    responses={401: {"description": "Missing, invalid or expired token"}},
)
async def me(
    user: User = Depends(get_current_user),
    active_role: UserRole = Depends(get_active_role),
) -> UserOut:
    """Return the current user — the app's session check.

    Identical to `GET /auth/me`; this shorter path is the one the new app
    builds call. `onboarding.needs_profile_completion` tells the app whether to
    route the user to a profile-completion screen first.
    """
    return UserOut.from_user(user, active_role=active_role)


class ProfileUpdateIn(BaseModel):
    """Body for PATCH /profile/me. Every field is optional — send only what changed.

    The API accepts `null` for a string field to mean "leave it unchanged",
    and an empty string to mean "clear it", so the app can distinguish
    "don't touch" from "wipe".
    """

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    email: str | None = Field(default=None, max_length=254)
    address: str | None = Field(default=None, max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @field_validator("full_name", "address")
    @classmethod
    def _collapse_whitespace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned or None

    @field_validator("email")
    @classmethod
    def _normalise_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        email = value.strip().lower()
        return email or None


class ProfileOut(BaseModel):
    full_name: str | None
    email: str | None
    address: str | None
    latitude: float | None
    longitude: float | None


@router.patch(
    "/me",
    response_model=ProfileOut,
    tags=["profile"],
    summary="Update my profile",
    responses={401: {"description": "Missing, invalid or expired token"}},
)
async def update_profile(
    payload: ProfileUpdateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProfileOut:
    """Update your own profile. Partial — send only what changed.

    ```json
    { "email": "farmer@example.com", "address": "12 Gandhi St, Sulur" }
    ```

    The phone number stays the identity and cannot be changed here.
    """
    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.email is not None:
        user.email = payload.email
    if payload.address is not None:
        user.address = payload.address
    if payload.latitude is not None:
        user.latitude = payload.latitude
    if payload.longitude is not None:
        user.longitude = payload.longitude
    await db.flush()
    await db.refresh(user)
    log.info(
        "profile_updated",
        user_id=str(user.id),
        fields=["full_name", "email", "address", "latitude", "longitude"],
    )
    return ProfileOut(
        full_name=user.full_name,
        email=user.email,
        address=user.address,
        latitude=user.latitude,
        longitude=user.longitude,
    )
