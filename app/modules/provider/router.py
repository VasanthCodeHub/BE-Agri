"""Provider dashboard summary endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_db
from app.modules.auth.dependencies import require_role
from app.modules.contact.repository import ContactCallRepository
from app.modules.favourites.repository import FavouriteRepository
from app.modules.users.models import User, UserRole
from app.modules.vehicles.repository import VehicleRepository

log = get_logger(__name__)
router = APIRouter()


class ProviderSummaryOut(BaseModel):
    """The provider's dashboard, scoped to THEIR listings only.

    The product has no bookings, so the interesting numbers are interest and
    reputation: how many favourites and calls the listings draw, and what
    renters have said about them.
    """

    total_vehicles: int = Field(description="Live listings (not soft-deleted).")
    available_vehicles: int = Field(description="Currently on the public feed.")
    unavailable_vehicles: int = Field(description="Toggled off by the provider.")
    favourite_count: int = Field(description="Favourites across all of the provider's vehicles.")
    contact_call_count: int = Field(description="Calls initiated toward the provider's vehicles.")
    review_count: int = Field(description="Reviews written about the provider's vehicles.")
    average_rating: float | None = Field(
        description="Mean star rating across reviews, 1-5. Null when no reviews exist."
    )


@router.get(
    "/provider/summary",
    response_model=ProviderSummaryOut,
    tags=["provider"],
    summary="Provider dashboard stats",
    operation_id="provider_dashboard_summary",
)
async def provider_summary(
    provider: User = Depends(require_role(UserRole.PROVIDER)),
    db: AsyncSession = Depends(get_db),
) -> ProviderSummaryOut:
    """Statistics for the authenticated provider's own listings.

    Every number is derived from the provider's live vehicles, never from
    anyone else's. Requires the PROVIDER role.
    """
    vehicles = VehicleRepository(db)
    favourites = FavouriteRepository(db)
    calls = ContactCallRepository(db)

    total_vehicles = await vehicles.count_for_provider(provider_user_id=provider.id)
    available = await vehicles.count_available_for_provider(provider_user_id=provider.id)
    owned_ids = await vehicles.list_owned_ids(provider_user_id=provider.id)

    favourite_count = await favourites.count_for_vehicles(vehicle_ids=owned_ids)
    call_count = await calls.count_for_provider_vehicles(
        provider_user_id=provider.id, vehicle_ids=owned_ids
    )

    stats = await vehicles.review_stats(owned_ids)
    review_count = sum(count for _, count in stats.values())
    rating_values = [rating for rating, _ in stats.values() if rating is not None]
    average_rating = round(sum(rating_values) / len(rating_values), 2) if rating_values else None

    log.info(
        "provider_summary",
        provider_id=str(provider.id),
        total_vehicles=total_vehicles,
    )

    return ProviderSummaryOut(
        total_vehicles=total_vehicles,
        available_vehicles=available,
        unavailable_vehicles=total_vehicles - available,
        favourite_count=favourite_count,
        contact_call_count=call_count,
        review_count=review_count,
        average_rating=average_rating,
    )
