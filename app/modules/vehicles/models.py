"""Vehicle listing models.

Three tables:

- `vehicle_types` — the seeded taxonomy (tractor, harvester, …). Reference data,
  never free text, so the same machine cannot be spelled three ways and split
  into three filter buckets.
- `vehicles` — one row per listing.
- `vehicle_images` — the photo URLs, ordered.

WHY VEHICLES HANG OFF `users` AND NOT A PROVIDER PROFILE
--------------------------------------------------------
`provider_profiles` does not exist yet (Phase 2). A vehicle needs an owner
today, and the owner that actually matters for authorisation is the *user
account* — that is what the access token identifies and what `require_role`
checks. When provider profiles arrive they become a 1:1 detail table hanging off
the same user, so nothing here has to move.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.users.models import User


class FuelType(StrEnum):
    DIESEL = "DIESEL"
    PETROL = "PETROL"
    ELECTRIC = "ELECTRIC"
    CNG = "CNG"
    HYBRID = "HYBRID"


class Transmission(StrEnum):
    MANUAL = "MANUAL"
    AUTOMATIC = "AUTOMATIC"
    #: Common on harvesters and some tractors, so worth naming rather than
    #: forcing owners to pick the wrong one of the other two.
    HYDROSTATIC = "HYDROSTATIC"


class PriceUnit(StrEnum):
    """How the rental price is charged (Q13 — all four are supported)."""

    HOUR = "HOUR"
    DAY = "DAY"
    ACRE = "ACRE"
    TRIP = "TRIP"


class ListingStatus(StrEnum):
    """Moderation state of a listing.

    The full state machine and admin review arrive in Phase 5. Until then new
    listings default to APPROVED, because there is no admin to approve them and
    a listing nobody can see is worse than no listing. Phase 5 changes that
    default to DRAFT — deliberately, and with the migration to match.
    """

    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUSPENDED = "SUSPENDED"


#: Postgres enum types, bound to the metadata so each is created exactly once.
fuel_type_enum = Enum(FuelType, name="fuel_type", metadata=Base.metadata)
transmission_enum = Enum(Transmission, name="transmission", metadata=Base.metadata)
price_unit_enum = Enum(PriceUnit, name="price_unit", metadata=Base.metadata)
listing_status_enum = Enum(ListingStatus, name="listing_status", metadata=Base.metadata)

#: Photo limits (Q15 — 6 photos). Enforced in the schema layer, documented here
#: because the database cannot express "a parent may have at most 6 children".
MIN_IMAGES = 1
MAX_IMAGES = 6


class VehicleType(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Seeded vehicle taxonomy.

    A table rather than a Python enum because the client will extend the list
    (Q12 is still open), the names need Tamil translations (Q14), and admins
    should be able to add a type without a deployment.
    """

    __tablename__ = "vehicle_types"

    #: Stable machine key, e.g. "TRACTOR". This is what the API accepts and
    #: returns, so translations can change without breaking clients.
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name_en: Mapped[str] = mapped_column(String(80))
    name_ta: Mapped[str | None] = mapped_column(String(80))

    #: Display order in the app's picker; lower comes first.
    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100")

    #: Retired types stay in the table so existing listings keep resolving, but
    #: disappear from the picker.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Vehicle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vehicles"
    __table_args__ = (
        # A registration number identifies one physical vehicle, so it must not
        # be listed twice. Partial index: a soft-deleted listing releases the
        # number, so an owner who deletes and re-adds their tractor is not
        # permanently locked out.
        Index(
            "uq_vehicles_registration_number_live",
            "registration_number",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # The listing feed always filters on these three together.
        Index(
            "ix_vehicles_discoverable",
            "listing_status",
            "is_available",
            "vehicle_type_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "manufacture_year BETWEEN 1950 AND 2100",
            name="manufacture_year_plausible",
        ),
        CheckConstraint("price_amount > 0", name="price_amount_positive"),
        CheckConstraint("power_hp BETWEEN 0 AND 2000", name="power_hp_plausible"),
        CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90",
            name="latitude_in_range",
        ),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name="longitude_in_range",
        ),
    )

    #: The provider who owns this listing. Authorisation compares this against
    #: the caller's user id — holding the PROVIDER role is not enough to edit
    #: someone else's vehicle.
    provider_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    #: RESTRICT, not CASCADE: deleting a type must never silently delete the
    #: listings that reference it. Retire it with is_active instead.
    vehicle_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vehicle_types.id", ondelete="RESTRICT"), index=True
    )

    #: Optional references into the master data. Nullable on purpose: listings
    #: created before master data existed keep working, and a provider is never
    #: forced to pick from the catalogue. When set, `brand`/`model` hold the
    #: canonical names from the master rows (kept denormalised so the text
    #: search and old clients keep working).
    manufacturer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vehicle_manufacturers.id", ondelete="SET NULL"), index=True
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vehicle_models.id", ondelete="SET NULL"), index=True
    )
    variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vehicle_variants.id", ondelete="SET NULL"), index=True
    )

    #: What the owner calls it, e.g. "Mahindra 575 DI".
    name: Mapped[str] = mapped_column(String(120))
    brand: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(60))
    manufacture_year: Mapped[int] = mapped_column(Integer)

    #: Normalised uppercase, no spaces — see `normalise_registration_number`.
    #: Never exposed on the public feed: an RC number can be used to look up the
    #: owner, so it is provider-facing only.
    #:
    #: No index=True here: the partial unique index in __table_args__ already
    #: covers every query we make (all of which filter deleted_at IS NULL), and a
    #: second index on the same column would be paid for on every write for
    #: nothing.
    registration_number: Mapped[str] = mapped_column(String(20))

    #: RC book details — private to the owner, never on the public feed. One
    #: model can have thousands of physical vehicles, each with its own RC, so
    #: these live here and never on the master rows.
    rc_number: Mapped[str | None] = mapped_column(String(40))
    #: Cloudinary public_id of the RC document, uploaded via the same signed
    #: direct-upload flow as photos. A reference, never the file itself.
    rc_document_public_id: Mapped[str | None] = mapped_column(String(255))
    #: Engine and chassis numbers identify the physical vehicle. Private —
    #: provider-facing only, like the RC number above.
    engine_number: Mapped[str | None] = mapped_column(String(40))
    chassis_number: Mapped[str | None] = mapped_column(String(40))

    note: Mapped[str] = mapped_column(Text)

    #: Integer minor units (paise), per the money convention in PROJECT.md
    #: §4.4. Floats are never used for money — 0.1 + 0.2 != 0.3.
    price_amount: Mapped[int] = mapped_column(Integer)
    price_unit: Mapped[PriceUnit] = mapped_column(price_unit_enum)

    #: Free text for now: "Sulur, Coimbatore". Radius search is deferred, so
    #: there is deliberately no PostGIS column yet.
    location_text: Mapped[str] = mapped_column(String(160))
    #: Optional coordinates, captured now so that enabling radius search later
    #: is a migration over existing data rather than asking every provider to
    #: re-enter their location.
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)

    fuel_type: Mapped[FuelType] = mapped_column(fuel_type_enum)
    #: Horsepower — the rating Indian farmers actually compare tractors by.
    power_hp: Mapped[int] = mapped_column(Integer)
    transmission: Mapped[Transmission] = mapped_column(transmission_enum)

    #: The owner's own switch: "rented out this week", "in for repair".
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    listing_status: Mapped[ListingStatus] = mapped_column(
        listing_status_enum,
        default=ListingStatus.APPROVED,
        server_default=ListingStatus.APPROVED.value,
    )

    #: Soft delete. Calls and (later) bookings reference vehicles, so a hard
    #: delete would tear holes in history.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: selectin, not the lazy default: touching a lazy relationship while
    #: serialising a response raises MissingGreenlet in async SQLAlchemy.
    images: Mapped[list[VehicleImage]] = relationship(
        back_populates="vehicle",
        cascade="all, delete-orphan",
        order_by="VehicleImage.sort_order",
        lazy="selectin",
    )
    vehicle_type: Mapped[VehicleType] = relationship(lazy="selectin")
    provider: Mapped[User] = relationship(lazy="selectin")

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class VehicleImage(UUIDPrimaryKeyMixin, Base):
    """A photo attached to a listing.

    Stores Cloudinary's `public_id` — the asset's path inside the account — and
    NOT a delivery URL. Three reasons:

    - **Sizes.** One id serves any dimension, so the feed can request 400px
      thumbnails while the detail screen gets full size. Storing one fixed URL
      would force every list card to download a full-resolution photo, which on a
      rural connection is the difference between a usable app and an unusable one.
    - **Verification.** An id inside our own folder is provably ours; an
      arbitrary URL could point anywhere on the internet.
    - **Portability.** Moving off Cloudinary changes one URL-building function
      rather than every stored row.
    """

    __tablename__ = "vehicle_images"
    __table_args__ = (
        # Two photos cannot claim the same position.
        UniqueConstraint("vehicle_id", "sort_order"),
    )

    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vehicles.id", ondelete="CASCADE"), index=True
    )
    #: e.g. "agri/vehicles/9f8e7d6c5b4a3928".
    public_id: Mapped[str] = mapped_column(String(255))
    #: 0-based. The first image is the card thumbnail.
    sort_order: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    vehicle: Mapped[Vehicle] = relationship(back_populates="images")
