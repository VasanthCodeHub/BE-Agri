"""Cloudinary — signing uploads and building delivery URLs.

THE IMAGE BYTES NEVER TOUCH THIS BACKEND
----------------------------------------
    app  ──"may I upload?"──▶  our API        (tiny JSON)
    app  ◀──── signature ────  our API        (tiny JSON)
    app  ──── the photo ────▶  Cloudinary     (big, and direct)

We authorise; we do not carry. Proxying the file would double the upload for a
provider on rural 4G and tie up a worker holding the bytes.

WHY SIGNING RATHER THAN AN UNSIGNED PRESET
------------------------------------------
An unsigned preset lets *anyone* upload to the account: the cloud name and preset
name both ship inside the APK, and an APK is trivial to unpack. There is no login
step, so a stranger can exhaust the quota or host their own files on the account,
and nothing records who did it.

A signature closes that. The client must first present a valid access token to
this API, and the proof it gets back cannot be forged without the API secret,
which never leaves the server.

HOW THE SIGNATURE WORKS
-----------------------
Cloudinary's scheme: take the parameters you want to commit to, sort them by
name, join them as `k=v&k=v`, append the API secret, and hash the result. Both
sides compute it and compare.

Because the hash is one-way, a signature captured off the wire cannot be turned
back into the secret. And because `timestamp` is one of the signed parameters and
Cloudinary rejects stale ones, a captured signature stops working shortly after.

WE CHOOSE THE public_id, NOT THE CLIENT
---------------------------------------
The `public_id` is the asset's path inside the account. We generate it and sign
it, so a client cannot pick its own — otherwise it could aim an upload at
`agri/documents/...` and overwrite a provider's verification papers. A signed
parameter cannot be changed without invalidating the signature.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass

from app.core.config import Settings

_DELIVERY_HOST = "res.cloudinary.com"

#: Cloudinary's own validity window for a signed upload is one hour. Reported to
#: the client so it knows to ask again rather than cache the signature.
SIGNATURE_TTL_SECONDS = 3600

#: Width used for list-screen thumbnails. The feed sending full-size photos is
#: the difference between a usable and an unusable app on a rural connection.
THUMB_WIDTH = 400

#: Conservative subset of what Cloudinary allows in a public_id. Anything with a
#: colon, a space or a traversal sequence is rejected rather than escaped.
_PUBLIC_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_-]{0,199}$")


class CloudinaryNotConfiguredError(RuntimeError):
    """Raised when a signature is requested with no credentials set."""


@dataclass(frozen=True)
class UploadSignature:
    """Everything the Flutter SDK needs for one direct upload."""

    cloud_name: str
    api_key: str
    timestamp: int
    signature: str
    folder: str
    public_id: str
    upload_preset: str | None
    expires_in: int


def sign_upload(settings: Settings, *, timestamp: int) -> UploadSignature:
    """Authorise exactly one upload.

    `timestamp` is passed in rather than read from the clock so the caller stays
    testable and the value is identical in the signature and the response.
    """
    if not settings.cloudinary_configured:
        raise CloudinaryNotConfiguredError(
            "Cloudinary is not configured. Set CLOUDINARY_CLOUD_NAME, "
            "CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET."
        )

    folder = settings.cloudinary_folder
    # A random leaf, so two providers uploading at the same moment cannot collide
    # and nobody can guess or overwrite another asset's path.
    public_id = f"{folder}/{uuid.uuid4().hex}"

    params: dict[str, str] = {"public_id": public_id, "timestamp": str(timestamp)}
    if settings.cloudinary_upload_preset:
        params["upload_preset"] = settings.cloudinary_upload_preset

    return UploadSignature(
        cloud_name=settings.cloudinary_cloud_name,
        api_key=settings.cloudinary_api_key,
        timestamp=timestamp,
        signature=_signature(params, settings.cloudinary_api_secret.get_secret_value()),
        folder=folder,
        public_id=public_id,
        upload_preset=settings.cloudinary_upload_preset or None,
        expires_in=SIGNATURE_TTL_SECONDS,
    )


def _signature(params: dict[str, str], api_secret: str) -> str:
    """Cloudinary's signature: sha1(sorted "k=v&k=v" + api_secret).

    SHA-1 is Cloudinary's protocol default, not our choice, and it is not being
    used to protect a stored secret here — it commits to a set of parameters that
    the other side re-derives with its own copy of the shared secret. Switching to
    SHA-256 requires the account to be set to that algorithm.
    """
    payload = "&".join(f"{key}={params[key]}" for key in sorted(params))
    return hashlib.sha1(f"{payload}{api_secret}".encode()).hexdigest()  # noqa: S324


# ---------------------------------------------------------------------------
# Verifying what the client sends back
# ---------------------------------------------------------------------------
def is_well_formed_public_id(public_id: str) -> bool:
    """Shape check: a path, not a URL, and no traversal."""
    if ".." in public_id or public_id.startswith("/"):
        return False
    return bool(_PUBLIC_ID_PATTERN.match(public_id))


def is_in_our_folder(public_id: str, settings: Settings) -> bool:
    """Is this asset inside the folder we sign uploads into?

    Stops a caller attaching someone else's asset — or a provider's verification
    document — to a vehicle listing as if it were a photo of the tractor.
    """
    prefix = f"{settings.cloudinary_folder}/"
    return public_id.startswith(prefix) and len(public_id) > len(prefix)


# ---------------------------------------------------------------------------
# Delivery URLs
# ---------------------------------------------------------------------------
def build_url(public_id: str, settings: Settings, *, width: int | None = None) -> str | None:
    """A delivery URL for an asset, optionally resized.

    Built here rather than stored, which is why the database keeps the
    `public_id`: the same asset can be served at any size, and moving off
    Cloudinary later changes this function instead of every stored row.

    Returns None when no cloud name is configured — there is no honest URL to
    give, and a broken one would be worse than an explicit null.
    """
    if not settings.cloudinary_cloud_name:
        return None
    transformation = f"w_{width},c_fill,q_auto,f_auto/" if width else "q_auto,f_auto/"
    return (
        f"https://{_DELIVERY_HOST}/{settings.cloudinary_cloud_name}"
        f"/image/upload/{transformation}{public_id}"
    )
