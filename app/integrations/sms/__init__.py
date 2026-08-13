"""SMS delivery.

The factory below is the ONLY place that decides which implementation to use.
Business logic just receives an `SmsProvider` and calls it.

Adding a real vendor later means writing `msg91.py` with a `send_otp` method
and adding one branch here. No service, router or test changes.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.integrations.sms.base import SmsDeliveryError, SmsProvider
from app.integrations.sms.fake import FakeSmsProvider

__all__ = ["SmsDeliveryError", "SmsProvider", "get_sms_provider", "reset_sms_provider"]

log = get_logger(__name__)

#: Process-wide singleton. Note we cannot use @lru_cache here: pydantic
#: Settings objects are not hashable, so caching on the argument would raise
#: TypeError. A module-level singleton does the same job.
_provider: SmsProvider | None = None


def get_sms_provider(settings: Settings | None = None) -> SmsProvider:
    """Return the configured SMS provider, creating it on first use.

    A singleton because the fake provider records every code it "sent" in a
    list, and tests read that list.
    """
    global _provider
    if _provider is None:
        settings = settings or get_settings()

        if settings.sms_provider == "fake":
            if settings.is_production:
                # Belt and braces: production must never silently swallow OTPs.
                raise RuntimeError("SMS_PROVIDER=fake is not allowed in production.")
            log.info("sms_provider_selected", provider="fake")
            _provider = FakeSmsProvider()
        else:
            raise RuntimeError(
                f"Unknown SMS_PROVIDER={settings.sms_provider!r}. Only 'fake' is "
                "implemented so far — a real vendor adapter is pending open "
                "questions Q2/Q3 in docs/PROJECT.md."
            )

    return _provider


def reset_sms_provider() -> None:
    """Clear the singleton. Used by tests to start from a clean state."""
    global _provider
    _provider = None
