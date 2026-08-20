"""add physical-vehicle identity fields and new vehicle types

Revision ID: c8e3f5a7b9c1
Revises: b7c2d4e6f8a0
Created: 2026-08-21 10:00:00.000000+00:00

The Add Vehicle form needs identity for the PHYSICAL vehicle, kept strictly
separate from the master rows (one model can have thousands of physical
vehicles, each with its own RC book):

    vehicles.rc_number
    vehicles.rc_document_public_id   (Cloudinary public_id, like photos)
    vehicles.engine_number
    vehicles.chassis_number

All four are nullable: existing listings keep working, and a provider may
leave them blank. They are private to the owner — public endpoints never
return them.

Also seeds three vehicle types the master-data cascade examples need:
BACKHOE_LOADER (JCB 3DX), CULTIVATOR, and OTHER. Tamil names stay NULL
pending the Q14 review, like BALER/LEVELLER/WATER_TANKER.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8e3f5a7b9c1"
down_revision: str | None = "b7c2d4e6f8a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_TYPES = [
    # (code, name_en, name_ta, sort_order)
    ("BACKHOE_LOADER", "Backhoe loader", None, 130),
    ("CULTIVATOR", "Cultivator", None, 140),
    ("OTHER", "Other", None, 1000),
]


def upgrade() -> None:
    op.add_column("vehicles", sa.Column("rc_number", sa.String(length=40), nullable=True))
    op.add_column(
        "vehicles", sa.Column("rc_document_public_id", sa.String(length=255), nullable=True)
    )
    op.add_column("vehicles", sa.Column("engine_number", sa.String(length=40), nullable=True))
    op.add_column("vehicles", sa.Column("chassis_number", sa.String(length=40), nullable=True))

    types_table = sa.table(
        "vehicle_types",
        sa.column("id", sa.UUID()),
        sa.column("code", sa.String()),
        sa.column("name_en", sa.String()),
        sa.column("name_ta", sa.String()),
        sa.column("sort_order", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
    )
    # Idempotent: only rows whose code is not already present get inserted
    # (the unique index on code is the backstop; the WHERE NOT EXISTS makes
    # the migration safe even on a partially-migrated database).
    bind = op.get_bind()
    for code, name_en, name_ta, sort_order in _NEW_TYPES:
        existing = bind.execute(
            sa.text("SELECT 1 FROM vehicle_types WHERE code = :code LIMIT 1"),
            {"code": code},
        ).first()
        if existing is not None:
            continue
        bind.execute(
            sa.insert(types_table).values(
                id=uuid.uuid4(),
                code=code,
                name_en=name_en,
                name_ta=name_ta,
                sort_order=sort_order,
                is_active=True,
            )
        )


def downgrade() -> None:
    op.execute("DELETE FROM vehicle_types WHERE code IN ('BACKHOE_LOADER', 'CULTIVATOR', 'OTHER')")
    op.drop_column("vehicles", "chassis_number")
    op.drop_column("vehicles", "engine_number")
    op.drop_column("vehicles", "rc_document_public_id")
    op.drop_column("vehicles", "rc_number")
