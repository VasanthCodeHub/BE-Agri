"""Tests for the login flow.

These cover the behaviour that matters most: that only verified numbers become
users, that a role is required, that ADMIN cannot be self-assigned, that the
attempt limit actually holds, and that a stolen refresh token is detected.

The last two are here because both were genuinely broken on first
implementation — the writes were being rolled back by the error response. Tests
exist so that cannot silently return.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.integrations.sms as sms_registry
from app.integrations.sms.base import SmsDeliveryError
from app.integrations.sms.fake import FakeSmsProvider
from app.modules.users.models import User

AUTH = "/api/v1/auth"


async def _login(client: AsyncClient, phone: str, role: str = "USER") -> dict:
    """Helper: complete a login with the dev bypass code.

    Sends ONLY phone + code — the verify endpoint is authentication-only;
    profile fields are collected afterwards via PATCH /me.
    """
    await client.post(f"{AUTH}/otp/request", json={"phone": phone, "role": role})
    response = await client.post(f"{AUTH}/otp/verify", json={"phone": phone, "code": "0000"})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Step 1 — requesting a code
# ---------------------------------------------------------------------------
async def test_request_otp_reports_new_user(client: AsyncClient, phone: str) -> None:
    response = await client.post(f"{AUTH}/otp/request", json={"phone": phone, "role": "USER"})

    assert response.status_code == 200
    body = response.json()
    assert body["is_new_user"] is True
    # Profile fields are collected after login (PATCH /me), never pre-verify.
    assert body["name_required"] is False
    assert body["otp_sent"] is True


async def test_response_never_contains_the_full_phone_number(
    client: AsyncClient, phone: str
) -> None:
    """The number is masked even for its owner (ADR-009)."""
    response = await client.post(f"{AUTH}/otp/request", json={"phone": phone, "role": "USER"})

    body = response.json()
    assert body["phone"] == "+9198****0001"
    assert phone not in response.text


async def test_a_failed_sms_send_is_a_503_not_a_500(
    client: AsyncClient, phone: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead gateway is a retryable outage, and the app must say so.

    Without the handler in the service this surfaced as a generic 500
    INTERNAL_ERROR, which tells the mobile app nothing about whether retrying
    is worth it.
    """

    class BrokenGateway:
        async def send_otp(self, *, phone_e164: str, code: str) -> None:
            raise SmsDeliveryError("gateway unreachable")

    monkeypatch.setattr(sms_registry, "_provider", BrokenGateway())

    response = await client.post(f"{AUTH}/otp/request", json={"phone": phone, "role": "USER"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "OTP_SEND_FAILED"


async def test_role_is_required(client: AsyncClient, phone: str) -> None:
    response = await client.post(f"{AUTH}/otp/request", json={"phone": phone})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["fields"][0]["field"] == "role"


async def test_admin_role_cannot_be_self_assigned(client: AsyncClient, phone: str) -> None:
    """Privilege escalation guard: the public endpoint accepts only two roles."""
    response = await client.post(f"{AUTH}/otp/request", json={"phone": phone, "role": "ADMIN"})

    assert response.status_code == 422


async def test_invalid_phone_is_rejected(client: AsyncClient) -> None:
    response = await client.post(f"{AUTH}/otp/request", json={"phone": "12345", "role": "USER"})

    assert response.status_code == 422
    assert response.json()["error"]["details"]["fields"][0]["field"] == "phone"


async def test_phone_formats_all_normalise_to_one_user(client: AsyncClient) -> None:
    """The same person typing their number four ways must be one account."""
    first = await client.post(f"{AUTH}/otp/request", json={"phone": "9800000002", "role": "USER"})
    assert first.json()["is_new_user"] is True

    await _login(client, "9800000002")

    for variant in ("09800000002", "+919800000002", "91 98000 00002", "9800000002"):
        response = await client.post(f"{AUTH}/otp/request", json={"phone": variant, "role": "USER"})
        assert response.json()["is_new_user"] is False, f"{variant} created a second account"


# ---------------------------------------------------------------------------
# Step 2 — verifying
# ---------------------------------------------------------------------------
async def test_verify_creates_the_user_and_returns_tokens(client: AsyncClient, phone: str) -> None:
    body = await _login(client, phone, role="PROVIDER")

    assert body["is_new_user"] is True
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["expires_in"] == 900  # 15 minutes
    assert body["refresh_expires_in"] == 2_592_000  # 30 days
    # Verify only authenticates — the profile is filled later via PATCH /me.
    assert body["user"]["full_name"] is None
    assert body["user"]["roles"] == ["PROVIDER"]
    assert body["user"]["active_role"] == "PROVIDER"


async def test_a_new_user_can_verify_without_profile_fields(
    client: AsyncClient, phone: str
) -> None:
    """OTP verification only authenticates — name/email/location come later.

    The profile form is shown AFTER login, driven by `needs_profile_completion`
    on GET /me, and saved through PATCH /me. Requiring a name here would break
    that flow.
    """
    await client.post(f"{AUTH}/otp/request", json={"phone": phone, "role": "USER"})

    response = await client.post(f"{AUTH}/otp/verify", json={"phone": phone, "code": "0000"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["full_name"] is None
    assert body["user"]["onboarding"]["needs_profile_completion"] is True


async def test_profile_fields_are_rejected_at_verification(client: AsyncClient, phone: str) -> None:
    """The verify body accepts only phone + code.

    `extra="forbid"` turns a client that still sends profile fields into a
    clear 422 instead of a silent ignore — the contract is enforced, not
    aspirational.
    """
    await client.post(f"{AUTH}/otp/request", json={"phone": phone, "role": "USER"})

    response = await client.post(
        f"{AUTH}/otp/verify",
        json={
            "phone": phone,
            "code": "0000",
            "name": "Vasanth",
            "email": "vasanth@example.com",
            "address": "12 Gandhi Street, Sulur",
            "latitude": 11.0246,
            "longitude": 77.1252,
        },
    )

    assert response.status_code == 422
    fields = [f["field"] for f in response.json()["error"]["details"]["fields"]]
    assert "name" in fields
    assert "email" in fields
    assert "address" in fields
    assert "latitude" in fields
    assert "longitude" in fields


async def test_the_real_otp_works_not_only_the_bypass(
    client: AsyncClient, phone: str, sms: FakeSmsProvider
) -> None:
    """Read the code the fake provider 'sent' and use it."""
    await client.post(f"{AUTH}/otp/request", json={"phone": phone, "role": "USER"})

    sent_phone, code = sms.sent[-1]
    assert sent_phone == "+919800000001"
    assert len(code) == 4 and code.isdigit()

    response = await client.post(f"{AUTH}/otp/verify", json={"phone": phone, "code": code})
    assert response.status_code == 200


async def test_a_code_works_only_once(client: AsyncClient, phone: str) -> None:
    await _login(client, phone)

    # The bypass code needs an active OTP record; the first login consumed it.
    response = await client.post(f"{AUTH}/otp/verify", json={"phone": phone, "code": "0000"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OTP_NOT_FOUND"


async def test_wrong_code_decrements_remaining_attempts(client: AsyncClient, phone: str) -> None:
    """Regression test: the attempt counter must PERSIST across requests.

    This was broken initially — the increment was rolled back by the error
    response, so an attacker had unlimited guesses at a 4-digit code.
    """
    await client.post(f"{AUTH}/otp/request", json={"phone": phone, "role": "USER"})

    seen = []
    for _ in range(5):
        response = await client.post(f"{AUTH}/otp/verify", json={"phone": phone, "code": "1111"})
        assert response.status_code == 400
        error = response.json()["error"]
        assert error["code"] == "OTP_INVALID"
        seen.append(error["details"]["remaining_attempts"])

    assert seen == [4, 3, 2, 1, 0], f"attempts did not persist: {seen}"

    # The code is burned once the limit is reached.
    final = await client.post(f"{AUTH}/otp/verify", json={"phone": phone, "code": "0000"})
    assert final.json()["error"]["code"] == "OTP_NOT_FOUND"


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
async def test_one_phone_can_hold_both_roles(client: AsyncClient, phone: str) -> None:
    """Logging in with a role the user lacks grants it."""
    first = await _login(client, phone, role="PROVIDER")
    assert first["user"]["roles"] == ["PROVIDER"]

    second = await _login(client, phone, role="USER")

    assert second["is_new_user"] is False
    assert second["user"]["roles"] == ["PROVIDER", "USER"]
    assert second["user"]["active_role"] == "USER"


async def test_the_role_cannot_be_changed_at_verification(client: AsyncClient, phone: str) -> None:
    """A client cannot request as USER then verify as something else.

    `role` is not accepted by the verify endpoint at all — it comes from the
    stored OTP row, and any attempt to smuggle one in is a 422.
    """
    await client.post(f"{AUTH}/otp/request", json={"phone": phone, "role": "USER"})

    response = await client.post(
        f"{AUTH}/otp/verify", json={"phone": phone, "code": "0000", "role": "PROVIDER"}
    )

    assert response.status_code == 422
    fields = [f["field"] for f in response.json()["error"]["details"]["fields"]]
    assert "role" in fields


async def test_requesting_an_already_held_role_changes_nothing(
    client: AsyncClient, phone: str, db_session: AsyncSession
) -> None:
    """Scenario 5: [USER, PROVIDER] requests PROVIDER again.

    Same account, no duplicate role, no duplicate user. The unique
    (user_id, role) index is the backstop; this proves the code path never
    needs it for a repeated role.
    """
    await _login(client, phone, role="PROVIDER")
    await _login(client, phone, role="USER")
    third = await _login(client, phone, role="PROVIDER")

    assert third["user"]["roles"] == ["PROVIDER", "USER"]

    user_row = (
        await db_session.execute(select(User).where(User.phone_e164 == "+919800000001"))
    ).scalar_one()
    assert len(user_row.role_assignments) == 2


async def test_verifying_the_same_phone_twice_reuses_the_one_account(
    client: AsyncClient, phone: str, db_session: AsyncSession
) -> None:
    """Scenario 9: a second verify of the same phone must not duplicate."""
    first = await _login(client, phone, role="USER")
    assert first["is_new_user"] is True

    second = await _login(client, phone, role="USER")
    assert second["is_new_user"] is False

    count = (
        await db_session.execute(
            select(func.count()).select_from(User).where(User.phone_e164 == "+919800000001")
        )
    ).scalar_one()
    assert count == 1


# ---------------------------------------------------------------------------
# Session check — /me
# ---------------------------------------------------------------------------
async def test_me_returns_the_current_user(client: AsyncClient, phone: str) -> None:
    tokens = await _login(client, phone)

    response = await client.get(
        f"{AUTH}/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )

    assert response.status_code == 200
    # Verify never collected a name — profile fields arrive via PATCH /me.
    assert response.json()["full_name"] is None


async def test_me_requires_a_token(client: AsyncClient) -> None:
    response = await client.get(f"{AUTH}/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_MISSING"


async def test_me_rejects_a_garbage_token(client: AsyncClient) -> None:
    response = await client.get(f"{AUTH}/me", headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


async def test_a_refresh_token_cannot_be_used_as_an_access_token(
    client: AsyncClient, phone: str
) -> None:
    """The `typ` claim check stops the two token kinds being confused."""
    tokens = await _login(client, phone)

    response = await client.get(
        f"{AUTH}/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"}
    )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Refresh and logout
# ---------------------------------------------------------------------------
async def test_refresh_returns_a_new_pair(client: AsyncClient, phone: str) -> None:
    tokens = await _login(client, phone)

    response = await client.post(f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 200
    refreshed = response.json()
    assert refreshed["refresh_token"] != tokens["refresh_token"], "token was not rotated"
    assert refreshed["access_token"]


async def test_refresh_keeps_the_active_role(client: AsyncClient, phone: str) -> None:
    """A provider refreshing must not land in the USER experience."""
    tokens = await _login(client, phone, role="PROVIDER")

    refreshed = await client.post(
        f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]}
    )

    me = await client.get(
        f"{AUTH}/me",
        headers={"Authorization": f"Bearer {refreshed.json()['access_token']}"},
    )
    assert me.json()["active_role"] == "PROVIDER"


async def test_reusing_a_rotated_token_revokes_the_whole_family(
    client: AsyncClient, phone: str
) -> None:
    """Regression test: revocation must PERSIST past the error response.

    This was broken initially — the revocation was rolled back, so a stolen
    token kept working after detection.
    """
    tokens = await _login(client, phone)
    old = tokens["refresh_token"]

    rotated = await client.post(f"{AUTH}/refresh", json={"refresh_token": old})
    new = rotated.json()["refresh_token"]

    # Presenting the old token signals a copy exists.
    reuse = await client.post(f"{AUTH}/refresh", json={"refresh_token": old})
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "TOKEN_REUSED"

    # ...so the whole chain dies, including the token the thief would hold.
    after = await client.post(f"{AUTH}/refresh", json={"refresh_token": new})
    assert after.status_code == 401, "family revocation did not persist"


async def test_unknown_refresh_token_is_rejected(client: AsyncClient) -> None:
    response = await client.post(f"{AUTH}/refresh", json={"refresh_token": "x" * 40})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"


async def test_logout_revokes_the_session(client: AsyncClient, phone: str) -> None:
    tokens = await _login(client, phone)

    logout = await client.post(f"{AUTH}/logout", json={"refresh_token": tokens["refresh_token"]})
    assert logout.status_code == 204

    after = await client.post(f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert after.status_code == 401


async def test_logout_with_an_unknown_token_still_returns_204(client: AsyncClient) -> None:
    """Responding differently would let a caller probe which tokens are valid."""
    response = await client.post(f"{AUTH}/logout", json={"refresh_token": "y" * 40})

    assert response.status_code == 204
