"""Vehicle listing endpoints.

Two groups, and the difference between them is who may call:

  POST   /provider/vehicles                     provider only, own listings
  GET    /provider/vehicles                     provider only, own listings
  GET    /provider/vehicles/{id}                provider only, own listings
  PATCH  /provider/vehicles/{id}                provider only, own listings
  PATCH  /provider/vehicles/{id}/availability   provider only, own listings
  DELETE /provider/vehicles/{id}                provider only, own listings

  GET    /vehicles                              public — no token needed
  GET    /vehicles/{id}                         public — no token needed
  GET    /vehicle-types                         public — the app's picker

The public feed is deliberately unauthenticated (Q11: browsing is anonymous,
login is required only to call a provider).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.auth.dependencies import require_role
from app.modules.users.models import User, UserRole
from app.modules.vehicles.repository import VehicleRepository
from app.modules.vehicles.schemas import (
    VehicleCardOut,
    VehicleCardPage,
    VehicleCreateIn,
    VehicleOut,
    VehiclePage,
    VehicleTypeOut,
    VehicleUpdateIn,
)
from app.modules.vehicles.service import VehicleService

router = APIRouter()

#: The provider guard. `require_role` returns a dependency that rejects a caller
#: without the PROVIDER role AND hands back the user row, so a handler never
#: repeats an authorisation check.
provider_only = require_role(UserRole.PROVIDER)


def get_vehicle_service(db: AsyncSession = Depends(get_db)) -> VehicleService:
    return VehicleService(repo=VehicleRepository(db))


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
    """The seeded taxonomy, for the app's type picker.

    Send the `code` (not the name) when creating a vehicle. Names are
    translatable and may change; codes will not.
    """
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
    """Create a listing owned by the calling provider.

    Every field is required except `latitude`/`longitude`, which are optional
    only because radius search is not built yet — send them if the app has GPS
    and search will work over your existing listings later.

    Notes on two fields that catch people out:

    - **`price_amount` is in paise**, not rupees. ₹500 per hour is `50000`.
    - **`vehicle_type_code`** must be a `code` from `GET /vehicle-types`.

    The registration number is normalised (`TN 38 AB 1234` → `TN38AB1234`) and
    must not already belong to a live listing.
    """
    return await service.create_vehicle(provider=provider, payload=payload)


@router.get(
    "/provider/vehicles",
    response_model=VehiclePage,
    tags=["vehicles"],
    summary="My vehicles",
    responses={
        401: {"description": "Missing or invalid token"},
        403: {"description": "Caller does not hold the PROVIDER role"},
    },
)
async def list_my_vehicles(
    provider: User = Depends(provider_only),
    service: VehicleService = Depends(get_vehicle_service),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> VehiclePage:
    """Every listing owned by the calling provider, newest first.

    Includes listings the provider has marked unavailable — this is their
    management screen, not the public feed. Soft-deleted listings are excluded.
    """
    return await service.list_my_vehicles(provider=provider, limit=limit, offset=offset)


@router.get(
    "/provider/vehicles/{vehicle_id}",
    response_model=VehicleOut,
    tags=["vehicles"],
    summary="One of my vehicles",
    responses={
        401: {"description": "Missing or invalid token"},
        403: {"description": "Caller does not hold the PROVIDER role"},
        404: {"description": "Not found, or not owned by the caller"},
    },
)
async def get_my_vehicle(
    vehicle_id: uuid.UUID,
    provider: User = Depends(provider_only),
    service: VehicleService = Depends(get_vehicle_service),
) -> VehicleOut:
    """One of your own listings — what an edit screen loads to prefill its form.

    Returns the **owner's** view, so unlike `GET /vehicles/{id}` it includes the
    registration number and the moderation status, and it works regardless of
    whether the listing is currently available or approved.

    404 (not 403) for someone else's vehicle: a 403 would confirm the id is real,
    which is how one provider enumerates another's inventory.
    """
    return await service.get_my_vehicle(provider=provider, vehicle_id=vehicle_id)


@router.patch(
    "/provider/vehicles/{vehicle_id}",
    response_model=VehicleOut,
    tags=["vehicles"],
    summary="Edit a vehicle",
    responses={
        400: {"description": "Unknown vehicle type"},
        403: {"description": "Caller does not hold the PROVIDER role"},
        404: {"description": "Not found, or not owned by the caller"},
        422: {"description": "A field is invalid"},
    },
)
async def update_vehicle(
    vehicle_id: uuid.UUID,
    payload: VehicleUpdateIn,
    provider: User = Depends(provider_only),
    service: VehicleService = Depends(get_vehicle_service),
) -> VehicleOut:
    """Change one of your own listings. **Partial** — send only what changed.

    ```json
    { "price_amount": 60000 }
    ```

    Two things worth knowing:

    - **`image_urls` replaces the whole set.** Send all the photos you want to
      keep, in the order you want them; the first is the card thumbnail. Omit the
      field to leave the photos untouched.
    - **`latitude: null` clears the coordinate**, while omitting `latitude`
      leaves it as it was. Those are different requests.

    `registration_number` cannot be changed — that would point the listing at a
    different physical vehicle. Delete it and create a new listing instead.
    """
    return await service.update_vehicle(provider=provider, vehicle_id=vehicle_id, payload=payload)


@router.patch(
    "/provider/vehicles/{vehicle_id}/availability",
    response_model=VehicleOut,
    tags=["vehicles"],
    summary="Mark a vehicle available or unavailable",
    responses={404: {"description": "Not found, or not owned by the caller"}},
)
async def set_availability(
    vehicle_id: uuid.UUID,
    is_available: bool = Query(description="true puts it back on the public feed."),
    provider: User = Depends(provider_only),
    service: VehicleService = Depends(get_vehicle_service),
) -> VehicleOut:
    """Toggle a listing on or off the public feed.

    This is the switch for "rented out this week" or "in for repair" — it keeps
    the listing and its photos, unlike delete.
    """
    return await service.set_availability(
        provider=provider, vehicle_id=vehicle_id, is_available=is_available
    )


@router.delete(
    "/provider/vehicles/{vehicle_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["vehicles"],
    summary="Delete a vehicle",
    responses={404: {"description": "Not found, or not owned by the caller"}},
)
async def delete_vehicle(
    vehicle_id: uuid.UUID,
    provider: User = Depends(provider_only),
    service: VehicleService = Depends(get_vehicle_service),
) -> Response:
    """Remove a listing.

    A soft delete: the row stays so history survives, but it leaves the feed and
    releases the registration number for re-listing.
    """
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
        default=None,
        description="Filter by vehicle type `code`, e.g. TRACTOR.",
        examples=["TRACTOR"],
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> VehicleCardPage:
    """Every available listing from every provider. **No login required.**

    A listing appears here only when all four are true: not deleted, the owner
    marked it available, moderation approved it, and the owner's account is
    active.

    The response contains **no provider phone number** and **no registration
    number** — see `VehicleCardOut`. Contacting a provider goes through the
    masked-calling endpoint in Phase 7.

    Ordered newest first. Distance ordering arrives with radius search.
    """
    return await service.list_available_vehicles(
        limit=limit, offset=offset, type_code=type_code.strip().upper() if type_code else None
    )


@router.get(
    "/vehicles/{vehicle_id}",
    response_model=VehicleCardOut,
    tags=["vehicles"],
    summary="One vehicle's details",
    responses={404: {"description": "No such vehicle, or it is not discoverable"}},
)
async def get_vehicle(
    vehicle_id: uuid.UUID,
    service: VehicleService = Depends(get_vehicle_service),
) -> VehicleCardOut:
    """The listing behind a card on the feed. **No login required.**

    Same visibility rules as `GET /vehicles`: a listing that is deleted,
    unavailable, unapproved, or owned by a suspended provider returns **404**
    here too. Hidden from the feed means hidden by id as well.

    Carries **no provider phone number** and **no registration number**. To
    contact the owner, use `provider_id` with the masked-calling endpoint
    (Phase 7).
    """
    return await service.get_public_vehicle(vehicle_id=vehicle_id)
