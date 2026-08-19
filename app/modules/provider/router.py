"""Provider dashboard summary endpoints."""

from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.modules.auth.dependencies import require_role
from app.modules.bookings.models import BookingStatus
from app.modules.bookings.repository import BookingRepository
from app.modules.users.models import User, UserRole
from app.modules.vehicles.models import Vehicle
from app.modules.vehicles.repository import VehicleRepository

log = get_logger(__name__)
router = APIRouter()


class ProviderSummaryOut(BaseModel):
    total_vehicles: int
    active_rentals: int
    completed_rentals: int
    lifetime_earnings_paise: int
    lifetime_earnings_rupees: float


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
    vehicle_repo = VehicleRepository(db)
    booking_repo = BookingRepository(db)

    total_vehicles = await vehicle_repo.count_for_provider(provider.id)
    active = await booking_repo.count_for_provider(provider.id, status="ACTIVE")
    completed = await booking_repo.count_for_provider(provider.id, status="COMPLETED")
    earnings = await booking_repo.earnings_for_provider(provider.id)

    return ProviderSummaryOut(
        total_vehicles=total_vehicles,
        active_rentals=active,
        completed_rentals=completed,
        lifetime_earnings_paise=earnings,
        lifetime_earnings_rupees=earnings / 100.0,
    )