"""Vehicle listing endpoints.

Two groups, and the difference between them is who may call:

  POST   /provider/vehicles                        provider only, own listings
  GET    /provider/vehicles                        provider only, own listings
  GET    /provider/vehicles/{id}                   provider only, own listings
  PATCH  /provider/vehicles/{id}                   provider only, own listings
  PATCH  /provider/vehicles/{id}/availability      provider only, own listings
  DELETE /provider/vehicles/{id}                   provider only, own listings

  GET    /vehicles                                 public — no token needed
  GET    /vehicles/{id}                            public — no token needed
  GET    /vehicle-types                            public — the app's picker

The public feed is deliberately unauthenticated (browsing is anonymous;
login is required only to call a provider — see the contact module).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.modules.auth.dependencies import require_role
from app.modules.masters.repository import MasterRepository
from app.modules.masters.service import MasterService
from app.modules.users.models import User, UserRole
from app.modules.vehicles.repository import VehicleRepository
from app.modules.vehicles.schemas import (
    VehicleCardOut,
    VehicleCardPage,
    VehicleCreateIn,
    VehicleOut,
    VehiclePage,
    VehicleSearchParams,
    VehicleTypeOut,
    VehicleUpdateIn,
)
from app.modules.vehicles.service import VehicleService

router = APIRouter()

log = get_logger(__name__)

provider_only = require_role(UserRole.PROVIDER)


def get_vehicle_service(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> VehicleService:
    return VehicleService(
        repo=VehicleRepository(db),
        settings=settings,
        masters=MasterService(repo=MasterRepository(db)),
    )


def _build_search_params(
    type_code: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    radius_km: float | None = None,
    q: str | None = None,
    max_price: float | None = None,
    available_only: bool = True,
    sort: str = "newest",
) -> VehicleSearchParams:
    return VehicleSearchParams(
        type_code=type_code.strip().upper() if type_code else None,
        lat=lat,
        lng=lng,
        radius_km=radius_km,
        q=q.strip() if q else None,
        max_price=int(max_price) if max_price is not None else None,
        available_only=available_only,
        sort=sort,
    )


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------
@router.get(
    "/vehicle-types",
    response_model=list[VehicleTypeOut],
    tags=["vehicles"],
    summary="List vehicle types",
)
async def list_vehicle_types(
    service: VehicleService = Depends(get_vehicle_service),
) -> list[VehicleTypeOut]:
    return await service.list_types()


# ---------------------------------------------------------------------------
# Provider — my listings
# ---------------------------------------------------------------------------
@router.post(
    "/provider/vehicles",
    response_model=VehicleOut,
    status_code=status.HTTP_201_CREATED,
    tags=["vehicles"],
    summary="Add a vehicle",
    responses={
        400: {"description": "Unknown vehicle type"},
        401: {"description": "Missing or invalid token"},
        403: {"description": "Caller does not hold the PROVIDER role"},
        409: {"description": "That registration number is already listed"},
        422: {"description": "A field is missing or invalid"},
    },
)
async def create_vehicle(
    payload: VehicleCreateIn,
    provider: User = Depends(provider_only),
    service: VehicleService = Depends(get_vehicle_service),
) -> VehicleOut:
    return await service.create_vehicle(provider=provider, payload=payload)


@router.get(
    "/provider/vehicles",
    response_model=VehiclePage,
    tags=["vehicles"],
    summary="My vehicles",
)
async def list_my_vehicles(
    provider: User = Depends(provider_only),
    service: VehicleService = Depends(get_vehicle_service),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> VehiclePage:
    return await service.list_my_vehicles(provider=provider, limit=limit, offset=offset)


@router.get(
    "/provider/vehicles/{vehicle_id}",
    response_model=VehicleOut,
    tags=["vehicles"],
    summary="One of my vehicles",
)
async def get_my_vehicle(
    vehicle_id: uuid.UUID,
    provider: User = Depends(provider_only),
    service: VehicleService = Depends(get_vehicle_service),
) -> VehicleOut:
    return await service.get_my_vehicle(provider=provider, vehicle_id=vehicle_id)


@router.patch(
    "/provider/vehicles/{vehicle_id}",
    response_model=VehicleOut,
    tags=["vehicles"],
    summary="Edit a vehicle",
)
async def update_vehicle(
    vehicle_id: uuid.UUID,
    payload: VehicleUpdateIn,
    provider: User = Depends(provider_only),
    service: VehicleService = Depends(get_vehicle_service),
) -> VehicleOut:
    return await service.update_vehicle(provider=provider, vehicle_id=vehicle_id, payload=payload)


@router.patch(
    "/provider/vehicles/{vehicle_id}/availability",
    response_model=VehicleOut,
    tags=["vehicles"],
    summary="Mark a vehicle available or unavailable",
)
async def set_availability(
    vehicle_id: uuid.UUID,
    is_available: bool = Query(description="true puts it back on the public feed."),
    provider: User = Depends(provider_only),
    service: VehicleService = Depends(get_vehicle_service),
) -> VehicleOut:
    return await service.set_availability(
        provider=provider, vehicle_id=vehicle_id, is_available=is_available
    )


@router.delete(
    "/provider/vehicles/{vehicle_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["vehicles"],
    summary="Delete a vehicle",
)
async def delete_vehicle(
    vehicle_id: uuid.UUID,
    provider: User = Depends(provider_only),
    service: VehicleService = Depends(get_vehicle_service),
) -> Response:
    await service.delete_vehicle(provider=provider, vehicle_id=vehicle_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Public feed
# ---------------------------------------------------------------------------
@router.get(
    "/vehicles",
    response_model=VehicleCardPage,
    tags=["vehicles"],
    summary="Browse all available vehicles",
)
async def list_available_vehicles(
    service: VehicleService = Depends(get_vehicle_service),
    type_code: str | None = Query(
        default=None, description="Filter by vehicle type code.", examples=["TRACTOR"]
    ),
    lat: float | None = Query(
        default=None, ge=-90, le=90, description="User latitude for geo-search."
    ),
    lng: float | None = Query(
        default=None, ge=-180, le=180, description="User longitude for geo-search."
    ),
    radius_km: float | None = Query(default=None, gt=0, le=500, description="Search radius in km."),
    q: str | None = Query(
        default=None, max_length=100, description="Text search on name, brand, location."
    ),
    max_price: float | None = Query(default=None, gt=0, description="Max price per unit (paise)."),
    available_only: bool = Query(default=True),
    sort: str = Query(default="newest", pattern="^(newest|distance|price_asc|price_desc)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> VehicleCardPage:
    """Browse available vehicles. Supports geo-radius search, text search, and sorting."""
    params = VehicleSearchParams(
        type_code=type_code.strip().upper() if type_code else None,
        lat=lat,
        lng=lng,
        radius_km=radius_km,
        q=q.strip() if q else None,
        max_price=int(max_price) if max_price is not None else None,
        available_only=available_only,
        sort=sort,
        limit=limit,
        offset=offset,
    )
    return await service.list_available_vehicles(params=params)


@router.get(
    "/vehicles/{vehicle_id}",
    response_model=VehicleCardOut,
    tags=["vehicles"],
    summary="One vehicle's details",
)
async def get_vehicle(
    vehicle_id: uuid.UUID,
    service: VehicleService = Depends(get_vehicle_service),
) -> VehicleCardOut:
    return await service.get_public_vehicle(vehicle_id=vehicle_id)
