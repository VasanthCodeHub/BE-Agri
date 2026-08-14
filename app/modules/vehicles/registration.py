"""Vehicle registration number normalisation.

Same problem as phone numbers (`app/core/phone.py`): one physical vehicle can be
typed a dozen ways, and the database's uniqueness guarantee is only as good as
the normalisation in front of it.

    TN 38 AB 1234   ─┐
    tn38ab1234       ├─▶  TN38AB1234
    TN-38-AB-1234   ─┘

Two formats are accepted, because both are on Indian roads today:

  - **State series** — `TN38AB1234`: state code, RTO district number, series
    letters, up to 4 digits.
  - **BH (Bharat) series** — `22BH1234AA`: year, literal BH, 4 digits, letters.
    Introduced in 2021 for vehicles that move between states; rejecting it would
    turn away exactly the kind of owner who works across district borders.
"""

from __future__ import annotations

import re

_NOT_ALNUM = re.compile(r"[^A-Z0-9]")

#: TN38AB1234, KA05MH1234, TN38A1234, DL1CAB1234
_STATE_SERIES = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{1,4}$")
#: 22BH1234AA
_BH_SERIES = re.compile(r"^\d{2}BH\d{4}[A-Z]{1,2}$")


class InvalidRegistrationNumberError(ValueError):
    """Raised when a value cannot be an Indian registration number."""


def normalise_registration_number(raw: str) -> str:
    """Uppercase, strip punctuation and validate, or raise.

    Returns the canonical form that goes in the database, which is what the
    partial unique index on live listings compares.
    """
    if not raw or not raw.strip():
        raise InvalidRegistrationNumberError("Registration number is required.")

    cleaned = _NOT_ALNUM.sub("", raw.upper())

    if not cleaned:
        raise InvalidRegistrationNumberError("Registration number is required.")

    if _STATE_SERIES.match(cleaned) or _BH_SERIES.match(cleaned):
        return cleaned

    raise InvalidRegistrationNumberError(
        "Enter a valid registration number, for example TN38AB1234 or 22BH1234AA."
    )
