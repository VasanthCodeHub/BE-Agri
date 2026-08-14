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
        "image_public_ids": ["agri/vehicles/aaaa1111"],
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
    assert [image["public_id"] for image in body["images"]] == ["agri/vehicles/aaaa1111"]
    # Delivery URLs are derived from the id, never stored.
    assert body["images"][0]["url"] == (
        "https://res.cloudinary.com/test-cloud/image/upload/q_auto,f_auto/agri/vehicles/aaaa1111"
    )
    assert "w_400,c_fill" in body["images"][0]["thumb_url"]


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
        "image_public_ids",
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
async def test_urls_are_rejected_only_public_ids_are_accepted(client: AsyncClient) -> None:
    """We store Cloudinary ids, not links.

    A stored `javascript:` or `data:` URL becomes an attack the moment a client
    renders it, and an arbitrary https URL points at content nobody can verify.
    """
    token = await _token(client, "9810000011")

    for value in (
        "https://cdn.example.com/a.jpg",
        "http://cdn.example.com/a.jpg",
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "../../etc/passwd",
        "/agri/vehicles/leading-slash",
        "agri/vehicles/has spaces",
        "",
    ):
        response = await client.post(
            f"{API}/provider/vehicles",
            json=_payload(image_public_ids=[value]),
            headers=_auth(token),
        )
        assert response.status_code == 422, f"{value!r} was accepted"


async def test_images_outside_our_folder_are_rejected(client: AsyncClient) -> None:
    """A well-formed id from elsewhere in the Cloudinary account is still refused.

    Otherwise a caller could attach another app's asset — or a provider's
    verification document — to a public listing.
    """
    token = await _token(client, "9810000047")

    response = await client.post(
        f"{API}/provider/vehicles",
        json=_payload(image_public_ids=["agri/documents/someones-rc-book"]),
        headers=_auth(token),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IMAGE_NOT_RECOGNISED"


async def test_image_count_is_bounded(client: AsyncClient) -> None:
    token = await _token(client, "9810000012")

    no_images = await client.post(
        f"{API}/provider/vehicles", json=_payload(image_public_ids=[]), headers=_auth(token)
    )
    assert no_images.status_code == 422

    too_many = await client.post(
        f"{API}/provider/vehicles",
        json=_payload(image_public_ids=[f"agri/vehicles/img{i}" for i in range(7)]),
        headers=_auth(token),
    )
    assert too_many.status_code == 422


async def test_duplicate_image_urls_are_rejected(client: AsyncClient) -> None:
    token = await _token(client, "9810000013")

    response = await client.post(
        f"{API}/provider/vehicles",
        json=_payload(image_public_ids=["agri/vehicles/dup1"] * 2),
        headers=_auth(token),
    )

    assert response.status_code == 422


async def test_images_keep_their_order(client: AsyncClient) -> None:
    """The first image is the card thumbnail, so order is meaningful."""
    token = await _token(client, "9810000014")
    urls = [f"agri/vehicles/{n}0001" for n in ("c", "a", "b")]

    body = await _create(client, token, image_public_ids=urls)

    assert [image["public_id"] for image in body["images"]] == urls


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
# My listing, by id — what the edit screen loads
# ---------------------------------------------------------------------------
async def test_provider_can_fetch_one_of_their_own_vehicles(client: AsyncClient) -> None:
    token = await _token(client, "9810000050")
    vehicle = await _create(client, token, registration_number="TN38GG1111")

    response = await client.get(f"{API}/provider/vehicles/{vehicle['id']}", headers=_auth(token))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == vehicle["id"]
    # Owner's view: the fields the public card deliberately omits.
    assert body["registration_number"] == "TN38GG1111"
    assert body["listing_status"] == "APPROVED"


async def test_my_vehicle_by_id_works_when_unavailable(client: AsyncClient) -> None:
    """The public detail 404s once hidden; the owner must still be able to edit it."""
    token = await _token(client, "9810000051")
    vehicle = await _create(client, token, registration_number="TN38GG2222")

    await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}/availability",
        params={"is_available": False},
        headers=_auth(token),
    )

    public = await client.get(f"{API}/vehicles/{vehicle['id']}")
    mine = await client.get(f"{API}/provider/vehicles/{vehicle['id']}", headers=_auth(token))

    assert public.status_code == 404
    assert mine.status_code == 200
    assert mine.json()["is_available"] is False


