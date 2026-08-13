"""Tests for the system endpoints and the error envelope.

These are the first tests in the project and they deliberately need no
database, so the foundation is verifiable before PostgreSQL exists.
"""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_returns_ok(client: AsyncClient) -> None:
    """Liveness must succeed with no external dependencies."""
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "local"


async def test_root_returns_service_banner(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert response.json()["service"] == "Agri Vehicle Rental API"


async def test_ready_reports_dependency_status(client: AsyncClient) -> None:
    """Readiness reports each dependency and 503s if any is down.

    Asserts the *shape*, not the verdict, so it passes whether or not
    PostgreSQL happens to be running.
    """
    response = await client.get("/ready")

    assert response.status_code in (200, 503)
    body = response.json()
    assert "database" in body["checks"]
    assert body["ready"] is (response.status_code == 200)


async def test_every_response_carries_a_request_id(client: AsyncClient) -> None:
    """The middleware must stamp X-Request-ID on every response."""
    response = await client.get("/health")

    assert response.headers.get("X-Request-ID")


async def test_incoming_request_id_is_preserved(client: AsyncClient) -> None:
    """If a client sends a request id, we trace with it rather than replacing it."""
    response = await client.get("/health", headers={"X-Request-ID": "trace-me-123"})

    assert response.headers["X-Request-ID"] == "trace-me-123"


async def test_security_headers_are_present(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"


async def test_unknown_route_uses_the_error_envelope(client: AsyncClient) -> None:
    """A 404 must use our envelope, not FastAPI's default {"detail": ...}."""
    response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["request_id"]


async def test_docs_are_served_locally(client: AsyncClient) -> None:
    """Docs are on locally. The production case is covered separately."""
    assert (await client.get("/docs")).status_code == 200
    assert (await client.get("/openapi.json")).status_code == 200
