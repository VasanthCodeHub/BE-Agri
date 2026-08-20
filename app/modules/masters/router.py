"""Vehicle master data endpoints.

The app's "add a vehicle" form is built from these responses — the frontend
never hard-codes a manufacturer or model. The tree is:

    GET /vehicle-masters              → manufacturers → models → variants
    GET /vehicle-masters?type_code=…  → the same tree, narrowed to one type
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.masters.repository import MasterRepository
from app.modules.masters.schemas import VehicleMastersOut
from app.modules.masters.service import MasterService

router = APIRouter()


def get_master_service(db: AsyncSession = Depends(get_db)) -> MasterService:
    return MasterService(repo=MasterRepository(db))


@router.get(
    "/vehicle-masters",
    response_model=VehicleMastersOut,
    tags=["masters"],
    summary="Vehicle master data for the add-vehicle form",
)
async def list_masters(
    service: MasterService = Depends(get_master_service),
    type_code: str | None = Query(
        default=None,
        description="Narrow the tree to models of one vehicle type.",
        examples=["TRACTOR"],
    ),
) -> VehicleMastersOut:
    """Manufacturers → models → variants, for the dropdowns.

    Public: the provider needs it before logging in to fill the form, and the
    data is not sensitive.
    """
    return await service.list_masters(
        vehicle_type_code=type_code.strip().upper() if type_code else None
    )
