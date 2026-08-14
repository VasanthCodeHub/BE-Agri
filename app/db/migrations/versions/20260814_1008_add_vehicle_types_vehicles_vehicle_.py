"""add vehicle types, vehicles, vehicle images

Revision ID: 4d996dee3993
Revises: 73d129f5ac63
Created: 2026-08-14 10:08:05.714427+00:00

HAND-CORRECTED after autogenerate, for the same three reasons as the users
migration — worth re-reading if you ever wonder why generated migrations are
reviewed rather than trusted:

1. Autogenerate emitted `sa.Enum(..., metadata=MetaData())` without importing
   `MetaData`. That is an immediate NameError on execution.
2. Each enum type is now created ONCE up front and referenced with
   `create_type=False`, so PostgreSQL is not asked to CREATE TYPE twice.
3. `downgrade()` now drops the enum types too. Without that, downgrading and
   upgrading again fails with "type already exists".

It also SEEDS `vehicle_types`. The seed lives in the migration rather than a
separate script because `vehicles.vehicle_type_id` is a NOT NULL foreign key —
an empty taxonomy means no listing can be created at all, so the data is part of
the schema being correct, not an optional extra.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "4d996dee3993"
down_revision: str | None = "73d129f5ac63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Declared once, shared by every table that uses them.
fuel_type = postgresql.ENUM(
    "DIESEL", "PETROL", "ELECTRIC", "CNG", "HYBRID", name="fuel_type", create_type=False
)
transmission = postgresql.ENUM(
    "MANUAL", "AUTOMATIC", "HYDROSTATIC", name="transmission", create_type=False
)
price_unit = postgresql.ENUM("HOUR", "DAY", "ACRE", "TRIP", name="price_unit", create_type=False)
listing_status = postgresql.ENUM(
    "DRAFT",
    "PENDING_REVIEW",
    "APPROVED",
    "REJECTED",
    "SUSPENDED",
    name="listing_status",
    create_type=False,
)

#: Provisional taxonomy. Q12 (the client's definitive list) is still open, which
#: is exactly why this is a table and not a Python enum — adding or retiring a
#: type is a data change, not a deployment.
#:
#: The Tamil names need review by a Tamil speaker before launch (Q14). Getting
#: one wrong is harmless to clients: the API accepts and returns `code`, so a
#: translation can be corrected without breaking a single app build.
_SEED_TYPES = [
    # (code, name_en, name_ta, sort_order)
    ("TRACTOR", "Tractor", "டிராக்டர்", 10),
    ("POWER_TILLER", "Power tiller", "பவர் டில்லர்", 20),
    ("HARVESTER", "Harvester", "அறுவடை இயந்திரம்", 30),
    ("ROTAVATOR", "Rotavator", "ரோட்டவேட்டர்", 40),
    ("PLOUGH", "Plough", "கலப்பை", 50),
    ("SEED_DRILL", "Seed drill", "விதைப்பு இயந்திரம்", 60),
    ("SPRAYER", "Sprayer", "தெளிப்பான்", 70),
    ("THRESHER", "Thresher", "கதிரடிக்கும் இயந்திரம்", 80),
    ("BALER", "Baler", None, 90),
    ("LEVELLER", "Land leveller", None, 100),
    ("TRAILER", "Trailer", "டிரெயிலர்", 110),
    ("WATER_TANKER", "Water tanker", None, 120),
]


def upgrade() -> None:
    bind = op.get_bind()

    # checkfirst=True makes this safe to re-run.
    fuel_type.create(bind, checkfirst=True)
    transmission.create(bind, checkfirst=True)
    price_unit.create(bind, checkfirst=True)
    listing_status.create(bind, checkfirst=True)

    # --- vehicle_types -----------------------------------------------------
    vehicle_types = op.create_table(
        "vehicle_types",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name_en", sa.String(length=80), nullable=False),
        sa.Column("name_ta", sa.String(length=80), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="100", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicle_types")),
    )
    op.create_index(op.f("ix_vehicle_types_code"), "vehicle_types", ["code"], unique=True)

    # UUIDs generated in Python, not by gen_random_uuid(): that function needs
    # the pgcrypto extension, and bulk_insert binds values as parameters rather
    # than inlining SQL expressions anyway. The ids differ per environment,
    # which is fine — everything references a type by its `code`.
    op.bulk_insert(
        vehicle_types,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "name_en": name_en,
                "name_ta": name_ta,
                "sort_order": sort_order,
            }
            for code, name_en, name_ta, sort_order in _SEED_TYPES
        ],
    )

    # --- vehicles ----------------------------------------------------------
    op.create_table(
        "vehicles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("provider_user_id", sa.UUID(), nullable=False),
        sa.Column("vehicle_type_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("brand", sa.String(length=60), nullable=False),
        sa.Column("model", sa.String(length=60), nullable=False),
        sa.Column("manufacture_year", sa.Integer(), nullable=False),
        sa.Column("registration_number", sa.String(length=20), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("price_amount", sa.Integer(), nullable=False),
        sa.Column("price_unit", price_unit, nullable=False),
        sa.Column("location_text", sa.String(length=160), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("fuel_type", fuel_type, nullable=False),
        sa.Column("power_hp", sa.Integer(), nullable=False),
        sa.Column("transmission", transmission, nullable=False),
        sa.Column("is_available", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("listing_status", listing_status, server_default="APPROVED", nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90",
            name=op.f("ck_vehicles_latitude_in_range"),
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name=op.f("ck_vehicles_longitude_in_range"),
        ),
        sa.CheckConstraint(
            "manufacture_year BETWEEN 1950 AND 2100",
            name=op.f("ck_vehicles_manufacture_year_plausible"),
        ),
        sa.CheckConstraint(
            "power_hp BETWEEN 1 AND 2000", name=op.f("ck_vehicles_power_hp_plausible")
        ),
        sa.CheckConstraint("price_amount > 0", name=op.f("ck_vehicles_price_amount_positive")),
        sa.ForeignKeyConstraint(
            ["provider_user_id"],
            ["users.id"],
            name=op.f("fk_vehicles_provider_user_id_users"),
            ondelete="CASCADE",
        ),
        # RESTRICT: deleting a type must never cascade into deleting listings.
        sa.ForeignKeyConstraint(
            ["vehicle_type_id"],
            ["vehicle_types.id"],
            name=op.f("fk_vehicles_vehicle_type_id_vehicle_types"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicles")),
    )
    op.create_index(
        op.f("ix_vehicles_provider_user_id"), "vehicles", ["provider_user_id"], unique=False
    )
    op.create_index(
        op.f("ix_vehicles_vehicle_type_id"), "vehicles", ["vehicle_type_id"], unique=False
    )
    # One physical vehicle, one live listing. Partial, so a soft-deleted listing
    # releases the number for re-listing.
    op.create_index(
        "uq_vehicles_registration_number_live",
        "vehicles",
        ["registration_number"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # The public feed filters on exactly these three, over live rows only.
    op.create_index(
        "ix_vehicles_discoverable",
        "vehicles",
        ["listing_status", "is_available", "vehicle_type_id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- vehicle_images ----------------------------------------------------
    op.create_table(
        "vehicle_images",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
            name=op.f("fk_vehicle_images_vehicle_id_vehicles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicle_images")),
        sa.UniqueConstraint(
            "vehicle_id", "sort_order", name=op.f("uq_vehicle_images_vehicle_id_sort_order")
        ),
    )
    op.create_index(
        op.f("ix_vehicle_images_vehicle_id"), "vehicle_images", ["vehicle_id"], unique=False
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_index(op.f("ix_vehicle_images_vehicle_id"), table_name="vehicle_images")
    op.drop_table("vehicle_images")

    op.drop_index(
        "ix_vehicles_discoverable",
        table_name="vehicles",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "uq_vehicles_registration_number_live",
        table_name="vehicles",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(op.f("ix_vehicles_vehicle_type_id"), table_name="vehicles")
    op.drop_index(op.f("ix_vehicles_provider_user_id"), table_name="vehicles")
    op.drop_table("vehicles")

    op.drop_index(op.f("ix_vehicle_types_code"), table_name="vehicle_types")
    op.drop_table("vehicle_types")

    # Drop the types too, or upgrading again fails with "type already exists".
    listing_status.drop(bind, checkfirst=True)
    price_unit.drop(bind, checkfirst=True)
    transmission.drop(bind, checkfirst=True)
    fuel_type.drop(bind, checkfirst=True)
