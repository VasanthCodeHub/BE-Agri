"""Tests for upload authorisation.

Nothing is uploaded here — no network, no Cloudinary account. Signing is a pure
function of the config, so what is worth testing is the contract: only providers
get a signature, the signature matches Cloudinary's documented algorithm, the
secret never appears in a response, and the client cannot choose where its file
lands.
"""

from __future__ import annotations

import hashlib

import pytest
from httpx import AsyncClient
from pydantic import ValidationError

from app.core.config import Settings
from app.integrations.cloudinary import (
    CloudinaryNotConfiguredError,
    build_url,
    is_in_our_folder,
    is_well_formed_public_id,
    sign_upload,
)

AUTH = "/api/v1/auth"
SIGN = "/api/v1/provider/uploads/signature"

SECRET = "test-cloudinary-secret"  # matches the conftest settings fixture


async def _token(client: AsyncClient, phone: str, role: str = "PROVIDER") -> str:
    await client.post(f"{AUTH}/otp/request", json={"phone": phone, "role": role})
    response = await client.post(f"{AUTH}/otp/verify", json={"phone": phone, "code": "0000"})
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


# ---------------------------------------------------------------------------
# Who may ask
# ---------------------------------------------------------------------------
async def test_a_provider_gets_a_signature(client: AsyncClient) -> None:
    token = await _token(client, "9820000001")

    response = await client.post(SIGN, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cloud_name"] == "test-cloud"
    assert body["api_key"] == "123456789012345"
    assert body["folder"] == "agri/vehicles"
    assert body["public_id"].startswith("agri/vehicles/")
    assert body["expires_in"] == 3600
    assert len(body["signature"]) == 40  # sha1 hex


async def test_the_signature_endpoint_needs_a_token(client: AsyncClient) -> None:
    """This is the gate that an unsigned upload preset does not have."""
    response = await client.post(SIGN)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_MISSING"


async def test_a_user_cannot_get_a_signature(client: AsyncClient) -> None:
    token = await _token(client, "9820000002", role="USER")

    response = await client.post(SIGN, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_REQUIRED"


# ---------------------------------------------------------------------------
# What comes back
# ---------------------------------------------------------------------------
async def test_the_api_secret_never_appears_in_the_response(client: AsyncClient) -> None:
    """The point of signing is that the secret stays on the server."""
    token = await _token(client, "9820000003")

    response = await client.post(SIGN, headers={"Authorization": f"Bearer {token}"})

    assert SECRET not in response.text


async def test_each_request_gets_a_different_public_id(client: AsyncClient) -> None:
    """One signature authorises one file.

    A shared or guessable path would let one upload overwrite another.
    """
    token = await _token(client, "9820000004")
    headers = {"Authorization": f"Bearer {token}"}

    first = await client.post(SIGN, headers=headers)
    second = await client.post(SIGN, headers=headers)

    assert first.json()["public_id"] != second.json()["public_id"]


async def test_the_client_cannot_choose_where_its_file_lands(client: AsyncClient) -> None:
    """The endpoint takes no input at all, so there is nothing to influence.

    If a client could name the public_id, it could aim an upload at another part
    of the account — provider documents, for instance.
    """
    token = await _token(client, "9820000005")

    response = await client.post(
        SIGN,
        json={"public_id": "agri/documents/overwrite-me", "folder": "agri/documents"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["public_id"].startswith("agri/vehicles/")
    assert response.json()["folder"] == "agri/vehicles"


# ---------------------------------------------------------------------------
# The signature itself
# ---------------------------------------------------------------------------
def test_the_signature_matches_cloudinarys_algorithm(settings: Settings) -> None:
    """sha1 of the sorted "k=v&k=v" parameters plus the API secret.

    Recomputed here the way Cloudinary's server does, so a change to how we build
    it cannot pass unnoticed — a wrong signature is rejected on upload, which is
    a confusing failure to debug from the app side.
    """
    signed = sign_upload(settings, timestamp=1786800000)

    expected_payload = f"public_id={signed.public_id}&timestamp=1786800000"
    expected = hashlib.sha1(f"{expected_payload}{SECRET}".encode()).hexdigest()  # noqa: S324

    assert signed.signature == expected


def test_the_upload_preset_is_signed_when_configured(settings: Settings) -> None:
    """A preset is where file-size, format and metadata-stripping rules live, so
    it must be part of what is signed rather than a client-supplied extra."""
    with_preset = settings.model_copy(update={"cloudinary_upload_preset": "agri_signed"})

    signed = sign_upload(with_preset, timestamp=1786800000)

    payload = f"public_id={signed.public_id}&timestamp=1786800000&upload_preset=agri_signed"
    expected = hashlib.sha1(f"{payload}{SECRET}".encode()).hexdigest()  # noqa: S324
    assert signed.signature == expected
    assert signed.upload_preset == "agri_signed"


def test_the_timestamp_changes_the_signature(settings: Settings) -> None:
    """Timestamps are signed, which is what makes a captured signature expire."""
    first = sign_upload(settings, timestamp=1786800000)
    second = sign_upload(settings, timestamp=1786800001)

    assert first.signature != second.signature


# ---------------------------------------------------------------------------
# Not configured
# ---------------------------------------------------------------------------
def test_signing_without_credentials_raises() -> None:
    bare = Settings(_env_file=None, jwt_secret_key="x" * 40)

    with pytest.raises(CloudinaryNotConfiguredError):
        sign_upload(bare, timestamp=1786800000)


def test_a_half_configured_cloudinary_refuses_to_start() -> None:
    """Two of three values is always a mistake, and the symptom would otherwise
    be "uploads silently do not work"."""
    with pytest.raises(ValidationError, match="CLOUDINARY_API_SECRET"):
        Settings(
            _env_file=None,
            jwt_secret_key="x" * 40,
            cloudinary_cloud_name="some-cloud",
            cloudinary_api_key="123",
        )


# ---------------------------------------------------------------------------
# public_id checks and URL building
# ---------------------------------------------------------------------------
def test_well_formed_public_ids() -> None:
    assert is_well_formed_public_id("agri/vehicles/9f8e7d6c")
    assert is_well_formed_public_id("agri/vehicles/a-b_c")

    assert not is_well_formed_public_id("")
    assert not is_well_formed_public_id("/agri/vehicles/x")  # leading slash
    assert not is_well_formed_public_id("agri/../documents/x")  # traversal
    assert not is_well_formed_public_id("agri/vehicles/has space")
    assert not is_well_formed_public_id("https://res.cloudinary.com/c/image/upload/x")


def test_folder_ownership(settings: Settings) -> None:
    assert is_in_our_folder("agri/vehicles/abc", settings)

    assert not is_in_our_folder("agri/documents/abc", settings)
    assert not is_in_our_folder("agri/vehicles", settings)  # the folder itself
    assert not is_in_our_folder("other-app/agri/vehicles/abc", settings)


def test_delivery_urls_are_derived_not_stored(settings: Settings) -> None:
    """One id, any size — this is the whole reason we store ids."""
    full = build_url("agri/vehicles/abc", settings)
    thumb = build_url("agri/vehicles/abc", settings, width=400)

    assert full == (
        "https://res.cloudinary.com/test-cloud/image/upload/q_auto,f_auto/agri/vehicles/abc"
    )
    assert thumb == (
        "https://res.cloudinary.com/test-cloud/image/upload/"
        "w_400,c_fill,q_auto,f_auto/agri/vehicles/abc"
    )


def test_no_url_is_invented_when_cloudinary_is_unconfigured() -> None:
    """None is honest; a half-built URL would 404 in the app."""
    bare = Settings(_env_file=None, jwt_secret_key="x" * 40)

    assert build_url("agri/vehicles/abc", bare) is None
