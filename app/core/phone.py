"""Phone number normalisation.

The phone number IS the user's identity here, so it must be stored in exactly
one canonical format. Otherwise the same person creates several accounts just
by typing their number differently:

    9876543210        →  +919876543210
    09876543210       →  +919876543210
    +91 98765 43210   →  +919876543210
    91-9876543210     →  +919876543210

All four are the same person. We normalise to **E.164** (the international
standard: `+`, country code, digits, no spaces or punctuation) before the value
reaches the database, where the column has a unique constraint.

Scope: Indian mobile numbers only — all the MVP needs (single-country launch,
assumption A8). Supporting more countries later means adding the
`phonenumbers` library and changing only this file; the rest of the app never
sees anything but the normalised result.
"""

from __future__ import annotations

import re

#: India. Assumption A8 in docs/PROJECT.md.
COUNTRY_CODE = "91"

#: Indian mobile numbers are 10 digits and begin with 6, 7, 8 or 9.
_VALID_FIRST_DIGITS = frozenset("6789")
_NATIONAL_LENGTH = 10

_NON_DIGITS = re.compile(r"\D")


class InvalidPhoneNumberError(ValueError):
    """Raised when a value cannot be a valid Indian mobile number."""


def normalise_phone(raw: str) -> str:
    """Convert user input to E.164, or raise `InvalidPhoneNumberError`.

    Accepts the number with or without a country code, with or without a
    leading zero, and with any spacing or punctuation.
    """
    if not raw or not raw.strip():
        raise InvalidPhoneNumberError("Phone number is required.")

    digits = _NON_DIGITS.sub("", raw)

    if len(digits) == _NATIONAL_LENGTH:
        national = digits
    elif len(digits) == _NATIONAL_LENGTH + 1 and digits.startswith("0"):
        national = digits[1:]  # trunk prefix, e.g. 09876543210
    elif len(digits) == _NATIONAL_LENGTH + 2 and digits.startswith(COUNTRY_CODE):
        national = digits[2:]  # with country code, e.g. 919876543210
    else:
        raise InvalidPhoneNumberError(
            "Enter a 10-digit Indian mobile number, optionally prefixed with +91."
        )

    if national[0] not in _VALID_FIRST_DIGITS:
        raise InvalidPhoneNumberError("An Indian mobile number must start with 6, 7, 8 or 9.")

    return f"+{COUNTRY_CODE}{national}"


def mask_phone(phone_e164: str) -> str:
    """Mask for display: +919876543210 -> +9198****3210.

    Used wherever a number is echoed back to a client, so the full number never
    lands in a response body, a log line, or a user's screenshot.
    """
    if len(phone_e164) < 10:
        return "***"
    return f"{phone_e164[:5]}{'*' * (len(phone_e164) - 9)}{phone_e164[-4:]}"
