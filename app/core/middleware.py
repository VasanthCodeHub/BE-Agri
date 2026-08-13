"""Middleware — code that runs on every request, before and after your route.

Two pieces here:

1. `RequestContextMiddleware` — gives every request a unique id, binds it to
   the logging context, times the request, and logs the outcome.
2. `SecurityHeadersMiddleware` — adds standard hardening headers to responses.

Why request ids matter: a single request may write ten log lines from
different modules. Without a shared id you cannot tell which lines belong
together under concurrent traffic. With one, you filter by it and see the
whole story — and the id is also returned in error responses, so a user's
screenshot leads you straight to the logs.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.logging import get_logger

log = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

#: Paths that would otherwise flood the logs (load balancers poll these).
_QUIET_PATHS = frozenset({"/health", "/ready", "/favicon.ico"})


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id, time the request, and log the result."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Reuse an incoming id if a proxy/mobile client sent one, so a single
        # user action can be traced across systems. Otherwise generate one.
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]

        # Available to exception handlers via request.state.
        request.state.request_id = request_id

        # Bind for the duration of this request: every log line emitted
        # anywhere downstream automatically includes these fields.
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # The exception handler builds the response; we just record timing.
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            log.exception("request_failed", duration_ms=duration_ms)
            raise

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id

        if request.url.path not in _QUIET_PATHS:
            log.info(
                "request_completed",
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response.

    Cheap, and they close off whole classes of attack:

    - `X-Content-Type-Options: nosniff` — stops browsers guessing a response
      is HTML/JS when we said it was JSON.
    - `X-Frame-Options: DENY` — no embedding our responses in an iframe.
    - `Referrer-Policy` — do not leak our URLs to third parties.
    - `Cache-Control: no-store` — API responses carry personal data and must
      not sit in intermediate caches.
    """

    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        if self.hsts:
            # Only meaningful over HTTPS, so it is enabled in production only.
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
