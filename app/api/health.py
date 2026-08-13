"""Health and readiness endpoints.

Two endpoints, two different questions — and the distinction matters once
this runs behind a load balancer:

- **/health (liveness)** — "is the process alive?" Checks nothing external.
  If this fails, restart the container.
- **/ready (readiness)** — "can it actually serve traffic?" Checks the
  database. If this fails, stop sending it requests but do NOT restart it —
  the database being down is not fixed by restarting the app.

Getting these the wrong way round causes restart storms: the database hiccups,
every app instance fails its liveness check, they all restart at once, and
they hammer the recovering database with a fresh flood of connections.

These endpoints are deliberately unversioned: they are infrastructure, not
part of the mobile API contract.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.db.session import check_database, check_postgis

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    environment: str


class ReadyResponse(BaseModel):
    ready: bool
    checks: dict[str, str]


@router.get("/health", response_model=HealthResponse, summary="Liveness check")
async def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """Always 200 if the process is running. No external calls."""
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env.value,
    )


@router.get("/ready", response_model=ReadyResponse, summary="Readiness check")
async def ready(response: Response) -> ReadyResponse:
    """Check dependencies. Returns 503 if any are unavailable.

    Right now only the database is checked. Redis joins this list when the
    OTP/rate-limit work lands.
    """
    db_ok = await check_database()

    checks = {"database": "ok" if db_ok else "unavailable"}

    # Reported for visibility, but not part of the ready verdict: PostGIS is
    # only required from Phase 6 (radius search) onward.
    if db_ok:
        checks["postgis"] = await check_postgis()

    all_ok = db_ok
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadyResponse(ready=all_ok, checks=checks)
