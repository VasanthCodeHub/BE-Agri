"""Shared pytest fixtures.

`conftest.py` is special: pytest imports it automatically, so every fixture
here is available to all tests with no import.

THE IMPORTANT PART — how tests avoid touching your development data
--------------------------------------------------------------------
We deliberately have no separate test database, so tests run against
`agri_local`. To make that safe, each test runs inside a transaction that is
ALWAYS rolled back at the end:

    open connection → BEGIN → run the test → ROLLBACK → close

`join_transaction_mode="create_savepoint"` is what makes this work even though
the app calls `session.commit()`. Instead of committing for real, the session's
commit releases a SAVEPOINT inside our outer transaction — so application code
behaves exactly as it does in production, and the final rollback still discards
everything.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.integrations.sms import get_sms_provider, reset_sms_provider
from app.integrations.sms.fake import FakeSmsProvider
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Test settings, independent of your local .env."""
    return Settings(
        app_env="local",
        debug=True,
        log_level="WARNING",  # keep test output readable
        log_format="console",
        jwt_secret_key="test-secret-key-not-used-in-any-real-environment",
        database_url="postgresql+asyncpg://agri:agri_local_password@localhost:5432/agri_local",
        cors_origins="http://testserver",
        sms_provider="fake",
        otp_dev_bypass_code="0000",
        otp_max_attempts=5,
    )


@pytest.fixture
async def db_session(settings: Settings) -> AsyncIterator[AsyncSession]:
    """A session whose work is always rolled back."""
    engine = create_async_engine(settings.database_url)
    connection = await engine.connect()
    transaction = await connection.begin()

    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        # Application commits become savepoint releases, not real commits.
        join_transaction_mode="create_savepoint",
    )

    try:
        yield session
    finally:
        await session.close()
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest.fixture
def app(settings: Settings, db_session: AsyncSession) -> FastAPI:
    """The application, wired to the rolled-back session and test settings.

    `dependency_overrides` is FastAPI's built-in seam for exactly this: swap a
    dependency for a test double without changing any application code.
    """
    application = create_app(settings)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_settings] = lambda: settings

    reset_sms_provider()  # fresh fake provider per test
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """An HTTP client that calls the app in-process.

    Real routing, middleware, validation and serialisation — no network, no
    server to start.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as http_client:
        yield http_client


@pytest.fixture
def sms(app: FastAPI, settings: Settings) -> FakeSmsProvider:
    """The fake SMS provider, so a test can read the code that was 'sent'."""
    provider = get_sms_provider(settings)
    assert isinstance(provider, FakeSmsProvider)
    return provider


@pytest.fixture
def phone() -> str:
    """A phone number for tests. Rolled back, so it never persists."""
    return "9800000001"
