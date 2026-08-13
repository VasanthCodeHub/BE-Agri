"""Fake SMS provider — prints the OTP instead of sending it.

This is what makes local development possible with no SMS vendor, no DLT
registration, and no cost. Request an OTP and the code appears in your
terminal:

    [warning] fake_sms_otp  phone=+9198****3210  otp=482913

Copy it into /auth/otp/verify and you are logged in.

It is also what the test suite uses, so tests never send a real message.

Note this logs at WARNING level, deliberately: it should be conspicuous in the
output, and impossible to mistake for a real SMS being sent. The `otp` field is
redacted automatically in production by the logging scrubber — but this
provider should never run in production anyway.
"""

from __future__ import annotations

from app.core.logging import get_logger

log = get_logger(__name__)


class FakeSmsProvider:
    """Logs the OTP instead of delivering it."""

    def __init__(self) -> None:
        #: Every code sent, in order. Lets a test assert what was "sent"
        #: without parsing log output.
        self.sent: list[tuple[str, str]] = []

    async def send_otp(self, *, phone_e164: str, code: str) -> None:
        self.sent.append((phone_e164, code))
        log.warning(
            "fake_sms_otp",
            phone=phone_e164,  # masked by the logging scrubber
            otp=code,
            hint="No real SMS sent. Use this code to log in.",
        )