async def test_provider_cannot_fetch_another_providers_vehicle_by_id(client: AsyncClient) -> None:
    owner = await _token(client, "9810000052")
    stranger = await _token(client, "9810000053")
    vehicle = await _create(client, owner, registration_number="TN38GG3333")

    response = await client.get(f"{API}/provider/vehicles/{vehicle['id']}", headers=_auth(stranger))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "VEHICLE_NOT_FOUND"


async def test_my_vehicle_by_id_requires_the_provider_role(client: AsyncClient) -> None:
    provider = await _token(client, "9810000054")
    renter = await _token(client, "9810000055", role="RENTER")
    vehicle = await _create(client, provider, registration_number="TN38GG4444")

    anonymous = await client.get(f"{API}/provider/vehicles/{vehicle['id']}")
    as_renter = await client.get(f"{API}/provider/vehicles/{vehicle['id']}", headers=_auth(renter))

    assert anonymous.status_code == 401
    assert as_renter.status_code == 403


# ---------------------------------------------------------------------------
# Listing detail
# ---------------------------------------------------------------------------
async def test_a_single_listing_can_be_fetched_without_a_token(client: AsyncClient) -> None:
    """The screen a renter lands on after tapping a card."""
    token = await _token(client, "9810000030")
    vehicle = await _create(client, token, registration_number="TN38DD1111")

    response = await client.get(f"{API}/vehicles/{vehicle['id']}")

    assert response.status_code == 200
    card = response.json()
    assert card["id"] == vehicle["id"]
    assert card["name"] == "Mahindra 575 DI"
    assert card["price_label"] == "₹500 / hour"


async def test_listing_detail_hides_the_same_fields_as_the_feed(client: AsyncClient) -> None:
    phone = "9810000031"
    token = await _token(client, phone)
    vehicle = await _create(client, token, registration_number="TN38DD2222")

    response = await client.get(f"{API}/vehicles/{vehicle['id']}")

    assert phone not in response.text
    assert "TN38DD2222" not in response.text
    card = response.json()
    assert "registration_number" not in card
    assert "listing_status" not in card


async def test_a_hidden_listing_is_404_by_id_too(client: AsyncClient) -> None:
    """Hidden from the feed must mean hidden by id — otherwise the id is a
    back door around the visibility rules."""
    token = await _token(client, "9810000032")
    vehicle = await _create(client, token, registration_number="TN38DD3333")

    await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}/availability",
        params={"is_available": False},
        headers=_auth(token),
    )

    response = await client.get(f"{API}/vehicles/{vehicle['id']}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "VEHICLE_NOT_FOUND"


async def test_an_unknown_vehicle_id_is_404(client: AsyncClient) -> None:
    response = await client.get(f"{API}/vehicles/3f2a4b6c-0000-4000-8000-000000000000")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Editing a listing
# ---------------------------------------------------------------------------
async def test_a_provider_can_edit_one_field(client: AsyncClient) -> None:
    """Partial update: everything not sent must be left alone."""
    token = await _token(client, "9810000033")
    vehicle = await _create(client, token, registration_number="TN38EE1111")

    response = await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}",
        json={"price_amount": 60000},
        headers=_auth(token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["price_amount"] == 60000
    assert body["price_label"] == "₹600 / hour"
    assert body["name"] == vehicle["name"]  # untouched
    assert body["images"] == vehicle["images"]


async def test_editing_can_change_the_vehicle_type(client: AsyncClient) -> None:
    token = await _token(client, "9810000034")
    vehicle = await _create(client, token, registration_number="TN38EE2222")

    response = await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}",
        json={"vehicle_type_code": "HARVESTER"},
        headers=_auth(token),
    )

    assert response.json()["vehicle_type"]["code"] == "HARVESTER"


async def test_editing_with_an_unknown_type_is_rejected(client: AsyncClient) -> None:
    token = await _token(client, "9810000035")
    vehicle = await _create(client, token, registration_number="TN38EE3333")

    response = await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}",
        json={"vehicle_type_code": "SPACESHIP"},
        headers=_auth(token),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VEHICLE_TYPE_UNKNOWN"


async def test_image_urls_are_replaced_as_a_whole_set(client: AsyncClient) -> None:
    """Replacing three photos with two must leave exactly two, in order.

    This is the case that breaks if the new rows are inserted before the old
    ones are deleted — sort_order is unique per vehicle.
    """
    token = await _token(client, "9810000036")
    vehicle = await _create(
        client,
        token,
        registration_number="TN38EE4444",
        image_public_ids=[f"agri/vehicles/{n}0002" for n in ("a", "b", "c")],
    )

    replacement = ["agri/vehicles/zzz1", "agri/vehicles/yyy1"]
    response = await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}",
        json={"image_public_ids": replacement},
        headers=_auth(token),
    )

    assert response.status_code == 200, response.text
    assert [i["public_id"] for i in response.json()["images"]] == replacement


