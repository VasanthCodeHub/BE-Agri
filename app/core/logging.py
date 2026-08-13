"""Structured logging.

Normal logging writes sentences:

    INFO  User +919876543210 requested an OTP

Structured logging writes fields:

    event=otp_requested  phone=+9198****3210  request_id=a1b2c3

The second kind is searchable ("show me every otp_requested for this
request_id"), and it can be shipped to a log aggregator without regex
parsing. That is the whole reason we use structlog.

It also gives us one enforceable place to scrub sensitive values, so a
phone number or signing key cannot leak into a log line by accident.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.config import LogFormat, Settings

REDACTED = "***redacted***"

#: Keys whose values must never appear in a log at all.
_REDACT_KEYS = frozenset(
    {
        "password",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "api_token",
        "jwt_secret_key",
    }
)

#: Keys holding a phone number. Masked rather than removed, so log lines for
#: one user can still be correlated.
_PHONE_KEYS = frozenset({"phone", "phone_e164", "phone_number", "msisdn", "caller_id"})


def mask_phone(value: str) -> str:
    """Mask the middle of a phone number: +919876543210 -> +9198****3210."""
    if len(value) < 10:
        return "***"
    return f"{value[:5]}{'*' * (len(value) - 9)}{value[-4:]}"


def _make_scrubber(*, redact_otp: bool) -> Any:
    """Build the processor that removes/masks sensitive fields.

    `redact_otp` is False locally on purpose: the fake SMS provider logs the
    OTP so you can log in without a real SMS gateway. In production the OTP
    is always redacted.
    """

    def scrub(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        for key, value in list(event_dict.items()):
            lowered = key.lower()
            should_redact = (
                lowered in _REDACT_KEYS
                or "secret" in lowered
                or "password" in lowered
                or (lowered == "otp" and redact_otp)
            )
            if should_redact:
                event_dict[key] = REDACTED
            elif lowered in _PHONE_KEYS and isinstance(value, str):
                event_dict[key] = mask_phone(value)
        return event_dict

    return scrub


def configure_logging(settings: Settings) -> None:
    """Configure structlog once, at application startup."""
    processors: list[Any] = [
        # Pulls in context bound for this request (e.g. request_id) so every
        # log line inside one request carries it automatically.
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        _make_scrubber(redact_otp=settings.is_production),
    ]

    if settings.log_format is LogFormat.JSON:
        processors += [
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Coloured, aligned, human-readable — local development only.
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.log_level]
        ),
        logger_factory=structlog.WriteLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Uvicorn and SQLAlchemy use the standard library logger, not structlog.
    # Point them at stdout at the same level so all output is consistent.
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=settings.log_level)
    logging.getLogger("uvicorn.access").handlers = []  # our middleware logs requests


def get_logger(name: str | None = None) -> Any:
    """Return a logger. Usage: `log = get_logger(__name__)`."""
    return structlog.get_logger(name)
