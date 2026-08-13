"""enable postgis extension

Revision ID: 0001_extensions
Revises: None
Created: 2026-08-13

Installs PostGIS, which provides the geography column type and spatial
indexing that radius search depends on (ADR-002 in docs/PROJECT.md).

WHY THIS MIGRATION IS CONDITIONAL
---------------------------------
Normally a migration should do exactly the same thing everywhere. This one
checks whether PostGIS is available first, and skips with a warning if it is
not.

The reason: PostGIS is a separate Windows download from PostgreSQL itself, and
nothing before Phase 6 (search) needs it. Making this migration hard-fail would
block authentication, profiles and listings work on an unrelated installer step.

This is a deliberate, temporary trade-off:

  - The Phase 6 migration that adds the `location geography(Point,4326)` column
    will fail loudly without PostGIS — which is correct, because by then it is
    genuinely required.
  - `/ready` reports PostGIS status, so a deployment missing it is visible.

Once PostGIS is installed, re-run this migration's logic with:
    psql -U agri -d agri_local -c "CREATE EXTENSION IF NOT EXISTS postgis;"
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_extensions"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()

    # pg_available_extensions lists what the server COULD install, i.e. which
    # extension control files exist on disk. Checking this first turns a hard
    # crash into an actionable warning.
    is_available = connection.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'postgis'")
    ).scalar()

    if is_available:
        # IF NOT EXISTS makes this safe to re-run.
        # Note: creating an extension requires elevated database privileges.
        op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        version = connection.execute(sa.text("SELECT postgis_version()")).scalar()
        print(f"  PostGIS enabled: {version}")
    else:
        print(
            "\n"
            "  ****************************************************************\n"
            "  PostGIS is NOT installed on this PostgreSQL server - SKIPPING.\n"
            "\n"
            "  This is fine for now: nothing before Phase 6 (radius search)\n"
            "  needs it. Authentication, profiles and listings all work.\n"
            "\n"
            "  To install it later, download the PostGIS bundle matching your\n"
            "  PostgreSQL major version from:\n"
            "      https://download.osgeo.org/postgis/windows/\n"
            "  then run:\n"
            '      psql -U agri -d agri_local -c "CREATE EXTENSION postgis;"\n'
            "  ****************************************************************\n"
        )


def downgrade() -> None:
    # RESTRICT (the default) refuses to drop the extension while any table
    # still has a geography/geometry column — a useful safety net.
    op.execute("DROP EXTENSION IF EXISTS postgis")
