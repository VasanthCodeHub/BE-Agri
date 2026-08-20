"""Response shapes for upload authorisation."""

from __future__ import annotations

from pydantic import BaseModel


class UploadSignatureOut(BaseModel):
    """Everything the Flutter SDK needs for one direct upload.

    Never contains the API secret: the signature is proof that the server
    authorised this exact `public_id`, which is why the client cannot tamper
    with it.
    """

    cloud_name: str
    api_key: str
    timestamp: int
    signature: str
    folder: str
    public_id: str
    upload_preset: str | None
    expires_in: int
