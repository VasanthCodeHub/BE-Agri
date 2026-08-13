"""Application errors and the single error response shape.

Every error the API returns looks the same:

    {
      "error": {
        "code": "PROVIDER_NOT_VERIFIED",
        "message": "This provider has not completed verification.",
        "details": {},
        "request_id": "0f9c1e4a2b"
      }
    }

Why a machine-readable `code` matters: the Flutter app can branch on
`code == "OTP_EXPIRED"` reliably. If it had to match on the English message,
every wording tweak would break the app.

`request_id` matters because a user can screenshot an error and you can find
the exact request in the logs.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Exception types — raise these from the service layer
# ---------------------------------------------------------------------------
class AppError(Exception):
    """Base class for every expected error.

    Services raise these; the handlers below turn them into the response
    envelope. Business logic never builds an HTTP response itself.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "INTERNAL_ERROR"
    message: str = "Something went wrong."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        if code:
            self.code = code
        super().__init__(self.message)


class BadRequestError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "BAD_REQUEST"
    message = "The request was invalid."


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHORIZED"
    message = "Authentication is required."


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"
    message = "You do not have permission to do that."


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"
    message = "The requested resource was not found."


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"
    message = "That conflicts with the current state."


class RateLimitedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "RATE_LIMITED"
    message = "Too many requests. Please try again later."


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "SERVICE_UNAVAILABLE"
    message = "A required service is temporarily unavailable."


# ---------------------------------------------------------------------------
# Response building
# ---------------------------------------------------------------------------
def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else None


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": request_id,
            }
        },
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def app_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle our own AppError subclasses."""
    assert isinstance(exc, AppError)
    log.warning(
        "app_error",
        code=exc.code,
        status_code=exc.status_code,
        path=request.url.path,
        detail=exc.message,
    )
    return error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        request_id=_request_id(request),
        details=exc.details,
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle Pydantic request-validation failures (bad/missing fields).

    FastAPI's default 422 body has its own shape. We reshape it into our
    envelope so clients only ever parse one format.
    """
    assert isinstance(exc, RequestValidationError)
    fields = [
        {
            "field": ".".join(str(part) for part in err["loc"][1:]) or str(err["loc"][0]),
            "reason": err["msg"],
        }
        for err in exc.errors()
    ]
    log.info("validation_error", path=request.url.path, fields=fields)
    return error_response(
        # 422. Newer Starlette renamed the constant to ..._CONTENT; the literal
        # avoids a deprecation warning while working on either version.
        status_code=422,
        code="VALIDATION_ERROR",
        message="One or more fields are invalid.",
        request_id=_request_id(request),
        details={"fields": fields},
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle plain HTTPException (including 404s from unmatched routes)."""
    assert isinstance(exc, StarletteHTTPException)
    codes = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        429: "RATE_LIMITED",
    }
    return error_response(
        status_code=exc.status_code,
        code=codes.get(exc.status_code, "HTTP_ERROR"),
        message=str(exc.detail),
        request_id=_request_id(request),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for bugs.

    The full traceback goes to the logs; the client gets a generic message.
    Never leak internals (file paths, SQL, variable values) to a caller —
    that is free reconnaissance for an attacker.
    """
    log.exception("unhandled_error", path=request.url.path, error_type=type(exc).__name__)
    return error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code="INTERNAL_ERROR",
        message="Something went wrong. Please try again.",
        request_id=_request_id(request),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire all handlers onto the app. Called from the app factory."""
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
