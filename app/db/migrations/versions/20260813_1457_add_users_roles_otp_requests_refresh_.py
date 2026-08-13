"""add users, roles, otp_requests, refresh_tokens

Revision ID: 73d129f5ac63
Revises: 0001_extensions
Created: 2026-08-13 14:57:47.291461+00:00

HAND-CORRECTED after autogenerate. Three fixes were needed — a good
illustration of why generated migrations must be read before they are run:

1. Autogenerate emitted `sa.Enum(..., metadata=MetaData())`, but never imported
   `MetaData`. That is an immediate NameError on execution.

2. The `user_role` enum was declared inline in three tables, so PostgreSQL would
   have been asked to CREATE TYPE user_role three times and failed on the
   second. Fixed by creating each type ONCE up front and referencing it with
   `create_type=False`.

3. `downgrade()` dropped the tables but left the enum types behind, so
   downgrading and upgrading again would fail with "type already exists".
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "73d129f5ac63"
down_revision: str | None = "0001_extensions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Declared once, shared by every table that uses them.
# create_type=False stops create_table from emitting its own CREATE TYPE; we
# create them explicitly in upgrade() instead.
user_role = postgresql.ENUM("RENTER", "PROVIDER", name="user_role", create_type=False)
user_status = postgresql.ENUM("ACTIVE", "SUSPENDED", name="user_status", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()

    # checkfirst=True makes this safe to re-run.
    user_role.create(bind, checkfirst=True)
    user_status.create(bind, checkfirst=True)

    # --- users -------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("phone_e164", sa.String(length=16), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=True),
        sa.Column("status", user_status, server_default="ACTIVE", nullable=False),
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    # Unique: the phone number is the identity.
    op.create_index(op.f("ix_users_phone_e164"), "users", ["phone_e164"], unique=True)

    # --- user_roles --------------------------------------------------------
    op.create_table(
        "user_roles",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", user_role, nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_roles_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_roles")),
        # The database guarantees a user cannot hold the same role twice.
        sa.UniqueConstraint("user_id", "role", name=op.f("uq_user_roles_user_id_role")),
    )
    op.create_index(op.f("ix_user_roles_user_id"), "user_roles", ["user_id"], unique=False)

    # --- otp_requests ------------------------------------------------------
    op.create_table(
        "otp_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("phone_e164", sa.String(length=16), nullable=False),
        sa.Column("code_hash", sa.String(length=255), nullable=False),
        sa.Column("requested_role", user_role, nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_otp_requests")),
    )
    op.create_index(
        op.f("ix_otp_requests_phone_e164"), "otp_requests", ["phone_e164"], unique=False
    )
    op.create_index(
        op.f("ix_otp_requests_created_at"), "otp_requests", ["created_at"], unique=False
    )

    # --- refresh_tokens ----------------------------------------------------
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column("active_role", user_role, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
    )
    # Unique: tokens are looked up by hash, and no hash may repeat.
    op.create_index(
        op.f("ix_refresh_tokens_token_hash"), "refresh_tokens", ["token_hash"], unique=True
    )
    op.create_index(op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_refresh_tokens_family_id"), "refresh_tokens", ["family_id"], unique=False
    )


def downgrade() -> None:
    # Reverse order: children before parents, then the enum types.
    op.drop_index(op.f("ix_refresh_tokens_family_id"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_token_hash"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    op.drop_index(op.f("ix_otp_requests_created_at"), table_name="otp_requests")
    op.drop_index(op.f("ix_otp_requests_phone_e164"), table_name="otp_requests")
    op.drop_table("otp_requests")

    op.drop_index(op.f("ix_user_roles_user_id"), table_name="user_roles")
    op.drop_table("user_roles")

    op.drop_index(op.f("ix_users_phone_e164"), table_name="users")
    op.drop_table("users")

    bind = op.get_bind()
    user_status.drop(bind, checkfirst=True)
    user_role.drop(bind, checkfirst=True)
