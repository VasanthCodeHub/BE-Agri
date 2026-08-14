"""store cloudinary public_id instead of image url

Revision ID: 171ff741f85d
Revises: 4d996dee3993
Created: 2026-08-14 11:00:05.002045+00:00

`vehicle_images.url` becomes `vehicle_images.public_id`.

WHY, in one line: a public_id can be served at any size, so the listing feed can
request 400px thumbnails instead of full-resolution photos — which on a rural 4G
connection decides whether the app feels usable. It also lets us verify an image
really came from our own Cloudinary folder, and it keeps a future move off
Cloudinary to one URL-building function instead of every stored row.

THIS MIGRATION IS INTENTIONALLY LOSSY, and the reason it is safe here is that
`vehicle_images` was empty when it was written (checked, 0 rows) — the URL-based
API never shipped.

It is NOT a rename. A stored URL cannot be converted into a public_id, because
the ids only exist for assets uploaded through the signed-upload endpoint that
this same change introduces. So any pre-existing row is deleted rather than
migrated: keeping it would mean a listing pointing at a photo we cannot verify,
serve at multiple sizes, or prove is ours.

The explicit DELETE also makes the migration deterministic. Without it,
`add_column(..., nullable=False)` fails on any table that does have rows, and the
failure would appear during a deploy rather than here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "171ff741f85d"
down_revision: str | None = "4d996dee3993"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Old rows hold URLs, which cannot become public_ids. See the note above.
    op.execute("DELETE FROM vehicle_images")
    op.add_column("vehicle_images", sa.Column("public_id", sa.String(length=255), nullable=False))
    op.drop_column("vehicle_images", "url")


def downgrade() -> None:
    # Symmetrically lossy: a public_id cannot become the URL column's contents
    # either, and the NOT NULL add would fail on a populated table.
    op.execute("DELETE FROM vehicle_images")
    op.add_column("vehicle_images", sa.Column("url", sa.String(length=500), nullable=False))
    op.drop_column("vehicle_images", "public_id")
