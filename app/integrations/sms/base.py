"""The SMS port — the interface the app depends on.

The auth service calls `sms.send_otp(...)` and has no idea whether that reaches
a real gateway or a log line. That is the whole point: the environment decides
which implementation is injected, so there is never an
`if settings.app_env == "production"` sitting inside business logic.

It also means tests run against the fake and never send a message or spend
money.
"""

from __future__ import annotations

from typing import Protocol


class SmsProvider(Protocol):
    """Anything that can deliver an OTP.

    A Protocol is Python's structural interface: any class with a matching
    `send_otp` method satisfies it, with no inheritance required. mypy checks
    the shape for us.
    """

    async def send_otp(self, *, phone_e164: str, code: str) -> None:
        """Deliver `code` to `phone_e164`.

        Raise `SmsDeliveryError` if delivery definitively failed.
        """
        ...


class SmsDeliveryError(Exception):
    """Raised when an SMS could not be delivered."""
