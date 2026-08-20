"""allow power_hp = 0 for non-motorised implements

Revision ID: 9f3b4d5e6f70
Revises: 8f2a3c4d5e6f
Created: 2026-08-20 12:50:00.000000+00:00

Rotavators, trailers, sprayers and other implements have no engine. The old
constraint (power_hp BETWEEN 1 AND 2000) made them unlistable.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "9f3b4d5e6f70"
down_revision: str | None = "8f2a3c4d5e6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("power_hp_plausible", "vehicles", type_="check")
    op.create_check_constraint(
        "power_hp_plausible",
        "vehicles",
        "power_hp BETWEEN 0 AND 2000",
    )


def downgrade() -> None:
    op.drop_constraint("power_hp_plausible", "vehicles", type_="check")
    op.create_check_constraint(
        "power_hp_plausible",
        "vehicles",
        "power_hp BETWEEN 1 AND 2000",
    )
