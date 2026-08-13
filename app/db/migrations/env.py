"""Alembic environment.

Two jobs:

1. Supply the database URL from Settings (never from the committed .ini file).
2. Give Alembic the metadata of all our models, so `--autogenerate` can diff
   Python models against the real database and write the migration for you.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

# Importing this module registers every ORM model on Base.metadata.
# Without it, autogenerate sees no tables and happily writes a migration
# that DROPS everything.
from app.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()

#: PostGIS creates these itself when the extension is installed. They are not
#: our tables, so autogenerate must ignore them — otherwise every generated
#: migration includes a `drop_table("spatial_ref_sys")`, which would break
#: PostGIS.
_IGNORED_TABLES = frozenset({"spatial_ref_sys", "geometry_columns", "geography_columns"})


def include_object(
    obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any
) -> bool:
    """Filter what autogenerate considers."""
    if type_ == "table" and name in _IGNORED_TABLES:
        return False
    # PostGIS creates its own spatial indexes (idx_*); don't let Alembic
    # try to manage or drop them.
    return not (type_ == "index" and reflected and (name or "").startswith("idx_"))


def _configure(**kwargs: Any) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_object=include_object,
        # Detect column type changes (e.g. VARCHAR(50) -> VARCHAR(100)).
        # Off by default, which silently misses real schema drift.
        compare_type=True,
        compare_server_default=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it.

    `alembic upgrade head --sql` uses this. Useful when a DBA must review or
    apply the SQL by hand in a locked-down production environment.
    """
    _configure(
        url=settings.database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against the async engine.

    NullPool: this is a short-lived CLI process, so pooling connections is
    pointless and would just delay exit.
    """
    engine = create_async_engine(settings.database_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        # run_sync bridges Alembic (which is synchronous) onto our async
        # connection.
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