async def test_reordering_photos_is_just_sending_them_in_a_new_order(
    client: AsyncClient,
) -> None:
    token = await _token(client, "9810000037")
    urls = [f"agri/vehicles/{n}0002" for n in ("a", "b", "c")]
    vehicle = await _create(client, token, registration_number="TN38EE5555", image_public_ids=urls)

    reordered = [urls[2], urls[0], urls[1]]
    response = await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}",
        json={"image_public_ids": reordered},
        headers=_auth(token),
    )

    assert [i["public_id"] for i in response.json()["images"]] == reordered


async def test_omitting_a_coordinate_differs_from_sending_null(client: AsyncClient) -> None:
    """`latitude: null` clears it; omitting `latitude` must not."""
    token = await _token(client, "9810000038")
    vehicle = await _create(
        client, token, registration_number="TN38EE6666", latitude=11.0246, longitude=77.1252
    )
    assert vehicle["latitude"] == 11.0246

    untouched = await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}",
        json={"note": "Updated note."},
        headers=_auth(token),
    )
    assert untouched.json()["latitude"] == 11.0246

    cleared = await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}",
        json={"latitude": None},
        headers=_auth(token),
    )
    assert cleared.json()["latitude"] is None


async def test_editing_cannot_change_the_registration_number(client: AsyncClient) -> None:
    """A different plate is a different vehicle, so the field is not editable.

    `extra` fields are ignored rather than rejected, so the request succeeds and
    the number simply does not move.
    """
    token = await _token(client, "9810000039")
    vehicle = await _create(client, token, registration_number="TN38EE7777")

    response = await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}",
        json={"registration_number": "TN38EE8888"},
        headers=_auth(token),
    )

    assert response.status_code == 200
    assert response.json()["registration_number"] == "TN38EE7777"


async def test_a_provider_cannot_edit_another_providers_vehicle(client: AsyncClient) -> None:
    owner = await _token(client, "9810000040")
    stranger = await _token(client, "9810000041")
    vehicle = await _create(client, owner, registration_number="TN38EE9999")

    response = await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}",
        json={"price_amount": 1},
        headers=_auth(stranger),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "VEHICLE_NOT_FOUND"


async def test_a_renter_cannot_edit_a_vehicle(client: AsyncClient) -> None:
    provider = await _token(client, "9810000042")
    renter = await _token(client, "9810000043", role="RENTER")
    vehicle = await _create(client, provider, registration_number="TN38FF1111")

    response = await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}",
        json={"price_amount": 1},
        headers=_auth(renter),
    )

    assert response.status_code == 403


async def test_editing_rejects_implausible_values(client: AsyncClient) -> None:
    token = await _token(client, "9810000044")
    vehicle = await _create(client, token, registration_number="TN38FF2222")

    for body in (
        {"price_amount": 0},
        {"manufacture_year": 1900},
        {"power_hp": 99999},
        {"image_public_ids": ["http://cdn.example.com/a.jpg"]},
        {"image_public_ids": []},
        {"name": "x"},
    ):
        response = await client.patch(
            f"{API}/provider/vehicles/{vehicle['id']}", json=body, headers=_auth(token)
        )
        assert response.status_code == 422, f"{body} was accepted"


async def test_an_empty_patch_changes_nothing(client: AsyncClient) -> None:
    token = await _token(client, "9810000045")
    vehicle = await _create(client, token, registration_number="TN38FF3333")

    response = await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}", json={}, headers=_auth(token)
    )

    assert response.status_code == 200
    assert response.json()["price_amount"] == vehicle["price_amount"]


async def test_an_edit_is_visible_on_the_public_feed(client: AsyncClient) -> None:
    """End to end: the provider edits, the renter sees it."""
    token = await _token(client, "9810000046")
    vehicle = await _create(client, token, registration_number="TN38FF4444")

    await client.patch(
        f"{API}/provider/vehicles/{vehicle['id']}",
        json={"price_amount": 75000, "note": "Price reduced for the season."},
        headers=_auth(token),
    )

    card = (await client.get(f"{API}/vehicles/{vehicle['id']}")).json()
    assert card["price_label"] == "₹750 / hour"
    assert card["note"] == "Price reduced for the season."


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
