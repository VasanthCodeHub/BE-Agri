"""Tests for vehicle listings.

The ones that matter most here are the authorisation and leakage tests: a
provider must not touch another provider's listing, and the public feed must
never carry a phone number or a registration number (ADR-009).
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

AUTH = "/api/v1/auth"
API = "/api/v1"


def _payload(**overrides: Any) -> dict[str, Any]:
    """A complete, valid create-vehicle body."""
    body: dict[str, Any] = {
        "name": "Mahindra 575 DI",
        "vehicle_type_code": "TRACTOR",
        "brand": "Mahindra",
        "model": "575 DI",
        "manufacture_year": 2019,
        "registration_number": "TN38AB1234",
        "note": "Well maintained. Rotavator included.",
        "price_amount": 50000,  # paise -> Rs 500
        "price_unit": "HOUR",
        "location_text": "Sulur, Coimbatore",
        "fuel_type": "DIESEL",
        "power_hp": 47,
        "transmission": "MANUAL",
        "image_urls": ["https://cdn.example.com/a.jpg"],
    }
    body.update(overrides)
    return body


async def _token(client: AsyncClient, phone: str, role: str = "PROVIDER") -> str:
    """Register/log in and return an access token for the given role."""
    await client.post(f"{AUTH}/otp/request", json={"phone": phone, "role": role})
    response = await client.post(
        f"{AUTH}/otp/verify", json={"phone": phone, "code": "0000", "name": f"User {phone[-4:]}"}
    )
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create(client: AsyncClient, token: str, **overrides: Any) -> dict[str, Any]:
    response = await client.post(
        f"{API}/provider/vehicles", json=_payload(**overrides), headers=_auth(token)
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


# ---------------------------------------------------------------------------
# The taxonomy
# ---------------------------------------------------------------------------
async def test_vehicle_types_are_seeded(client: AsyncClient) -> None:
    """The migration seeds the taxonomy — without it no listing can be created."""
    response = await client.get(f"{API}/vehicle-types")

    assert response.status_code == 200
    codes = [t["code"] for t in response.json()]
    assert "TRACTOR" in codes
    assert len(codes) >= 10


# ---------------------------------------------------------------------------
# Creating a listing
# ---------------------------------------------------------------------------
async def test_provider_can_add_a_vehicle(client: AsyncClient) -> None:
    token = await _token(client, "9810000001")

    body = await _create(client, token)

    assert body["name"] == "Mahindra 575 DI"
    assert body["vehicle_type"]["code"] == "TRACTOR"
    assert body["registration_number"] == "TN38AB1234"
    assert body["price_amount"] == 50000
    assert body["price_label"] == "₹500 / hour"  # paise rendered for display
    assert body["is_available"] is True
    assert body["image_urls"] == ["https://cdn.example.com/a.jpg"]


async def test_adding_a_vehicle_requires_a_token(client: AsyncClient) -> None:
    response = await client.post(f"{API}/provider/vehicles", json=_payload())

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_MISSING"


async def test_a_renter_cannot_add_a_vehicle(client: AsyncClient) -> None:
    """Holding only the RENTER role must not reach a provider endpoint."""
    token = await _token(client, "9810000002", role="RENTER")

    response = await client.post(f"{API}/provider/vehicles", json=_payload(), headers=_auth(token))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ROLE_REQUIRED"


async def test_registration_number_is_normalised(client: AsyncClient) -> None:
    """Same vehicle typed four ways must be one registration number."""
    token = await _token(client, "9810000003")

    body = await _create(client, token, registration_number="tn-38 ab 1234")

    assert body["registration_number"] == "TN38AB1234"


async def test_the_same_registration_cannot_be_listed_twice(client: AsyncClient) -> None:
    """One physical vehicle, one live listing — even across providers."""
    first = await _token(client, "9810000004")
    second = await _token(client, "9810000005")
    await _create(client, first)

    response = await client.post(
        f"{API}/provider/vehicles",
        json=_payload(registration_number="TN 38 AB 1234"),
        headers=_auth(second),
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "REGISTRATION_ALREADY_LISTED"


async def test_the_bh_series_is_accepted(client: AsyncClient) -> None:
    """Vehicles that move between states carry BH plates; rejecting them would
    turn away exactly the owners who work across district borders."""
    token = await _token(client, "9810000006")

    body = await _create(client, token, registration_number="22 BH 1234 AA")

    assert body["registration_number"] == "22BH1234AA"


async def test_a_nonsense_registration_is_rejected(client: AsyncClient) -> None:
    token = await _token(client, "9810000007")

    response = await client.post(
        f"{API}/provider/vehicles",
        json=_payload(registration_number="HELLO"),
        headers=_auth(token),
    )

    assert response.status_code == 422
    fields = [f["field"] for f in response.json()["error"]["details"]["fields"]]
    assert "registration_number" in fields


async def test_an_unknown_vehicle_type_is_rejected(client: AsyncClient) -> None:
    token = await _token(client, "9810000008")

    response = await client.post(
        f"{API}/provider/vehicles",
        json=_payload(vehicle_type_code="SPACESHIP"),
        headers=_auth(token),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VEHICLE_TYPE_UNKNOWN"


async def test_every_field_is_mandatory(client: AsyncClient) -> None:
    """Dropping any required field must be a 422 naming that field."""
    token = await _token(client, "9810000009")

    for field in (
        "name",
        "vehicle_type_code",
        "brand",
        "model",
        "manufacture_year",
        "registration_number",
        "note",
        "price_amount",
        "price_unit",
        "location_text",
        "fuel_type",
        "power_hp",
        "transmission",
        "image_urls",
    ):
        body = _payload()
        del body[field]
        response = await client.post(f"{API}/provider/vehicles", json=body, headers=_auth(token))

        assert response.status_code == 422, f"{field} was accepted as missing"
        named = [f["field"] for f in response.json()["error"]["details"]["fields"]]
        assert field in named, f"422 did not name {field}: {named}"


async def test_implausible_values_are_rejected(client: AsyncClient) -> None:
    token = await _token(client, "9810000010")

    for overrides in (
        {"manufacture_year": 1900},
        {"manufacture_year": 2200},
        {"price_amount": 0},
        {"price_amount": -100},
        {"power_hp": 0},
        {"power_hp": 99999},
        {"latitude": 200},
        {"longitude": -500},
    ):
        response = await client.post(
            f"{API}/provider/vehicles", json=_payload(**overrides), headers=_auth(token)
        )
        assert response.status_code == 422, f"{overrides} was accepted"


# ---------------------------------------------------------------------------
# Image URL safety
# ---------------------------------------------------------------------------
async def test_only_https_image_urls_are_accepted(client: AsyncClient) -> None:
    """A stored javascript: or data: URL becomes an attack in any webview."""
    token = await _token(client, "9810000011")

    for url in (
        "http://cdn.example.com/a.jpg",
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "not-a-url",
        "",
    ):
        response = await client.post(
            f"{API}/provider/vehicles", json=_payload(image_urls=[url]), headers=_auth(token)
        )
        assert response.status_code == 422, f"{url!r} was accepted"


async def test_image_count_is_bounded(client: AsyncClient) -> None:
    token = await _token(client, "9810000012")

    no_images = await client.post(
        f"{API}/provider/vehicles", json=_payload(image_urls=[]), headers=_auth(token)
    )
    assert no_images.status_code == 422

    too_many = await client.post(
        f"{API}/provider/vehicles",
        json=_payload(image_urls=[f"https://cdn.example.com/{i}.jpg" for i in range(7)]),
        headers=_auth(token),
    )
    assert too_many.status_code == 422


async def test_duplicate_image_urls_are_rejected(client: AsyncClient) -> None:
    token = await _token(client, "9810000013")

    response = await client.post(
        f"{API}/provider/vehicles",
        json=_payload(image_urls=["https://cdn.example.com/a.jpg"] * 2),
        headers=_auth(token),
    )

    assert response.status_code == 422


async def test_images_keep_their_order(client: AsyncClient) -> None:
    """The first image is the card thumbnail, so order is meaningful."""
    token = await _token(client, "9810000014")
    urls = [f"https://cdn.example.com/{n}.jpg" for n in ("c", "a", "b")]

    body = await _create(client, token, image_urls=urls)

    assert body["image_urls"] == urls


# ---------------------------------------------------------------------------
# My listings
# ---------------------------------------------------------------------------
async def test_provider_sees_only_their_own_vehicles(client: AsyncClient) -> None:
    mine = await _token(client, "9810000015")
    theirs = await _token(client, "9810000016")
    await _create(client, mine, registration_number="TN38AA1111")
    await _create(client, theirs, registration_number="TN38BB2222")

    response = await client.get(f"{API}/provider/vehicles", headers=_auth(mine))

    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 1
    assert page["items"][0]["registration_number"] == "TN38AA1111"


async def test_my_listings_include_unavailable_ones(client: AsyncClient) -> None:
    """This is the management screen, not the public feed."""
    token = await _token(client, "9810000017")
    vehicle = await _create(client, token)

    await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}/availability",
        params={"is_available": False},
        headers=_auth(token),
    )

    page = (await client.get(f"{API}/provider/vehicles", headers=_auth(token))).json()
    assert page["total"] == 1
    assert page["items"][0]["is_available"] is False


async def test_a_provider_cannot_touch_another_providers_vehicle(client: AsyncClient) -> None:
    """404, not 403 — telling the caller it exists confirms the id is real."""
    owner = await _token(client, "9810000018")
    stranger = await _token(client, "9810000019")
    vehicle = await _create(client, owner)

    patched = await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}/availability",
        params={"is_available": False},
        headers=_auth(stranger),
    )
    deleted = await client.delete(
        f"{API}/provider/vehicles/{vehicle['id']}", headers=_auth(stranger)
    )

    assert patched.status_code == 404
    assert deleted.status_code == 404
    assert patched.json()["error"]["code"] == "VEHICLE_NOT_FOUND"


# ---------------------------------------------------------------------------
# The public feed
# ---------------------------------------------------------------------------
async def test_the_public_feed_needs_no_token(client: AsyncClient) -> None:
    """Browsing is anonymous; login is required only to call a provider (Q11)."""
    token = await _token(client, "9810000020")
    await _create(client, token)

    response = await client.get(f"{API}/vehicles")

    assert response.status_code == 200
    assert response.json()["total"] >= 1


async def test_the_public_feed_never_exposes_a_phone_or_registration_number(
    client: AsyncClient,
) -> None:
    """The core privacy guarantee (ADR-009).

    A provider's phone number is what the masked-calling feature exists to
    protect, and an RC number can be used to look up the registered owner.
    """
    phone = "9810000021"
    token = await _token(client, phone)
    await _create(client, token, registration_number="TN38ZZ9999")

    response = await client.get(f"{API}/vehicles")

    assert response.status_code == 200
    assert phone not in response.text
    assert "+91" + phone not in response.text
    assert "TN38ZZ9999" not in response.text
    card = next(c for c in response.json()["items"] if c["name"] == "Mahindra 575 DI")
    assert "registration_number" not in card
    assert "latitude" not in card
    assert "listing_status" not in card
    assert card["provider_name"] == "User 0021"


async def test_unavailable_vehicles_are_not_on_the_public_feed(client: AsyncClient) -> None:
    token = await _token(client, "9810000022")
    vehicle = await _create(client, token, registration_number="TN38YY8888")

    await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}/availability",
        params={"is_available": False},
        headers=_auth(token),
    )

    feed = (await client.get(f"{API}/vehicles")).json()
    assert all(item["id"] != vehicle["id"] for item in feed["items"])


async def test_the_feed_can_be_filtered_by_type(client: AsyncClient) -> None:
    token = await _token(client, "9810000023")
    await _create(client, token, registration_number="TN38XX7777", vehicle_type_code="TRACTOR")
    await _create(
        client,
        token,
        registration_number="TN38WW6666",
        vehicle_type_code="SPRAYER",
        name="Aspee Sprayer",
    )

    sprayers = (await client.get(f"{API}/vehicles", params={"type_code": "SPRAYER"})).json()

    assert sprayers["total"] >= 1
    assert all(item["vehicle_type"]["code"] == "SPRAYER" for item in sprayers["items"])


async def test_the_feed_is_paginated(client: AsyncClient) -> None:
    token = await _token(client, "9810000024")
    for n in range(3):
        await _create(client, token, registration_number=f"TN38PP{n}00{n}")

    page = (await client.get(f"{API}/vehicles", params={"limit": 2, "offset": 0})).json()

    assert len(page["items"]) == 2
    assert page["total"] >= 3
    assert page["limit"] == 2


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
async def test_deleting_removes_the_listing_and_frees_the_registration(
    client: AsyncClient,
) -> None:
    """A soft delete keeps history but must not block re-listing the vehicle."""
    token = await _token(client, "9810000025")
    vehicle = await _create(client, token, registration_number="TN38QQ5555")

    deleted = await client.delete(f"{API}/provider/vehicles/{vehicle['id']}", headers=_auth(token))
    assert deleted.status_code == 204

    mine = (await client.get(f"{API}/provider/vehicles", headers=_auth(token))).json()
    assert mine["total"] == 0

    feed = (await client.get(f"{API}/vehicles")).json()
    assert all(item["id"] != vehicle["id"] for item in feed["items"])

    # The same vehicle can be listed again.
    again = await client.post(
        f"{API}/provider/vehicles",
        json=_payload(registration_number="TN38QQ5555"),
        headers=_auth(token),
    )
    assert again.status_code == 201, again.text


# ---------------------------------------------------------------------------
# Money formatting
# ---------------------------------------------------------------------------
async def test_price_is_stored_in_paise_and_displayed_in_rupees(client: AsyncClient) -> None:
    """Money is integer minor units end to end — floats never touch it."""
    token = await _token(client, "9810000026")

    per_acre = await _create(
        client, token, price_amount=125050, price_unit="ACRE", registration_number="TN38RR4444"
    )

    assert per_acre["price_amount"] == 125050
    assert per_acre["price_label"] == "₹1,250.50 / acre"
