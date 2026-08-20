"""Upload authorisation business logic."""

from __future__ import annotations

import time

from app.core.config import Settings
from app.core.exceptions import ServiceUnavailableError
from app.integrations.cloudinary import (
    CloudinaryNotConfiguredError,
    sign_upload,
)
from app.modules.uploads.schemas import UploadSignatureOut


class UploadService:
    def __init__(self, *, settings: Settings) -> None:
        self.settings = settings

    def authorise_upload(self) -> UploadSignatureOut:
        """Issue one upload signature for the calling provider.

        The client names nothing: folder and public_id are chosen here, signed
        with the API secret, and returned as an unmodifiable package.
        """
        try:
            signed = sign_upload(self.settings, timestamp=int(time.time()))
        except CloudinaryNotConfiguredError as exc:
            raise ServiceUnavailableError(
                "Image uploads are not available right now.",
                code="CLOUDINARY_NOT_CONFIGURED",
            ) from exc
        return UploadSignatureOut(
            cloud_name=signed.cloud_name,
            api_key=signed.api_key,
            timestamp=signed.timestamp,
            signature=signed.signature,
            folder=signed.folder,
            public_id=signed.public_id,
            upload_preset=signed.upload_preset,
            expires_in=signed.expires_in,
        )
