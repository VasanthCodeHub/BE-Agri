"""Request and response shapes for vehicle listings.

Two output shapes on purpose, and the difference is a security boundary:

- `VehicleOut` — the **owner's** view. Everything, including the registration
  number and moderation status.
- `VehicleCardOut` — the **public** view. No registration number, no provider
  phone number, no exact coordinates.

Keeping them as separate classes means a renter-facing endpoint cannot leak an
owner-only field by accident; it would have to be added here deliberately
(ADR-009).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.vehicles.models import (
    MAX_IMAGES,
    MIN_IMAGES,
    FuelType,
    ListingStatus,
    PriceUnit,
    Transmission,
    Vehicle,
    VehicleType,
)
from app.modules.vehicles.registration import (
    InvalidRegistrationNumberError,
    normalise_registration_number,
)

#: Only https. A stored `javascript:` or `data:` URL becomes an attack the moment
#: some client renders it in a webview, and plain http images break under the
#: app's transport security rules anyway.
_ALLOWED_URL_SCHEMES = frozenset({"https"})

_MAX_PRICE_PAISE = 100_000_000  # ₹10,00,000 — a sanity ceiling, not a business rule


def _validate_image_url(value: str) -> str:
    url = value.strip()
    if not url:
        raise ValueError("Image URL cannot be empty.")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES:
        raise ValueError(f"Image URLs must start with https:// (got {url[:40]!r}).")
    if not parsed.netloc:
        raise ValueError(f"Image URL is not a valid address ({url[:40]!r}).")
    return url


# ---------------------------------------------------------------------------
# Vehicle types (the seeded taxonomy)
# ---------------------------------------------------------------------------
class VehicleTypeOut(BaseModel):
    """A row from the seeded taxonomy, as the app's picker needs it."""

    id: uuid.UUID
    code: str = Field(description="Stable machine key — send this when creating a vehicle.")
    name_en: str
    name_ta: str | None

    @classmethod
    def from_model(cls, vehicle_type: VehicleType) -> VehicleTypeOut:
        return cls(
            id=vehicle_type.id,
            code=vehicle_type.code,
            name_en=vehicle_type.name_en,
            name_ta=vehicle_type.name_ta,
        )


