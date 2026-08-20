"""Contact endpoints.

  POST /contact/call   any authenticated user

The product has no bookings: the user calls the provider and the two sides
negotiate directly. This endpoint records the contact and returns the
provider's number for the dialer.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.contact.repository import ContactCallRepository
from app.modules.contact.schemas import ContactCallIn, ContactCallOut
from app.modules.contact.service import ContactService
from app.modules.notifications.repository import NotificationRepository
from app.modules.notifications.service import NotificationService
from app.modules.users.models import User
from app.modules.vehicles.repository import VehicleRepository

router = APIRouter()


def get_contact_service(db: AsyncSession = Depends(get_db)) -> ContactService:
    return ContactService(
        repo=ContactCallRepository(db),
        vehicles=VehicleRepository(db),
        notifier=NotificationService(repo=NotificationRepository(db)),
    )


@router.post(
    "/contact/call",
    response_model=ContactCallOut,
    status_code=status.HTTP_201_CREATED,
    tags=["contact"],
    summary="Initiate direct contact with a vehicle's provider",
    responses={
        401: {"description": "Missing or invalid token"},
        404: {"description": "Vehicle not found or not available"},
        503: {"description": "The provider has no contact number on file"},
    },
)
async def initiate_call(
    payload: ContactCallIn,
    user: User = Depends(get_current_user),
    service: ContactService = Depends(get_contact_service),
) -> ContactCallOut:
    """Get the provider's number to dial.

    The call is recorded and the provider gets a notification. The provider's
    phone number appears ONLY here — never on the public feed — and only after
    the caller is authenticated and the contact is recorded.
    """
    return await service.initiate_call(caller=user, vehicle_id=payload.vehicle_id)
