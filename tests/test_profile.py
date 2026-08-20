"""Tests for the onboarding flow: /me onboarding status and PATCH /me.

The flow the app follows after OTP verification:

    verify OTP → GET /me → needs_profile_completion?
      false → home
      true  → Name/Email/Location form → PATCH /me → home

OTP verification itself never collects profile fields (see test_auth.py).
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User

AUTH = "/api/v1/auth"
API = "/api/v1"


async def _login(client: AsyncClient, phone: str, role: str = "USER") -> dict:
    """Complete a login WITHOUT profile fields — the new app flow."""
    await client.post(f"{AUTH}/otp/request", json={"phone": phone, "role": role})
    response = await client.post(f"{AUTH}/otp/verify", json={"phone": phone, "code": "0000"})
    assert response.status_code == 200, response.text
    return response.json()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _me(client: AsyncClient, token: str) -> dict:
    response = await client.get(f"{API}/me", headers=_auth(token))
    assert response.status_code == 200, response.text
    return response.json()


async def _complete_profile(client: AsyncClient, token: str) -> None:
    response = await client.patch(
        f"{API}/me",
        json={"full_name": "John", "email": "john@example.com", "address": "Chennai"},
        headers=_auth(token),
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Onboarding status on /me
# ---------------------------------------------------------------------------
async def test_a_new_user_needs_profile_completion(client: AsyncClient, phone: str) -> None:
    """Scenario 1: brand-new number → verify → /me says complete the profile."""
    tokens = await _login(client, phone)

    me = await _me(client, tokens["access_token"])

    assert me["full_name"] is None
    assert me["email"] is None
    assert me["address"] is None
    assert me["onboarding"] == {"profile_completed": False, "needs_profile_completion": True}
    assert me["profile_complete"] is False  # mirrors onboarding


async def test_an_existing_incomplete_user_needs_profile_completion(
    client: AsyncClient, phone: str
) -> None:
    """Scenario 3: the user EXISTS (not new) yet still lacks profile fields.

    Onboarding is never derived from whether the phone number is new.
    """
    await _login(client, phone)
    second = await _login(client, phone)  # same number, second session

    me = await _me(client, second["access_token"])

    assert second["is_new_user"] is False  # genuinely an existing user
    assert me["onboarding"]["needs_profile_completion"] is True


async def test_a_completed_profile_does_not_need_completion(
    client: AsyncClient, phone: str
) -> None:
    """Scenario 2: completed profile stays complete across logins."""
    first = await _login(client, phone)
    await _complete_profile(client, first["access_token"])

    second = await _login(client, phone)  # log in again, like a returning user

    me = await _me(client, second["access_token"])
    assert me["full_name"] == "John"
    assert me["email"] == "john@example.com"
    assert me["address"] == "Chennai"
    assert me["onboarding"] == {"profile_completed": True, "needs_profile_completion": False}
    assert me["profile_complete"] is True


# ---------------------------------------------------------------------------
# Roles — one phone, both roles, one user row
# ---------------------------------------------------------------------------
async def test_requesting_provider_adds_the_role_without_a_duplicate_user(
    client: AsyncClient, phone: str, db_session: AsyncSession
) -> None:
    """Scenario 4: USER → requests PROVIDER → both roles, still ONE user row."""
    first = await _login(client, phone, role="USER")
    assert first["user"]["roles"] == ["USER"]

    second = await _login(client, phone, role="PROVIDER")

    assert second["user"]["roles"] == ["USER", "PROVIDER"]
    me = await _me(client, second["access_token"])
    assert me["roles"] == ["USER", "PROVIDER"]

    # The unique (user_id, role) constraint is the backstop; this proves the
    # code path never needed it — one row per phone, roles added on top.
    count = (
        await db_session.execute(
            select(func.count()).select_from(User).where(User.phone_e164 == "+919800000001")
        )
    ).scalar_one()
    assert count == 1


# ---------------------------------------------------------------------------
# PATCH /me
# ---------------------------------------------------------------------------
async def test_patch_me_updates_name_email_and_address(client: AsyncClient, phone: str) -> None:
    """Scenario 5: the profile form writes all three fields."""
    tokens = await _login(client, phone)

    response = await client.patch(
        f"{API}/me",
        json={"full_name": "John", "email": "john@example.com", "address": "Chennai"},
        headers=_auth(tokens["access_token"]),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "John"
    assert body["email"] == "john@example.com"
    assert body["address"] == "Chennai"


async def test_after_profile_completion_me_reports_needs_completion_false(
    client: AsyncClient, phone: str
) -> None:
    """Scenario 6: PATCH the form → /me flips to needs_profile_completion=false."""
    tokens = await _login(client, phone)
    assert (await _me(client, tokens["access_token"]))["onboarding"][
        "needs_profile_completion"
    ] is True

    await _complete_profile(client, tokens["access_token"])

    me = await _me(client, tokens["access_token"])
    assert me["onboarding"]["needs_profile_completion"] is False
    assert me["onboarding"]["profile_completed"] is True


async def test_a_partial_profile_still_needs_completion(client: AsyncClient, phone: str) -> None:
    """Name alone no longer counts as done — all three fields must be present."""
    tokens = await _login(client, phone)

    await client.patch(
        f"{API}/me", json={"full_name": "John"}, headers=_auth(tokens["access_token"])
    )

    me = await _me(client, tokens["access_token"])
    assert me["onboarding"]["needs_profile_completion"] is True


# ---------------------------------------------------------------------------
# Ownership — the profile is identified by the token, never by a client id
# ---------------------------------------------------------------------------
async def test_patch_me_rejects_a_user_id_field(client: AsyncClient, phone: str) -> None:
    """Scenario 7a: the API takes no user id — the token is the identity."""
    tokens = await _login(client, phone)

    response = await client.patch(
        f"{API}/me",
        json={"full_name": "John", "user_id": "00000000-0000-0000-0000-000000000000"},
        headers=_auth(tokens["access_token"]),
    )

    assert response.status_code == 422
    fields = [f["field"] for f in response.json()["error"]["details"]["fields"]]
    assert "user_id" in fields


async def test_a_user_cannot_modify_another_users_profile(client: AsyncClient, phone: str) -> None:
    """Scenario 7b: patching with your own token never touches another user."""
    first = await _login(client, phone)
    second = await _login(client, "9800000002")

    response = await client.patch(
        f"{API}/me",
        json={"full_name": "Intruder", "email": "intruder@example.com", "address": "Delhi"},
        headers=_auth(first["access_token"]),
    )
    assert response.status_code == 200

    other = await _me(client, second["access_token"])
    assert other["full_name"] is None
    assert other["email"] is None
    assert other["address"] is None
