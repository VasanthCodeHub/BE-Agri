"""add profile fields to users (email, address, lat, lng)

Revision ID: 8f2a3c4d5e6f
Revises: 522c1f6d69c7
Created: 2026-08-20 12:45:00.000000+00:00

The app collects email, address and coordinates at registration; until now
there was nowhere to store them and the data was silently discarded.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f2a3c4d5e6f"
down_revision: str | None = "522c1f6d69c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.String(length=254), nullable=True))
    op.add_column("users", sa.Column("address", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("latitude", sa.Float(), nullable=True))
    op.add_column("users", sa.Column("longitude", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "longitude")
    op.drop_column("users", "latitude")
    op.drop_column("users", "address")
    op.drop_column("users", "email")
