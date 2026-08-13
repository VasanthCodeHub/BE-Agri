"""Shared pytest fixtures.

`conftest.py` is special: pytest imports it automatically and makes every
fixture defined here available to all tests, with no import needed.

The client fixture calls the app **in-process** through httpx's ASGI
transport — real routing, middleware, validation and serialisation, but no
network and no server to start. Fast and realistic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Test settings, independent of your local .env file."""
    return Settings(
        app_env="local",
        debug=True,
        log_level="WARNING",  # keep test output readable
        log_format="console",
        jwt_secret_key="test-secret-key-not-used-in-any-real-environment",
        # Same database as development, by choice — there is no separate test
        # database. Every database-backed test therefore runs inside a
        # transaction that is ALWAYS rolled back, so tests never leave rows
        # behind or modify your development data.
        database_url="postgresql+asyncpg://agri:agri_local_password@localhost:5432/agri_local",
        cors_origins="http://testserver",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """An HTTP client that talks to the app directly.

    Note: ASGITransport does not run the lifespan, so no startup database
    probe happens here. That is what lets these tests pass with no database.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as http_client:
        yield http_client
