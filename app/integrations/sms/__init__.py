"""SMS delivery.

The factory below is the ONLY place that decides which implementation to use.
Business logic just receives an `SmsProvider` and calls it.

    SMS_PROVIDER=fake     local  → logs the OTP to your terminal, costs nothing
    SMS_PROVIDER=twilio   prod   → sends a real SMS

Adding another vendor later means writing `msg91.py` with a `send_otp` method
and adding one branch here. No service, router or test changes.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.integrations.sms.base import SmsDeliveryError, SmsProvider
from app.integrations.sms.fake import FakeSmsProvider
from app.integrations.sms.twilio import TwilioSmsProvider

__all__ = [
    "SmsDeliveryError",
    "SmsProvider",
    "close_sms_provider",
    "get_sms_provider",
    "reset_sms_provider",
]

log = get_logger(__name__)

#: Process-wide singleton. Note we cannot use @lru_cache here: pydantic
#: Settings objects are not hashable, so caching on the argument would raise
#: TypeError. A module-level singleton does the same job.
_provider: SmsProvider | None = None


def get_sms_provider(settings: Settings | None = None) -> SmsProvider:
    """Return the configured SMS provider, creating it on first use.

    A singleton for two reasons: the fake provider records every code it "sent"
    in a list that tests read, and the Twilio provider holds a pooled HTTP
    client that should be reused across requests rather than rebuilt per OTP.
    """
    global _provider
    if _provider is None:
        settings = settings or get_settings()

        if settings.sms_provider == "twilio":
            log.info(
                "sms_provider_selected",
                provider="twilio",
                sender=settings.twilio_phone_number,
            )
            _provider = TwilioSmsProvider(settings)
        elif settings.sms_provider == "fake":
            if settings.is_production:
                # Belt and braces: config already refuses to start production
                # with the fake provider, but production must never silently
                # swallow OTPs, so the check lives on both sides.
                raise RuntimeError("SMS_PROVIDER=fake is not allowed in production.")
            log.info("sms_provider_selected", provider="fake")
            _provider = FakeSmsProvider()
        else:
            # Unreachable: Settings validates SMS_PROVIDER against a known list.
            raise RuntimeError(f"Unknown SMS_PROVIDER={settings.sms_provider!r}.")

    return _provider


def reset_sms_provider() -> None:
    """Clear the singleton. Used by tests to start from a clean state."""
    global _provider
    _provider = None


async def close_sms_provider() -> None:
    """Release any network resources the provider holds.

    Called on application shutdown. Only the Twilio provider needs it (it owns
    an HTTP connection pool), hence the `aclose` check rather than adding a
    method every implementation would have to stub out.
    """
    global _provider
    closer = getattr(_provider, "aclose", None)
    if closer is not None:
        await closer()
    _provider = None
