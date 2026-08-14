"""Tests for the Twilio SMS provider and the config that selects it.

No network here: `httpx.MockTransport` answers the request in-process, so these
tests are fast, free, and send nothing to a real phone.

What is worth testing about an adapter is the contract with the vendor — the
exact fields Twilio expects, and that every failure becomes an
`SmsDeliveryError` rather than leaking an httpx exception into business logic.
"""

from __future__ import annotations

import base64

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.integrations.sms.base import SmsDeliveryError
from app.integrations.sms.twilio import TwilioSmsProvider

ACCOUNT_SID = "AC" + "0" * 32
#: Shaped like a real one — 32 hex characters — because the config validates it.
AUTH_TOKEN = "9f8e7d6c5b4a39281706f5e4d3c2b1a0"
PHONE = "+919800000001"

_ACCEPTED = {"sid": "SM" + "1" * 32, "status": "queued"}


def _settings(**overrides: object) -> Settings:
    """Twilio-configured settings, ignoring the developer's real .env."""
    base: dict[str, object] = {
        "_env_file": None,
        "app_env": "local",
        "jwt_secret_key": "test-secret-key-not-used-in-any-real-environment",
        "sms_provider": "twilio",
        "twilio_account_sid": ACCOUNT_SID,
        "twilio_auth_token": AUTH_TOKEN,
        "twilio_phone_number": "+12025550123",
        "otp_ttl_seconds": 300,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _provider(
    handler: object, **overrides: object
) -> tuple[TwilioSmsProvider, list[httpx.Request]]:
    """A provider whose HTTP calls are answered by `handler`."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)  # type: ignore[operator]

    return TwilioSmsProvider(_settings(**overrides), transport=httpx.MockTransport(record)), seen


def _ok(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(201, json=_ACCEPTED)


# ---------------------------------------------------------------------------
# The request we send to Twilio
# ---------------------------------------------------------------------------
async def test_posts_the_message_to_the_right_url() -> None:
    provider, seen = _provider(_ok)

    await provider.send_otp(phone_e164=PHONE, code="1234")

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == (
        f"https://api.twilio.com/2010-04-01/Accounts/{ACCOUNT_SID}/Messages.json"
    )


async def test_sends_the_fields_twilio_expects() -> None:
    provider, seen = _provider(_ok)

    await provider.send_otp(phone_e164=PHONE, code="1234")

    sent = httpx.QueryParams(seen[0].content.decode())
    assert sent["To"] == PHONE
    assert sent["From"] == "+12025550123"
    assert "1234" in sent["Body"]


async def test_no_verify_service_is_involved() -> None:
    """We own the code. Twilio must be called as a plain messaging pipe.

    A Verify-based integration would POST to /v2/Services/VA.../Verifications
    and send no Body at all — this asserts we are not doing that.
    """
    provider, seen = _provider(_ok)

    await provider.send_otp(phone_e164=PHONE, code="1234")

    assert seen[0].url.path.endswith("/Messages.json")
    assert "Verifications" not in str(seen[0].url)
    assert "Body" in httpx.QueryParams(seen[0].content.decode())


async def test_authenticates_with_the_account_sid_and_token() -> None:
    provider, seen = _provider(_ok)

    await provider.send_otp(phone_e164=PHONE, code="1234")

    expected = base64.b64encode(f"{ACCOUNT_SID}:{AUTH_TOKEN}".encode()).decode()
    assert seen[0].headers["authorization"] == f"Basic {expected}"


async def test_message_body_comes_from_the_template() -> None:
    """DLT requires exact wording, so the template must be used verbatim."""
    provider, seen = _provider(_ok, sms_otp_template="Code {code}. Valid {minutes} min.")

    await provider.send_otp(phone_e164=PHONE, code="4321")

    assert httpx.QueryParams(seen[0].content.decode())["Body"] == "Code 4321. Valid 5 min."


# ---------------------------------------------------------------------------
# Failures must all arrive as SmsDeliveryError
# ---------------------------------------------------------------------------
async def test_rejection_raises_sms_delivery_error() -> None:
    def rejected(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": 21211, "message": "Invalid 'To' number"})

    provider, _ = _provider(rejected)

    with pytest.raises(SmsDeliveryError):
        await provider.send_otp(phone_e164=PHONE, code="1234")


async def test_bad_credentials_raise_sms_delivery_error() -> None:
    def unauthorised(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": 20003, "message": "Authenticate"})

    provider, _ = _provider(unauthorised)

    with pytest.raises(SmsDeliveryError):
        await provider.send_otp(phone_e164=PHONE, code="1234")


async def test_a_timeout_raises_sms_delivery_error() -> None:
    """A network failure must not escape as an httpx exception."""

    def times_out(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    provider, _ = _provider(times_out)

    with pytest.raises(SmsDeliveryError):
        await provider.send_otp(phone_e164=PHONE, code="1234")


async def test_a_non_json_error_body_is_still_handled() -> None:
    """Gateways sometimes return an HTML error page. Do not crash on it."""

    def html_error(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="<html>Bad Gateway</html>")

    provider, _ = _provider(html_error)

    with pytest.raises(SmsDeliveryError):
        await provider.send_otp(phone_e164=PHONE, code="1234")


# ---------------------------------------------------------------------------
# Configuration guards — these fail at STARTUP, not at the first login
# ---------------------------------------------------------------------------
def test_twilio_without_credentials_refuses_to_start() -> None:
    with pytest.raises(ValidationError, match="TWILIO_ACCOUNT_SID"):
        Settings(
            _env_file=None,
            sms_provider="twilio",
            twilio_account_sid="",
            twilio_auth_token="",
            twilio_phone_number="",
        )


def test_twilio_without_a_sender_number_refuses_to_start() -> None:
    with pytest.raises(ValidationError, match="TWILIO_PHONE_NUMBER"):
        _settings(twilio_phone_number="")


def test_an_api_key_in_place_of_the_account_sid_is_caught() -> None:
    """An SK... value is an API key. Only the AC... Account SID belongs here."""
    with pytest.raises(ValidationError, match="should start with 'AC'"):
        _settings(twilio_account_sid="SK" + "0" * 32)


def test_the_account_sid_pasted_into_the_auth_token_is_caught() -> None:
    """Easy slip: both values sit in the same console panel.

    Twilio's own reply to this is a bare "20003 Authenticate" on the first send,
    so the mistake has to be named here or it costs an afternoon.
    """
    with pytest.raises(ValidationError, match="looks like the Account SID"):
        _settings(twilio_auth_token=ACCOUNT_SID)


def test_a_truncated_auth_token_is_caught() -> None:
    """32 hex characters exactly. A short paste is the common version of this."""
    with pytest.raises(ValidationError, match="32 hexadecimal characters"):
        _settings(twilio_auth_token=AUTH_TOKEN[:20])


def test_the_sender_number_must_be_e164() -> None:
    """12025550123 without the '+' is rejected by Twilio, so reject it earlier."""
    with pytest.raises(ValidationError, match=r"E\.164"):
        _settings(twilio_phone_number="12025550123")


def test_an_unknown_provider_name_is_rejected() -> None:
    with pytest.raises(ValidationError, match="SMS_PROVIDER"):
        Settings(_env_file=None, sms_provider="twillio")


def test_a_template_without_the_code_placeholder_is_rejected() -> None:
    """Otherwise we would send a perfectly formatted SMS containing no code."""
    with pytest.raises(ValidationError, match="must contain the placeholder"):
        _settings(sms_otp_template="Your verification code is on its way.")


def test_production_refuses_the_fake_provider() -> None:
    """Nobody could log in: the fake provider logs the OTP instead of sending it."""
    with pytest.raises(ValidationError, match="not allowed in production"):
        Settings(
            _env_file=None,
            app_env="production",
            debug=False,
            jwt_secret_key="a-real-production-secret-of-sufficient-length",
            cors_origins="https://app.example.com",
            otp_dev_bypass_code="",
            sms_provider="fake",
        )


def test_production_with_twilio_is_accepted() -> None:
    settings = Settings(
        _env_file=None,
        app_env="production",
        debug=False,
        jwt_secret_key="a-real-production-secret-of-sufficient-length",
        cors_origins="https://app.example.com",
        otp_dev_bypass_code="",
        sms_provider="twilio",
        twilio_account_sid=ACCOUNT_SID,
        twilio_auth_token=AUTH_TOKEN,
        twilio_phone_number="+12025550123",
        # Production also requires Cloudinary — providers cannot list a vehicle
        # without photos, so it is not optional there.
        cloudinary_cloud_name="prod-cloud",
        cloudinary_api_key="123456789012345",
        cloudinary_api_secret="prod-cloudinary-secret",
    )

    assert settings.is_production
    assert settings.sms_provider == "twilio"
    assert settings.cloudinary_configured
