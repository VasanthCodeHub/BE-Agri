"""Vehicle master data models.

Three levels, one table each:

- `vehicle_manufacturers` — "Mahindra", "John Deere", ...
- `vehicle_models` — "575 DI", "5050D", ... One model belongs to exactly one
  manufacturer and one vehicle type, and carries the fuel and power the type
  implies, so the app cannot offer "575 DI" as a harvester.
- `vehicle_variants` — trim levels / years within a model ("Standard 2019",
  "Power Plus 2022"). Optional: a model with no variants is perfectly valid.

Why tables rather than code: the data is owned by the business (the client can
add manufacturers without a deployment), the app must never hard-code it, and
the seed script stays idempotent via the unique constraints below.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.vehicles.models import FuelType, VehicleType, fuel_type_enum


class VehicleManufacturer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vehicle_manufacturers"

    name: Mapped[str] = mapped_column(String(80), unique=True)

    #: Lower comes first in the app's picker.
    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    models: Mapped[list[VehicleModel]] = relationship(
        back_populates="manufacturer",
        cascade="all, delete-orphan",
        order_by="VehicleModel.name",
        lazy="selectin",
    )


class VehicleModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vehicle_models"
    __table_args__ = (
        # The database, not a check in code, stops the same model name being
        # added twice under one manufacturer. This is also what makes the seed
        # script idempotent.
        UniqueConstraint("manufacturer_id", "name", name="uq_vehicle_models_manufacturer_name"),
    )

    manufacturer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vehicle_manufacturers.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(80))

    #: The vehicle type this model belongs to. A model is a *tractor* model or a
    #: *harvester* model — never both, which is what lets the app cascade the
    #: pickers safely.
    vehicle_type_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vehicle_types.id", ondelete="RESTRICT"), index=True
    )

    #: Typical values, offered to the provider as sensible defaults. The listing
    #: itself keeps its own fuel_type / power_hp, because a specific machine can
    #: differ from the model's brochure figures.
    fuel_type: Mapped[FuelType] = mapped_column(fuel_type_enum)
    power_hp: Mapped[int] = mapped_column(Integer)

    sort_order: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    manufacturer: Mapped[VehicleManufacturer] = relationship(
        back_populates="models", lazy="selectin"
    )
    vehicle_type: Mapped[VehicleType] = relationship(lazy="selectin")
    variants: Mapped[list[VehicleVariant]] = relationship(
        back_populates="model",
        cascade="all, delete-orphan",
        order_by="VehicleVariant.name",
        lazy="selectin",
    )


class VehicleVariant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vehicle_variants"
    __table_args__ = (UniqueConstraint("model_id", "name", name="uq_vehicle_variants_model_name"),)

    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vehicle_models.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80))

    #: Optional: the year (or year range label) this variant refers to. Null
    #: means "any year" — the listing's own manufacture_year is always what is
    #: stored against the vehicle.
    manufacture_year: Mapped[int | None] = mapped_column(Integer)
    power_hp: Mapped[int | None] = mapped_column(Integer)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    model: Mapped[VehicleModel] = relationship(back_populates="variants", lazy="selectin")
