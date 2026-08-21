"""remove bookings, add vehicle master data and contact calls

Revision ID: b7c2d4e6f8a0
Revises: 9f3b4d5e6f70
Created: 2026-08-21 09:00:00.000000+00:00

The product has no booking system, so:

1. The `bookings` table and its `booking_status` / `session_block` enum types
   are dropped. Any booking rows in the database are deleted by this
   migration — that is intentional (the mobile app is being rebuilt without
   bookings).

2. `user_role` gains the `USER` value in place of `RENTER`. `ALTER TYPE ...
   RENAME VALUE` rewrites every existing row (users, otp_requests,
   refresh_tokens) in place — no data backfill needed.

3. Vehicle master data arrives: `vehicle_manufacturers`,
   `vehicle_models`, `vehicle_variants`. `vehicles` gains three nullable
   foreign keys; `SET NULL` so retiring a master row never deletes listings.

4. `contact_calls` records direct caller→provider contact (the dashboard
   counts these; the app has no bookings).

The `notification_type` enum still contains the retired BOOKING_* values:
PostgreSQL refuses to drop a value still in use by existing rows, and the
Python model no longer inserts them, so they are inert. If you want them
gone, first purge notification rows that use them, then:

    ALTER TYPE notification_type DROP VALUE IF EXISTS 'BOOKING_REQUEST';
    ALTER TYPE notification_type DROP VALUE IF EXISTS 'BOOKING_ACCEPTED';
    ALTER TYPE notification_type DROP VALUE IF EXISTS 'BOOKING_REJECTED';
    ALTER TYPE notification_type DROP VALUE IF EXISTS 'BOOKING_CANCELLED';
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7c2d4e6f8a0"
down_revision: str | None = "9f3b4d5e6f70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Shared enum types — declared once, referenced with create_type=False.
# These types are created by the earlier migration (4d996dee3993).
# We re-declare them here with create_type=False so this migration can
# reference them without asking PostgreSQL to CREATE TYPE again.
# checkfirst=True in upgrade() makes this safe even on a fresh database.
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

# Booking-related enums (dropped in upgrade, recreated in downgrade).
booking_status = postgresql.ENUM(
    "PENDING",
    "ACCEPTED",
    "ACTIVE",
    "COMPLETED",
    "REJECTED",
    "CANCELLED",
    name="booking_status",
    create_type=False,
)
session_block = postgresql.ENUM(
    "MORNING", "AFTERNOON", "EVENING", "FULL_DAY", name="session_block", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()

    # Ensure shared enum types exist (created by 4d996dee3993, but checkfirst
    # makes this safe on a fresh database where that migration hasn't run yet).
    fuel_type.create(bind, checkfirst=True)
    transmission.create(bind, checkfirst=True)
    price_unit.create(bind, checkfirst=True)
    listing_status.create(bind, checkfirst=True)

    # --- Role: RENTER is now USER -----------------------------------------
    op.execute("ALTER TYPE user_role RENAME VALUE 'RENTER' TO 'USER'")

    # --- Bookings are gone ------------------------------------------------
    op.drop_index(op.f("ix_bookings_vehicle_id"), table_name="bookings")
    op.drop_index(op.f("ix_bookings_renter_user_id"), table_name="bookings")
    op.drop_index("ix_bookings_renter", table_name="bookings")
    op.drop_index(op.f("ix_bookings_reference"), table_name="bookings")
    op.drop_index(op.f("ix_bookings_provider_user_id"), table_name="bookings")
    op.drop_index("ix_bookings_provider", table_name="bookings")
    op.drop_table("bookings")
    op.execute("DROP TYPE IF EXISTS booking_status")
    op.execute("DROP TYPE IF EXISTS session_block")

    # --- Vehicle master data ----------------------------------------------
    op.create_table(
        "vehicle_manufacturers",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicle_manufacturers")),
        sa.UniqueConstraint("name", name=op.f("uq_vehicle_manufacturers_name")),
    )

    op.create_table(
        "vehicle_models",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("manufacturer_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("vehicle_type_id", sa.UUID(), nullable=False),
        sa.Column("fuel_type", fuel_type, nullable=False),
        sa.Column("power_hp", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["manufacturer_id"],
            ["vehicle_manufacturers.id"],
            name=op.f("fk_vehicle_models_manufacturer_id_vehicle_manufacturers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_type_id"],
            ["vehicle_types.id"],
            name=op.f("fk_vehicle_models_vehicle_type_id_vehicle_types"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicle_models")),
        sa.UniqueConstraint("manufacturer_id", "name", name="uq_vehicle_models_manufacturer_name"),
    )
    op.create_index(
        op.f("ix_vehicle_models_manufacturer_id"),
        "vehicle_models",
        ["manufacturer_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vehicle_models_vehicle_type_id"),
        "vehicle_models",
        ["vehicle_type_id"],
        unique=False,
    )

    op.create_table(
        "vehicle_variants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("model_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("manufacture_year", sa.Integer(), nullable=True),
        sa.Column("power_hp", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["vehicle_models.id"],
            name=op.f("fk_vehicle_variants_model_id_vehicle_models"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicle_variants")),
        sa.UniqueConstraint("model_id", "name", name="uq_vehicle_variants_model_name"),
    )
    op.create_index(
        op.f("ix_vehicle_variants_model_id"), "vehicle_variants", ["model_id"], unique=False
    )

    # --- Vehicles reference the master data (all optional) ------------------
    op.add_column("vehicles", sa.Column("manufacturer_id", sa.UUID(), nullable=True))
    op.add_column("vehicles", sa.Column("model_id", sa.UUID(), nullable=True))
    op.add_column("vehicles", sa.Column("variant_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_vehicles_manufacturer_id_vehicle_manufacturers"),
        "vehicles",
        "vehicle_manufacturers",
        ["manufacturer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_vehicles_model_id_vehicle_models"),
        "vehicles",
        "vehicle_models",
        ["model_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_vehicles_variant_id_vehicle_variants"),
        "vehicles",
        "vehicle_variants",
        ["variant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_vehicles_manufacturer_id"), "vehicles", ["manufacturer_id"], unique=False
    )
    op.create_index(op.f("ix_vehicles_model_id"), "vehicles", ["model_id"], unique=False)
    op.create_index(op.f("ix_vehicles_variant_id"), "vehicles", ["variant_id"], unique=False)

    # --- Contact calls ------------------------------------------------------
    op.create_table(
        "contact_calls",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("caller_user_id", sa.UUID(), nullable=False),
        sa.Column("provider_user_id", sa.UUID(), nullable=False),
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["caller_user_id"],
            ["users.id"],
            name=op.f("fk_contact_calls_caller_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["provider_user_id"],
            ["users.id"],
            name=op.f("fk_contact_calls_provider_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
            name=op.f("fk_contact_calls_vehicle_id_vehicles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_calls")),
    )
    op.create_index(
        op.f("ix_contact_calls_caller_user_id"), "contact_calls", ["caller_user_id"], unique=False
    )
    op.create_index(
        op.f("ix_contact_calls_provider_user_id"),
        "contact_calls",
        ["provider_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_contact_calls_provider_created",
        "contact_calls",
        ["provider_user_id", "created_at"],
        unique=False,
        postgresql_using="btree",
        postgresql_ops={"created_at": "DESC"},
    )
    op.create_index(
        op.f("ix_contact_calls_vehicle_id"), "contact_calls", ["vehicle_id"], unique=False
    )


def downgrade() -> None:
    # --- Contact calls ------------------------------------------------------
    op.drop_index(op.f("ix_contact_calls_vehicle_id"), table_name="contact_calls")
    op.drop_index(
        "ix_contact_calls_provider_created",
        table_name="contact_calls",
        postgresql_using="btree",
        postgresql_ops={"created_at": "DESC"},
    )
    op.drop_index(op.f("ix_contact_calls_provider_user_id"), table_name="contact_calls")
    op.drop_index(op.f("ix_contact_calls_caller_user_id"), table_name="contact_calls")
    op.drop_table("contact_calls")

    # --- Vehicles: drop master refs -----------------------------------------
    op.drop_index(op.f("ix_vehicles_variant_id"), table_name="vehicles")
    op.drop_index(op.f("ix_vehicles_model_id"), table_name="vehicles")
    op.drop_index(op.f("ix_vehicles_manufacturer_id"), table_name="vehicles")
    op.drop_constraint(
        op.f("fk_vehicles_variant_id_vehicle_variants"), "vehicles", type_="foreignkey"
    )
    op.drop_constraint(op.f("fk_vehicles_model_id_vehicle_models"), "vehicles", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_vehicles_manufacturer_id_vehicle_manufacturers"), "vehicles", type_="foreignkey"
    )
    op.drop_column("vehicles", "variant_id")
    op.drop_column("vehicles", "model_id")
    op.drop_column("vehicles", "manufacturer_id")

    # --- Vehicle master data -------------------------------------------------
    op.drop_index(op.f("ix_vehicle_variants_model_id"), table_name="vehicle_variants")
    op.drop_table("vehicle_variants")
    op.drop_index(op.f("ix_vehicle_models_vehicle_type_id"), table_name="vehicle_models")
    op.drop_index(op.f("ix_vehicle_models_manufacturer_id"), table_name="vehicle_models")
    op.drop_table("vehicle_models")
    op.drop_table("vehicle_manufacturers")

    # --- Bookings return ------------------------------------------------------
    op.execute(
        "CREATE TYPE booking_status AS ENUM ('PENDING','ACCEPTED','ACTIVE','COMPLETED','REJECTED','CANCELLED')"
    )
    op.execute("CREATE TYPE session_block AS ENUM ('MORNING','AFTERNOON','EVENING','FULL_DAY')")
    op.create_table(
        "bookings",
        sa.Column("vehicle_id", sa.UUID(), nullable=False),
        sa.Column("renter_user_id", sa.UUID(), nullable=False),
        sa.Column("provider_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "ACCEPTED",
                "ACTIVE",
                "COMPLETED",
                "REJECTED",
                "CANCELLED",
                name="booking_status",
                create_type=False,
            ),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("booking_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "session",
            sa.Enum(
                "MORNING",
                "AFTERNOON",
                "EVENING",
                "FULL_DAY",
                name="session_block",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("duration_hours", sa.Integer(), nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column("renter_note", sa.Text(), nullable=True),
        sa.Column("provider_note", sa.Text(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reference", sa.String(length=20), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.CheckConstraint("amount_paise > 0", name=op.f("ck_bookings_amount_paise_positive")),
        sa.CheckConstraint(
            "duration_hours BETWEEN 1 AND 24", name=op.f("ck_bookings_duration_hours_valid")
        ),
        sa.ForeignKeyConstraint(
            ["provider_user_id"],
            ["users.id"],
            name=op.f("fk_bookings_provider_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["renter_user_id"],
            ["users.id"],
            name=op.f("fk_bookings_renter_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
            name=op.f("fk_bookings_vehicle_id_vehicles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bookings")),
    )
    op.create_index(
        "ix_bookings_provider", "bookings", ["provider_user_id", "created_at"], unique=False
    )
    op.create_index(
        op.f("ix_bookings_provider_user_id"), "bookings", ["provider_user_id"], unique=False
    )
    op.create_index(op.f("ix_bookings_reference"), "bookings", ["reference"], unique=True)
    op.create_index(
        "ix_bookings_renter", "bookings", ["renter_user_id", "created_at"], unique=False
    )
    op.create_index(
        op.f("ix_bookings_renter_user_id"), "bookings", ["renter_user_id"], unique=False
    )
    op.create_index(op.f("ix_bookings_vehicle_id"), "bookings", ["vehicle_id"], unique=False)

    # --- Role: USER becomes RENTER again -------------------------------------
    op.execute("ALTER TYPE user_role RENAME VALUE 'USER' TO 'RENTER'")
