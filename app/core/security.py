"""Token generation, hashing and JWT handling.

TWO DIFFERENT HASHES, ON PURPOSE
--------------------------------
**OTP codes → Argon2.** A 6-digit code has only a million possibilities. If the
database leaked, a fast hash (SHA-256) would let an attacker try all million in
under a second. Argon2 is deliberately slow and salted, so that attack becomes
impractical. We can afford the ~50ms because we only verify one code per
request.

**Refresh tokens → SHA-256.** These are 384 bits of randomness — brute force is
impossible regardless of hash speed. More importantly, we must *look up* a token
by its hash, and Argon2 uses a random salt per hash, so the same input produces
different output every time and can't be used in a WHERE clause. SHA-256 is
deterministic, so it can be indexed.

The general rule: **low-entropy secrets need slow hashes; high-entropy secrets
need deterministic ones.**
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import Settings
from app.core.exceptions import UnauthorizedError

_hasher = PasswordHasher()

_DIGITS = "0123456789"


# ---------------------------------------------------------------------------
# OTP codes
# ---------------------------------------------------------------------------
def generate_otp(length: int) -> str:
    """Generate a numeric OTP.

    Uses `secrets`, not `random`. `random` is a predictable pseudo-random
    generator — given a few outputs its entire future sequence can be derived.
    For anything security-related, always `secrets`.
    """
    return "".join(secrets.choice(_DIGITS) for _ in range(length))


def hash_otp(code: str) -> str:
    """Hash an OTP for storage. Never store the plain code."""
    return _hasher.hash(code)


def verify_otp(code: str, hashed: str) -> bool:
    """Check a submitted code against the stored hash."""
    try:
        return _hasher.verify(hashed, code)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def matches_dev_bypass(code: str, bypass_code: str) -> bool:
    """Check the development bypass code in constant time.

    `compare_digest` takes the same time whether the mismatch is at the first
    character or the last. A plain `==` returns faster on an early mismatch,
    which leaks information about the correct value one character at a time.
    """
    if not bypass_code:
        return False
    return secrets.compare_digest(code, bypass_code)


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------
def generate_refresh_token() -> str:
    """Generate an opaque refresh token (~384 bits of entropy)."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Deterministic hash, so the token can be looked up by hash.

    A database leak therefore exposes only hashes, not usable sessions.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Passwords (admin accounts, Phase 5)
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ---------------------------------------------------------------------------
# JWT access tokens
# ---------------------------------------------------------------------------
def create_access_token(
    *,
    user_id: uuid.UUID,
    roles: list[str],
    active_role: str,
    settings: Settings,
) -> tuple[str, int]:
    """Build a signed access token. Returns (token, seconds_until_expiry).

    Claim names follow the JWT standard so any library can read them:
      sub  subject — who this token is about
      iat  issued at
      exp  expires at
      jti  unique token id (lets us deny-list a single token later)

    `roles` and `active_role` are ours: the app uses `active_role` to decide
    which experience to show. Server-side authorisation still re-checks against
    the database — a token claim is a hint, never the source of truth.
    """
    now = datetime.now(UTC)
    expires_in = settings.access_token_ttl_minutes * 60
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "roles": roles,
        "active_role": active_role,
        "typ": "access",
        "jti": secrets.token_urlsafe(12),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
    }
    token = jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return token, expires_in


def decode_access_token(token: str, settings: Settings) -> dict[str, Any]:
    """Verify and decode an access token, or raise UnauthorizedError.

    Distinguishing "expired" from "invalid" matters to the mobile app: on
    TOKEN_EXPIRED it should silently refresh and retry; on TOKEN_INVALID it
    should log the user out.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("Your session has expired.", code="TOKEN_EXPIRED") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Invalid authentication token.", code="TOKEN_INVALID") from exc

    if claims.get("typ") != "access":
        # Stops a refresh token being presented as an access token.
        raise UnauthorizedError("Invalid authentication token.", code="TOKEN_INVALID")

    return claims


def utc_now() -> datetime:
    """Timezone-aware current time. Use this everywhere instead of utcnow()."""
    return datetime.now(UTC)
