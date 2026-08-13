"""Database engine, session factory, and the `get_db` dependency.

Two objects to keep straight:

- **Engine** — one per process. Owns the connection pool. Expensive to
  create, so it is created once and reused.
- **Session** — one per request. Your unit of work: it tracks the objects you
  loaded and changed, and flushes them in one transaction.

The engine is created lazily so importing this module never opens a socket.
That matters right now: the app can boot and serve /health even with no
database running.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the process-wide engine, creating it on first use."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=settings.db_echo,  # True logs every SQL statement — noisy but great for learning
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            # Sends a cheap "SELECT 1" before handing out a pooled connection.
            # Without it, a connection dropped by the database (restart,
            # idle timeout) is handed to your code and fails the request.
            pool_pre_ping=True,
        )
        log.debug("db_engine_created", pool_size=settings.db_pool_size)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory, creating it on first use."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            # Keep attributes loaded after commit. With the default (True),
            # touching any attribute after a commit triggers a lazy reload —
            # which in async code raises MissingGreenlet. This is the single
            # most common async-SQLAlchemy trap.
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding one session per request.

    Usage in a route:

        async def handler(db: AsyncSession = Depends(get_db)):
            ...

    One transaction per request: commit if the handler succeeded, roll back if
    anything raised. So a request that fails halfway leaves no partial writes —
    a provider is never created without its profile row.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database() -> bool:
    """Return True if the database answers. Used by /ready."""
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # readiness must never raise, only report
        log.warning("database_unreachable", error=str(exc), error_type=type(exc).__name__)
        return False


async def check_postgis() -> str:
    """Report PostGIS availability: "enabled", "not_installed" or "unknown".

    PostGIS is a separate install from PostgreSQL on Windows and is required
    from Phase 6 (radius search) onward. Surfacing it in /ready means a server
    missing it is obvious immediately, rather than at the first search request.
    """
    try:
        async with get_engine().connect() as conn:
            installed = await conn.scalar(
                text("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")
            )
            if installed:
                return "enabled"
            available = await conn.scalar(
                text("SELECT 1 FROM pg_available_extensions WHERE name = 'postgis'")
            )
            return "available_not_enabled" if available else "not_installed"
    except Exception:
        return "unknown"


async def dispose_engine() -> None:
    """Close all pooled connections. Called on application shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        log.debug("db_engine_disposed")
    _engine = None
    _session_factory = None
