"""Twilio SMS provider — the real gateway, used in production.

This is the second implementation of the `SmsProvider` port. Nothing in the auth
service changes to use it: the factory in `__init__.py` picks it when
`SMS_PROVIDER=twilio`, and business logic still just calls `send_otp`.

WE GENERATE AND VERIFY THE CODE; TWILIO ONLY DELIVERS IT
--------------------------------------------------------
    user enters phone
          ↓
    our backend generates 4-digit OTP, stores the Argon2 hash
          ↓
    Twilio Messaging API  ──SMS──▶  user's phone
          ↓
    user enters the code → our backend verifies it against the stored hash

Twilio sells two products for this and only the first is used here:

  - **Programmable SMS** (this file) — Twilio is a dumb pipe. One API call:
    "send this text to this number". Needs only an account SID, auth token and
    a phone number to send from.
  - **Twilio Verify** — a separate service (`TWILIO_VERIFY_SERVICE_SID`) where
    Twilio generates the code, stores it, counts attempts and verifies it.

Verify is deliberately NOT used: expiry, single-use, attempt limits and the
role stored against each code already live in `app/modules/auth/`, and Verify
would mean deleting all of it, paying per verification, and handing our login
rules to a vendor. `TWILIO_VERIFY_SERVICE_SID` is therefore not a setting in
this app — if you see it in a tutorial, that tutorial is describing Verify.

WHY httpx AND NOT THE `twilio` SDK
----------------------------------
The official SDK is synchronous. Called from an async request handler it blocks
the event loop for the whole round trip to Twilio (~200-500ms), during which
this process serves nobody. The REST API is one form-encoded POST, so we make it
with httpx and stay async.

INDIA: DLT REGISTRATION IS NOT OPTIONAL
---------------------------------------
TRAI requires every commercial SMS sender to register an Entity, a Header
(sender id) and a Template with a DLT operator before messages reach Indian
handsets. Consequences for this file:

  - `SMS_OTP_TEMPLATE` must match the registered template **character for
    character**, or the message is silently dropped by the operator. That is why
    the wording is configuration, not a string literal in the code.
  - Trial Twilio accounts can only send to numbers you have verified in the
    console (error 21608 otherwise).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.sms.base import SmsDeliveryError

log = get_logger(__name__)

_API_ROOT = "https://api.twilio.com/2010-04-01"

#: Twilio error codes worth naming in the logs, because each has a specific
#: fix and they are the ones you actually hit during setup.
#: https://www.twilio.com/docs/api/errors
_KNOWN_ERRORS = {
    20003: "Authentication failed — check TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN.",
    21211: "Twilio rejected the destination number as invalid.",
    21408: "This Twilio account is not permitted to send to this country — "
    "enable the destination geo permissions in the console.",
    21608: "Trial account: the destination number must be verified in the Twilio console first.",
    21610: "The recipient has unsubscribed (replied STOP) and cannot be messaged.",
    30007: "Carrier filtered the message — for India this usually means the DLT "
    "template or header does not match SMS_OTP_TEMPLATE.",
}


class TwilioSmsProvider:
    """Sends OTPs through Twilio's Programmable SMS API."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """`transport` exists so tests can answer the request without a network.

        Production leaves it as None and httpx uses its real transport.
        """
        self._account_sid = settings.twilio_account_sid
        self._from_number = settings.twilio_phone_number
        self._template = settings.sms_otp_template
        self._otp_ttl_minutes = max(settings.otp_ttl_seconds // 60, 1)

        # One client for the process, so TCP + TLS handshakes are reused across
        # requests instead of paid for on every OTP. Constructing it opens no
        # socket, so this is safe outside an event loop.
        self._client = httpx.AsyncClient(
            base_url=f"{_API_ROOT}/Accounts/{self._account_sid}",
            auth=(self._account_sid, settings.twilio_auth_token.get_secret_value()),
            timeout=httpx.Timeout(settings.twilio_timeout_seconds),
            transport=transport,
        )

    async def send_otp(self, *, phone_e164: str, code: str) -> None:
        """Deliver the code, or raise `SmsDeliveryError`.

        Raising matters: the caller creates the OTP row *before* sending, so a
        failure here rolls the row back rather than leaving a code nobody
        received but that still counts against the user's limits.
        """
        # The entire Twilio contract: three form fields.
        payload: dict[str, str] = {
            "To": phone_e164,
            "From": self._from_number,
            "Body": self._render(code),
        }

        try:
            response = await self._client.post("/Messages.json", data=payload)
        except httpx.HTTPError as exc:
            # Timeout, DNS failure, connection reset — we do not know whether
            # Twilio accepted it, so treat it as failed and let the user resend.
            log.error(
                "twilio_sms_transport_error",
                phone=phone_e164,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise SmsDeliveryError(f"Could not reach Twilio: {exc}") from exc

        if response.status_code >= 400:
            details = self._error_details(response)
            log.error(
                "twilio_sms_rejected",
                phone=phone_e164,
                status_code=response.status_code,
                twilio_code=details.get("code"),
                twilio_message=details.get("message"),
                hint=_KNOWN_ERRORS.get(details.get("code", 0)),
            )
            raise SmsDeliveryError(
                f"Twilio rejected the message (HTTP {response.status_code}, "
                f"code {details.get('code')})"
            )

        body = self._json(response)
        # Note `status` here is "queued"/"accepted" — Twilio has taken the
        # message, not yet delivered it. Delivery confirmation needs a status
        # callback webhook, which we do not have yet.
        log.info(
            "twilio_sms_queued",
            phone=phone_e164,
            message_sid=body.get("sid"),
            status=body.get("status"),
        )

    def _render(self, code: str) -> str:
        """Build the message body from the configured template."""
        return self._template.format(code=code, minutes=self._otp_ttl_minutes)

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            parsed = response.json()
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @classmethod
    def _error_details(cls, response: httpx.Response) -> dict[str, Any]:
        """Twilio returns errors as {"code": 21211, "message": "...", ...}."""
        body = cls._json(response)
        code = body.get("code")
        return {
            "code": code if isinstance(code, int) else None,
            "message": body.get("message") or response.text[:200],
        }

    async def aclose(self) -> None:
        """Close the pooled connections. Called on application shutdown."""
        await self._client.aclose()
