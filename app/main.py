"""FastAPI application factory.

`create_app()` builds the application rather than creating it at import time.
That matters for testing: a test can build an app with different settings,
which is impossible if the app is a module-level constant configured once.

Run it with:
    uv run uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.db.session import check_database, dispose_engine
from app.integrations.sms import close_sms_provider

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown.

    Everything before `yield` runs once at startup; everything after runs at
    shutdown. Replaces the deprecated @app.on_event handlers.
    """
    settings = get_settings()
    log.info(
        "app_starting",
        environment=settings.app_env.value,
        version=settings.app_version,
        docs_enabled=settings.docs_enabled,
    )

    # Probe the database, but do NOT fail startup if it is down. A running app
    # that reports "not ready" is far more debuggable than a container that
    # crash-loops before it can log anything useful.
    if await check_database():
        log.info("database_connected")
    else:
        log.warning(
            "database_unavailable_at_startup",
            hint="The app will serve /health but not data endpoints. Is PostgreSQL running?",
        )

    yield

    # Return pooled connections cleanly so PostgreSQL is not left with
    # sockets to reap, and close the SMS vendor's HTTP pool.
    await close_sms_provider()
    await dispose_engine()
    log.info("app_stopped")


def _api_description(settings: Settings) -> str:
    """The text at the top of /docs.

    Written for the Flutter developer, who has this page and nothing else. It
    answers the two questions the endpoint list cannot: how login actually
    flows, and what every error looks like.

    It is built from `settings` so the OTP length and the dev bypass are never
    documented wrongly — the page describes the server that is actually running.
    """
    login_help = (
        f"No SMS is sent locally. The code is printed in the **server terminal** as "
        f"`fake_sms_otp ... otp={'1' * settings.otp_length}`."
    )
    if settings.otp_dev_bypass_code:
        login_help += (
            f"\n\nThe development bypass code **`{settings.otp_dev_bypass_code}`** also works "
            "for any number. It is rejected at startup in production."
        )
    if settings.sms_provider == "twilio":
        login_help = "Codes are delivered by SMS through Twilio."

    return f"""
Backend API for the Agri-Vehicle Rental app.

### Logging in

Two steps. The phone number is the identity; there are no passwords.

1. `POST /auth/otp/request` — send the phone number **and a role**
   (`RENTER` or `PROVIDER`). The response says whether the number is new and
   whether you must collect a name.
2. `POST /auth/otp/verify` — send the phone number, the {settings.otp_length}-digit code, and the
   name if it was asked for. You get back a user and two tokens.

The role is stored against the code, so it cannot be swapped at step 2. The user
row is created at step 2 — an unverified number never enters the database.

{login_help}

### Using the tokens

Send the access token on every request:

```
Authorization: Bearer <access_token>
```

It lasts {settings.access_token_ttl_minutes} minutes. When a call returns `401 TOKEN_EXPIRED`, call
`POST /auth/refresh` with the refresh token and retry — the user sees nothing.
Each refresh returns a **new** refresh token and invalidates the old one; if an
already-used one is presented again, every session in that chain is revoked.

In this page, click **Authorize** and paste an access token to try the protected
endpoints.

### Errors

Every error has the same shape, so the client parses one format:

```json
{{
  "error": {{
    "code": "OTP_INVALID",
    "message": "That code is not correct.",
    "details": {{ "remaining_attempts": 3 }},
    "request_id": "0f9c1e4a2b"
  }}
}}
```

Branch on `code`, never on `message` — wording changes, codes do not.
`request_id` is also returned as the `X-Request-ID` header and appears in the
server logs, so a screenshot of an error is enough to find the request.
"""


#: Descriptions for the endpoint groups in /docs. Without these a tag is just a
#: bare heading, and the reader has to guess what belongs under it.
_OPENAPI_TAGS = [
    {
        "name": "auth",
        "description": (
            "Login, sessions and logout. Start with `POST /auth/otp/request`; "
            "`GET /auth/me` is the session check to call on app start."
        ),
    },
    {
        "name": "vehicles",
        "description": (
            "Listings. `/provider/vehicles` requires the PROVIDER role and only "
            "ever touches the caller's own vehicles; `GET /vehicles` is the "
            "public feed and needs no token."
        ),
    },
    {
        "name": "uploads",
        "description": (
            "Permission to upload an image. The app uploads **directly to "
            "Cloudinary** — image bytes never pass through this API, which is "
            "what keeps uploads fast on a slow connection."
        ),
    },
    {
        "name": "system",
        "description": (
            "Infrastructure probes, deliberately unversioned — they are not part "
            "of the mobile API contract. `/health` asks whether the process is "
            "alive; `/ready` asks whether it can serve traffic."
        ),
    },
]


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure the application."""
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        summary="Backend API for the Agri-Vehicle Rental app",
        description=_api_description(settings),
        openapi_tags=_OPENAPI_TAGS,
        contact={"name": "Backend", "email": "karthikeyans@softsuave.com"},
        lifespan=lifespan,
        # Docs are disabled in production — the schema maps the whole attack
        # surface. Controlled by config, never by an `if` in the code.
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    # --- Middleware --------------------------------------------------------
    # Order note: the LAST middleware added is the OUTERMOST, so it sees the
    # request first and the response last. RequestContextMiddleware is added
    # last so a request_id exists before any other code runs.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
    app.add_middleware(RequestContextMiddleware)

    # --- Error handling ----------------------------------------------------
    register_exception_handlers(app)

    # --- Routes ------------------------------------------------------------
    app.include_router(health_router)  # /health, /ready — unversioned
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", tags=["system"], summary="Service banner")
    async def root() -> dict[str, str]:
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "environment": settings.app_env.value,
            "docs": "/docs" if settings.docs_enabled else "disabled",
        }

    log.debug("app_created", routes=len(app.routes))
    return app


#: The ASGI application uvicorn imports.
app = create_app()
