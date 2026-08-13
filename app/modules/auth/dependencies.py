"""Authentication dependencies.

`get_current_user` is the piece you will reuse in every protected endpoint from
now on:

    @router.get("/something")
    async def handler(user: User = Depends(get_current_user)):
        ...

That one line means: reject the request unless it carries a valid access token,
otherwise hand me the actual user row. No auth code inside the handler.

Note it loads the user **from the database** rather than trusting the token's
contents. The token proves *who* the caller is; the database decides what is
currently true about them. Otherwise a user suspended five minutes ago would
keep working until their token expired.
"""

from __future__ import annotations

import uuid

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.db.session import get_db
from app.integrations.sms import get_sms_provider
from app.modules.auth.repository import AuthRepository
from app.modules.auth.service import AuthService
from app.modules.users.models import User, UserRole

#: Declaring this makes the "Authorize" button appear in Swagger: paste a token
#: once and every protected endpoint you try will include it.
#: auto_error=False so we can raise our own error envelope instead of FastAPI's.
bearer_scheme = HTTPBearer(auto_error=False, description="Paste your access token")


def get_auth_service(
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthService:
    """Assemble the service with its dependencies.

    This is where the wiring happens: the service receives a repository and an
    SMS provider and never constructs them itself, which is what makes it
    testable with fakes.
    """
    return AuthService(
        repo=AuthRepository(db),
        sms=get_sms_provider(settings),
        settings=settings,
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    """Resolve the caller from their access token, or raise 401."""
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError(
            "Authentication required. Send an Authorization: Bearer <token> header.",
            code="TOKEN_MISSING",
        )

    claims = decode_access_token(credentials.credentials, settings)

    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("Invalid authentication token.", code="TOKEN_INVALID") from exc

    user = await AuthRepository(db).get_user_by_id(user_id)
    if user is None:
        raise UnauthorizedError("Invalid authentication token.", code="TOKEN_INVALID")

    if not user.is_active:
        raise ForbiddenError(
            "This account has been suspended. Please contact support.",
            code="ACCOUNT_SUSPENDED",
        )

    return user


async def get_active_role(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> UserRole:
    """The role the caller logged in as, taken from their token."""
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("Authentication required.", code="TOKEN_MISSING")
    claims = decode_access_token(credentials.credentials, settings)
    try:
        return UserRole(claims["active_role"])
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("Invalid authentication token.", code="TOKEN_INVALID") from exc


def require_role(role: UserRole) -> object:
    """Build a dependency that demands a specific role.

    Used from Phase 2 onward, e.g. provider-only endpoints:

        @router.post("/provider/vehicles",
                     dependencies=[Depends(require_role(UserRole.PROVIDER))])

    Checked against the database, not just the token claim — a token is a hint,
    the database is the truth.

    IMPORTANT: holding a role is not the same as being authorised for a
    specific object. A provider must still be proven to own the vehicle they are
    editing; that check belongs in the service layer.
    """

    async def dependency(user: User = Depends(get_current_user)) -> User:
        if not user.has_role(role):
            raise ForbiddenError(
                f"This action requires the {role.value} role.",
                code="ROLE_REQUIRED",
                details={"required_role": role.value},
            )
        return user

    return dependency


def get_user_agent(request: Request) -> str | None:
    """The client's User-Agent, recorded against each session."""
    return request.headers.get("user-agent")
