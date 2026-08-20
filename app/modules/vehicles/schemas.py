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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import Settings
from app.integrations.cloudinary import (
    THUMB_WIDTH,
    build_url,
    is_well_formed_public_id,
)
from app.modules.vehicles.models import (
    MAX_IMAGES,
    MIN_IMAGES,
    FuelType,
    ListingStatus,
    PriceUnit,
    Transmission,
    Vehicle,
    VehicleImage,
    VehicleType,
)
from app.modules.vehicles.registration import (
    InvalidRegistrationNumberError,
    normalise_registration_number,
)

_MAX_PRICE_PAISE = 100_000_000


def _validate_public_id(value: str) -> str:
    public_id = value.strip()
    if not public_id:
        raise ValueError("public_id cannot be empty.")
    if "://" in public_id or public_id.lower().startswith("http"):
        raise ValueError(
            "Send Cloudinary's public_id, not a URL — "
            f"e.g. 'agri/vehicles/9f8e7d6c' (got {public_id[:48]!r})."
        )
    if not is_well_formed_public_id(public_id):
        raise ValueError(
            f"{public_id[:48]!r} is not a valid public_id. Use the value Cloudinary "
            "returned from the upload."
        )
    return public_id


# ---------------------------------------------------------------------------
# Vehicle types
# ---------------------------------------------------------------------------
class VehicleTypeOut(BaseModel):
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
# Search query params
# ---------------------------------------------------------------------------
class VehicleSearchParams(BaseModel):
    """All possible filters for GET /vehicles. The router instantiates this
    from query parameters."""

    type_code: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float | None = Field(default=None, gt=0, le=500)
    q: str | None = Field(default=None, max_length=100)
    max_price: int | None = Field(default=None, gt=0, le=_MAX_PRICE_PAISE)
    available_only: bool = True
    sort: str = Field(default="newest", pattern="^(newest|distance|price_asc|price_desc)$")
    limit: int = 20
    offset: int = 0


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
    power_hp: int = Field(
        ...,
        ge=0,
        le=2000,
        description=(
            "Engine power in HP. 0 is valid for non-motorised implements "
            "(rotavators, trailers, ploughs)."
        ),
        examples=[47],
    )
    transmission: Transmission = Field(..., examples=["MANUAL"])
    image_public_ids: list[str] = Field(
        default_factory=list,
        min_length=0,
        max_length=MAX_IMAGES,
        description=(
            f"0-{MAX_IMAGES} Cloudinary `public_id` values. The first is the card "
            "thumbnail. Send ids, not URLs. Empty list allowed — edit later using "
            "PATCH /provider/vehicles/{{id}} with image_public_ids."
        ),
        examples=[["agri/vehicles/9f8e7d6c5b4a3928"], []],
    )
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

    @field_validator("image_public_ids")
    @classmethod
    def _check_public_ids(cls, value: list[str]) -> list[str]:
        ids = [_validate_public_id(public_id) for public_id in value]
        if len(set(ids)) != len(ids):
            raise ValueError("The same image is listed more than once.")
        return ids


# ---------------------------------------------------------------------------
# Editing a listing
# ---------------------------------------------------------------------------
class VehicleUpdateIn(BaseModel):
    """Body for PATCH /provider/vehicles/{id}. Every field is optional."""

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
    power_hp: int | None = Field(default=None, ge=0, le=2000)
    transmission: Transmission | None = None
    is_available: bool | None = None
    image_public_ids: list[str] | None = Field(
        default=None,
        min_length=0,
        max_length=MAX_IMAGES,
        description="Replace all photos. [] to clear.",
    )

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

    @field_validator("image_public_ids")
    @classmethod
    def _check_public_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [_validate_public_id(pid) for pid in value]


# ---------------------------------------------------------------------------
# Output — images
# ---------------------------------------------------------------------------
class VehicleImageOut(BaseModel):
    """One photo, at the two sizes a client actually needs."""

    public_id: str
    url: str | None = Field(description="Full size. Null if Cloudinary is not configured.")
    thumb_url: str | None = Field(
        description=f"{THUMB_WIDTH}px square-cropped — use this in lists to save bandwidth."
    )

    @classmethod
    def from_model(cls, image: VehicleImage, *, settings: Settings) -> VehicleImageOut:
        return cls(
            public_id=image.public_id,
            url=build_url(image.public_id, settings),
            thumb_url=build_url(image.public_id, settings, width=THUMB_WIDTH),
        )


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
    images: list[VehicleImageOut]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, vehicle: Vehicle, *, settings: Settings) -> VehicleOut:
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
            images=[
                VehicleImageOut.from_model(image, settings=settings) for image in vehicle.images
            ],
            created_at=vehicle.created_at,
            updated_at=vehicle.updated_at,
        )


# ---------------------------------------------------------------------------
# Output — public view
# ---------------------------------------------------------------------------
class VehicleCardOut(BaseModel):
    """A listing as a renter sees it."""

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
    images: list[VehicleImageOut]
    provider_id: uuid.UUID = Field(description="Use this to initiate a call.")
    provider_name: str | None
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int = Field(default=0, ge=0)
    distance_km: float | None = Field(default=None, ge=0)

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
                "images": [
                    {
                        "public_id": "agri/vehicles/9f8e7d6c",
                        "url": "https://res.cloudinary.com/your-cloud/image/upload/q_auto,f_auto/agri/vehicles/9f8e7d6c",
                        "thumb_url": "https://res.cloudinary.com/your-cloud/image/upload/w_400,c_fill,q_auto,f_auto/agri/vehicles/9f8e7d6c",
                    }
                ],
                "provider_id": "abc...",
                "provider_name": "Ravi Kumar",
                "rating": 4.2,
                "review_count": 3,
                "distance_km": 12.5,
            }
        }
    )

    @classmethod
    def from_model(
        cls,
        vehicle: Vehicle,
        *,
        settings: Settings,
        distance_km: float | None = None,
        rating: float | None = None,
        review_count: int = 0,
    ) -> VehicleCardOut:
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
            images=[
                VehicleImageOut.from_model(image, settings=settings) for image in vehicle.images
            ],
            provider_id=vehicle.provider_user_id,
            provider_name=vehicle.provider.full_name if vehicle.provider else None,
            rating=rating,
            review_count=review_count,
            distance_km=distance_km,
        )


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------
class VehiclePage(BaseModel):
    items: list[VehicleOut]
    total: int = Field(description="Total matching listings, ignoring pagination.")
    limit: int
    offset: int


class VehicleCardPage(BaseModel):
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
    rupees = amount_paise / 100
    formatted = f"{rupees:,.0f}" if rupees == int(rupees) else f"{rupees:,.2f}"
    return f"₹{formatted} / {_UNIT_LABELS[unit]}"