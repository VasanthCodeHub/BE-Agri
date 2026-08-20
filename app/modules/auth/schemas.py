"""Request and response shapes for authentication.

These Pydantic models are the API contract. They validate every incoming
field, and they generate the examples and field docs you see in Swagger at
/docs — so the effort spent here pays back twice.

Note they are deliberately separate from the ORM models in `models.py`. The
database row has a `code_hash`, a full phone number and internal timestamps;
none of that belongs in a response. Keeping the two apart is what makes
accidental leakage structurally impossible rather than a matter of remembering.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.phone import InvalidPhoneNumberError, mask_phone, normalise_phone
from app.modules.users.models import User, UserRole


# ---------------------------------------------------------------------------
# Step 1 — request an OTP
# ---------------------------------------------------------------------------
class OtpRequestIn(BaseModel):
    """Body for POST /auth/otp/request."""

    phone: str = Field(
        ...,
        description="Indian mobile number. Accepts 9876543210, 09876543210, "
        "+919876543210 or 91 98765 43210.",
        examples=["9876543210"],
    )
    # Required, with no default: omitting it produces a 422 telling the caller
    # to choose a role. When ADMIN is added to UserRole (Phase 5) this MUST
    # become a narrower enum — otherwise a caller could self-assign ADMIN
    # through a public endpoint.
    role: UserRole = Field(
        ...,
        description="Which experience to log in to. Choose RENTER or PROVIDER.",
        examples=["RENTER"],
    )

    @field_validator("phone")
    @classmethod
    def _normalise(cls, value: str) -> str:
        """Convert to E.164 at the edge, so the rest of the app sees one format."""
        try:
            return normalise_phone(value)
        except InvalidPhoneNumberError as exc:
            raise ValueError(str(exc)) from exc


class OtpRequestOut(BaseModel):
    """Response for POST /auth/otp/request."""

    phone: str = Field(description="Masked, for display back to the user.")
    is_new_user: bool = Field(description="True if this number has never logged in.")
    name_required: bool = Field(
        description="True when the app should show the name field before verifying."
    )
    otp_sent: bool
    expires_in: int = Field(description="Seconds until the code expires.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "phone": "+9198****3210",
                "is_new_user": True,
                "name_required": True,
                "otp_sent": True,
                "expires_in": 300,
            }
        }
    )


# ---------------------------------------------------------------------------
# Step 2 — verify the OTP
# ---------------------------------------------------------------------------
class OtpVerifyIn(BaseModel):
    """Body for POST /auth/otp/verify."""

    phone: str = Field(..., examples=["9876543210"])
    code: str = Field(
        ...,
        description="The 4-digit code from the SMS. In local development it is "
        "printed in the server terminal, and the dev bypass code (`0000`) works "
        "for any number.",
        examples=["0000"],
    )
    name: str | None = Field(
        default=None,
        max_length=120,
        description="Required only for a new user (when name_required was true).",
        examples=["Vasanth"],
    )
    email: str | None = Field(
        default=None,
        max_length=254,
        description="Optional, collected at registration.",
        examples=["vasanth@example.com"],
    )
    address: str | None = Field(
        default=None,
        max_length=255,
        description="Optional, collected at registration.",
        examples=["12 Gandhi Street, Sulur, Coimbatore"],
    )
    latitude: float | None = Field(default=None, ge=-90, le=90, examples=[11.0246])
    longitude: float | None = Field(default=None, ge=-180, le=180, examples=[77.1252])

    @field_validator("phone")
    @classmethod
    def _normalise(cls, value: str) -> str:
        try:
            return normalise_phone(value)
        except InvalidPhoneNumberError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("code")
    @classmethod
    def _check_code(cls, value: str) -> str:
        code = value.strip()
        if not code.isdigit():
            raise ValueError("The code must contain digits only.")
        if not 4 <= len(code) <= 8:
            raise ValueError("The code must be between 4 and 8 digits.")
        return code

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        name = " ".join(value.split())  # collapse runs of whitespace
        return name or None


# ---------------------------------------------------------------------------
# The user, as the API exposes them
# ---------------------------------------------------------------------------
class UserOut(BaseModel):
    """A user, safe to return to that user.

    The phone number is masked even for its owner: the app already knows the
    number it typed, so returning it in full only creates another place it can
    leak — a log, a crash report, a screenshot.
    """

    id: uuid.UUID
    phone: str
    full_name: str | None
    roles: list[str] = Field(description="Every role this user holds.")
    active_role: str = Field(description="The role they logged in as.")
    profile_complete: bool = Field(
        description="False when the app should send the user to complete their profile."
    )

    @classmethod
    def from_user(cls, user: User, *, active_role: UserRole) -> UserOut:
        return cls(
            id=user.id,
            phone=mask_phone(user.phone_e164),
            full_name=user.full_name,
            roles=user.roles,
            active_role=active_role.value,
            # For now, "complete" just means we have a name. Phase 2 extends
            # this to the renter/provider profile records.
            profile_complete=bool(user.full_name),
        )


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
class TokenPairOut(BaseModel):
    """The session. Returned by verify and by refresh."""

    access_token: str = Field(description="Send as `Authorization: Bearer <token>`.")
    refresh_token: str = Field(
        description="Store securely. Use only against /auth/refresh when the access token expires."
    )
    token_type: str = "bearer"  # noqa: S105 - the OAuth token type, not a secret
    expires_in: int = Field(description="Seconds until the ACCESS token expires.")
    refresh_expires_in: int = Field(description="Seconds until the REFRESH token expires.")


class LoginOut(TokenPairOut):
    """Response for POST /auth/otp/verify — tokens plus who you are."""

    is_new_user: bool
    user: UserOut


class RefreshIn(BaseModel):
    refresh_token: str = Field(..., min_length=20)


class LogoutIn(BaseModel):
    refresh_token: str = Field(..., min_length=20)
    all_devices: bool = Field(
        default=False,
        description="True revokes every session for this user, not just this one.",
    )
