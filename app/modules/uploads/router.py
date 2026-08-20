"""Upload authorisation endpoints.

  POST /provider/uploads/signature   provider only

The app uploads photo bytes directly to Cloudinary; this endpoint is the
gate that decides whether that upload may happen at all. It takes **no
input** — a client that could name a public_id could aim its upload at
another part of the account (provider documents, for instance), so both
the folder and the id are chosen and signed server-side.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.config import Settings, get_settings
from app.modules.auth.dependencies import require_role
from app.modules.uploads.schemas import UploadSignatureOut
from app.modules.uploads.service import UploadService
from app.modules.users.models import User, UserRole

router = APIRouter()

#: The provider guard — the same one the vehicle endpoints use. Only a
#: verified provider may upload photos for their listings.
provider_only = require_role(UserRole.PROVIDER)


def get_upload_service(
    settings: Settings = Depends(get_settings),
) -> UploadService:
    return UploadService(settings=settings)


@router.post(
    "/provider/uploads/signature",
    response_model=UploadSignatureOut,
    status_code=status.HTTP_200_OK,
    tags=["uploads"],
    summary="Authorise one direct upload to Cloudinary",
    responses={
        401: {"description": "Missing or invalid token"},
        403: {"description": "Caller does not hold the PROVIDER role"},
        503: {"description": "Cloudinary is not configured"},
    },
)
async def get_upload_signature(
    provider: User = Depends(provider_only),
    service: UploadService = Depends(get_upload_service),
) -> UploadSignatureOut:
    """Get everything the app needs for one direct photo upload.

    Call this just before uploading, then send the file straight to
    Cloudinary with these fields. The signature is valid for `expires_in`
    seconds and covers exactly one `public_id`, which is chosen here — the
    request body is ignored.

    The response never contains the API secret. `upload_preset` is null
    unless the server has one configured.
    """
    return service.authorise_upload()
