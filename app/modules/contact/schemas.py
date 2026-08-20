"""Request and response shapes for the contact/call flow."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ContactCallIn(BaseModel):
    """Body for POST /contact/call — the user wants to reach the provider."""

    vehicle_id: uuid.UUID = Field(..., description="The listing the user is calling about.")

    model_config = ConfigDict(
        json_schema_extra={"example": {"vehicle_id": "3f2a4b6c-1111-4000-8000-000000000001"}}
    )


class ContactCallOut(BaseModel):
    """What the app needs to open the dialer.

    The provider's phone is returned ONLY here — never on the public feed —
    and only to an authenticated caller who has actually initiated contact.
    The call is recorded on the server and the provider is notified.
    """

    call_id: uuid.UUID
    provider_id: uuid.UUID
    provider_name: str | None
    provider_phone: str = Field(description="E.164 number for the dialer.")
    vehicle_id: uuid.UUID
    created_at: datetime
    message: str = Field(description="Human-readable confirmation; do not branch logic on it.")