# ---------------------------------------------------------------------------
# Creating a listing
# ---------------------------------------------------------------------------
class VehicleCreateIn(BaseModel):
    """Body for POST /provider/vehicles. Every field is required."""

    name: str = Field(
        ...,
        min_length=2,
        max_length=120,
        description="What the owner calls it.",
        examples=["Mahindra 575 DI"],
    )
    vehicle_type_code: str = Field(
        ...,
        description="A `code` from GET /vehicle-types.",
        examples=["TRACTOR"],
    )
    brand: str = Field(..., min_length=1, max_length=60, examples=["Mahindra"])
    model: str = Field(..., min_length=1, max_length=60, examples=["575 DI"])
    manufacture_year: int = Field(..., ge=1950, le=2100, examples=[2019])
    registration_number: str = Field(
        ...,
        description="Any format — normalised to TN38AB1234. Must not already be listed.",
        examples=["TN 38 AB 1234"],
    )
    note: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Free text for the renter: condition, implements included, terms.",
        examples=["Well maintained. Rotavator and cultivator included."],
    )
    price_amount: int = Field(
        ...,
        gt=0,
        le=_MAX_PRICE_PAISE,
        description="Rental price in **paise** (integer minor units). ₹500 = 50000.",
        examples=[50000],
    )
    price_unit: PriceUnit = Field(..., description="What the price is per.", examples=["HOUR"])
    location_text: str = Field(
        ...,
        min_length=2,
        max_length=160,
        description="Where the vehicle is based.",
        examples=["Sulur, Coimbatore"],
    )
    fuel_type: FuelType = Field(..., examples=["DIESEL"])
    power_hp: int = Field(..., ge=1, le=2000, description="Engine power in HP.", examples=[47])
    transmission: Transmission = Field(..., examples=["MANUAL"])
    image_urls: Annotated[list[str], Field(min_length=MIN_IMAGES, max_length=MAX_IMAGES)] = Field(
        ...,
        description=(
            f"{MIN_IMAGES}-{MAX_IMAGES} https image URLs. The first is the card thumbnail."
        ),
        examples=[["https://cdn.example.com/tractor-1.jpg"]],
    )

    #: Optional, and the only pair that is: radius search is not built yet. Send
    #: them if the app has GPS, so search works over existing listings later
    #: without asking every owner to re-enter their location.
    latitude: float | None = Field(default=None, ge=-90, le=90, examples=[11.0246])
    longitude: float | None = Field(default=None, ge=-180, le=180, examples=[77.1252])

    @field_validator("registration_number")
    @classmethod
    def _normalise_registration(cls, value: str) -> str:
        try:
            return normalise_registration_number(value)
        except InvalidRegistrationNumberError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("vehicle_type_code")
    @classmethod
    def _upper_code(cls, value: str) -> str:
        code = value.strip().upper()
        if not code:
            raise ValueError("vehicle_type_code is required.")
        return code

    @field_validator("name", "brand", "model", "location_text", "note")
    @classmethod
    def _collapse_whitespace(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("This field cannot be blank.")
        return cleaned

    @field_validator("image_urls")
    @classmethod
    def _check_urls(cls, value: list[str]) -> list[str]:
        urls = [_validate_image_url(url) for url in value]
        if len(set(urls)) != len(urls):
            raise ValueError("The same image URL is listed more than once.")
        return urls


# ---------------------------------------------------------------------------
# Editing a listing
# ---------------------------------------------------------------------------
class VehicleUpdateIn(BaseModel):
    """Body for PATCH /provider/vehicles/{id}. Every field is optional.

    A **partial** update: only the fields present in the JSON are changed, which
    is what lets an edit screen send one field without having to resend the
    other twelve. `exclude_unset` in the service is what makes that work — and
    it is also why `latitude: null` clears the coordinate while omitting
    `latitude` leaves it alone. Those two must not mean the same thing.

    `registration_number` is deliberately absent. Changing it would not be
    editing this listing, it would be pointing the listing at a different
    physical vehicle — delete and re-list instead.
    """

    name: str | None = Field(default=None, min_length=2, max_length=120)
    vehicle_type_code: str | None = Field(default=None, examples=["HARVESTER"])
    brand: str | None = Field(default=None, min_length=1, max_length=60)
    model: str | None = Field(default=None, min_length=1, max_length=60)
    manufacture_year: int | None = Field(default=None, ge=1950, le=2100)
    note: str | None = Field(default=None, min_length=1, max_length=1000)
    price_amount: int | None = Field(
        default=None, gt=0, le=_MAX_PRICE_PAISE, description="In paise. ₹500 = 50000."
    )
    price_unit: PriceUnit | None = None
    location_text: str | None = Field(default=None, min_length=2, max_length=160)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    fuel_type: FuelType | None = None
    power_hp: int | None = Field(default=None, ge=1, le=2000)
    transmission: Transmission | None = None
    #: Also settable via PATCH …/availability, which is the one-tap shortcut for
    #: the listing card. Both write this same column.
    is_available: bool | None = None
    #: Sent as a whole set: the new list **replaces** every existing photo, so
    #: reordering is just sending them in a different order.
    image_urls: Annotated[
        list[str] | None, Field(default=None, min_length=MIN_IMAGES, max_length=MAX_IMAGES)
    ] = None

    @field_validator("vehicle_type_code")
    @classmethod
    def _upper_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = value.strip().upper()
        if not code:
            raise ValueError("vehicle_type_code cannot be blank.")
        return code

    @field_validator("name", "brand", "model", "location_text", "note")
    @classmethod
    def _collapse_whitespace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("This field cannot be blank.")
        return cleaned

    @field_validator("image_urls")
    @classmethod
    def _check_urls(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        urls = [_validate_image_url(url) for url in value]
        if len(set(urls)) != len(urls):
            raise ValueError("The same image URL is listed more than once.")
        return urls


# ---------------------------------------------------------------------------
# Output — owner's view
# ---------------------------------------------------------------------------
class VehicleOut(BaseModel):
    """A listing as its owner sees it. Includes owner-only fields."""

    id: uuid.UUID
    name: str
    vehicle_type: VehicleTypeOut
    brand: str
    model: str
    manufacture_year: int
    registration_number: str = Field(description="Owner-only — never on the public feed.")
    note: str
    price_amount: int = Field(description="In paise.")
    price_unit: PriceUnit
    price_label: str = Field(description="Ready to display, e.g. '₹500 / hour'.")
    location_text: str
    latitude: float | None
    longitude: float | None
    fuel_type: FuelType
    power_hp: int
    transmission: Transmission
    is_available: bool
    listing_status: ListingStatus
    image_urls: list[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, vehicle: Vehicle) -> VehicleOut:
        return cls(
            id=vehicle.id,
            name=vehicle.name,
            vehicle_type=VehicleTypeOut.from_model(vehicle.vehicle_type),
            brand=vehicle.brand,
            model=vehicle.model,
            manufacture_year=vehicle.manufacture_year,
            registration_number=vehicle.registration_number,
            note=vehicle.note,
            price_amount=vehicle.price_amount,
            price_unit=vehicle.price_unit,
            price_label=price_label(vehicle.price_amount, vehicle.price_unit),
            location_text=vehicle.location_text,
            latitude=vehicle.latitude,
            longitude=vehicle.longitude,
            fuel_type=vehicle.fuel_type,
            power_hp=vehicle.power_hp,
            transmission=vehicle.transmission,
            is_available=vehicle.is_available,
            listing_status=vehicle.listing_status,
            image_urls=[image.url for image in vehicle.images],
            created_at=vehicle.created_at,
            updated_at=vehicle.updated_at,
        )


# ---------------------------------------------------------------------------
# Output — public view
# ---------------------------------------------------------------------------
class VehicleCardOut(BaseModel):
    """A listing as a renter sees it.

    Deliberately absent, and each omission is a decision:

    - **provider phone number** — the whole point of the masked-calling feature
      (ADR-009). Contact happens through `/calls/initiate` in Phase 7.
    - **registration number** — an RC number can be used to look up the
      registered owner. Renters do not need it to choose a tractor.
    - **exact coordinates** — a precise home location for every provider is not
      something a public feed should hand out.
    - **listing_status** — internal moderation state.
    """

    id: uuid.UUID
    name: str
    vehicle_type: VehicleTypeOut
    brand: str
    model: str
    manufacture_year: int
    note: str
    price_amount: int = Field(description="In paise.")
    price_unit: PriceUnit
    price_label: str
    location_text: str
    fuel_type: FuelType
    power_hp: int
    transmission: Transmission
    image_urls: list[str]
    provider_id: uuid.UUID = Field(description="Use this to initiate a call (Phase 7).")
    provider_name: str | None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "3f2a…",
                "name": "Mahindra 575 DI",
                "vehicle_type": {"code": "TRACTOR", "name_en": "Tractor", "name_ta": "டிராக்டர்"},
                "brand": "Mahindra",
                "model": "575 DI",
                "manufacture_year": 2019,
                "note": "Well maintained.",
                "price_amount": 50000,
                "price_unit": "HOUR",
                "price_label": "₹500 / hour",
                "location_text": "Sulur, Coimbatore",
                "fuel_type": "DIESEL",
                "power_hp": 47,
                "transmission": "MANUAL",
                "image_urls": ["https://cdn.example.com/tractor-1.jpg"],
                "provider_name": "Ravi Kumar",
            }
        }
    )

    @classmethod
    def from_model(cls, vehicle: Vehicle) -> VehicleCardOut:
        return cls(
            id=vehicle.id,
            name=vehicle.name,
            vehicle_type=VehicleTypeOut.from_model(vehicle.vehicle_type),
            brand=vehicle.brand,
            model=vehicle.model,
            manufacture_year=vehicle.manufacture_year,
            note=vehicle.note,
            price_amount=vehicle.price_amount,
            price_unit=vehicle.price_unit,
            price_label=price_label(vehicle.price_amount, vehicle.price_unit),
            location_text=vehicle.location_text,
            fuel_type=vehicle.fuel_type,
            power_hp=vehicle.power_hp,
            transmission=vehicle.transmission,
            image_urls=[image.url for image in vehicle.images],
            provider_id=vehicle.provider_user_id,
            provider_name=vehicle.provider.full_name if vehicle.provider else None,
        )


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
class VehiclePage(BaseModel):
    """A page of owner-view listings."""

    items: list[VehicleOut]
    total: int = Field(description="Total matching listings, ignoring pagination.")
    limit: int
    offset: int


class VehicleCardPage(BaseModel):
    """A page of public listing cards."""

    items: list[VehicleCardOut]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_UNIT_LABELS = {
    PriceUnit.HOUR: "hour",
    PriceUnit.DAY: "day",
    PriceUnit.ACRE: "acre",
    PriceUnit.TRIP: "trip",
}


def price_label(amount_paise: int, unit: PriceUnit) -> str:
    """Render paise as a rupee string: 50000, HOUR -> '₹500 / hour'.

    Done server-side so every client shows money identically, and so the
    rounding rule lives in one place.
    """
    rupees = amount_paise / 100
    formatted = f"{rupees:,.0f}" if rupees == int(rupees) else f"{rupees:,.2f}"
    return f"₹{formatted} / {_UNIT_LABELS[unit]}"
