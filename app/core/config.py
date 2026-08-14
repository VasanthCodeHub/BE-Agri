"""Application configuration.

Every setting comes from an environment variable — nothing is hardcoded.
The same code runs in both environments; only the values change.

Two guarantees:
  1. A bad setting crashes the app at startup with a clear message, instead
     of failing mysteriously on some later request.
  2. Production refuses to start with a placeholder secret, DEBUG=true,
     or wildcard CORS.
"""

from __future__ import annotations

import re
from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    LOCAL = "local"  # your machine: development + testing, local DB
    PRODUCTION = "production"  # live users


class LogFormat(StrEnum):
    CONSOLE = "console"  # human-readable — local
    JSON = "json"  # machine-parseable — production


#: Secrets that must never reach production.
_PLACEHOLDER_SECRETS = frozenset(
    {
        "",
        "changeme",
        "change_me",
        "secret",
        "CHANGE_ME_generate_with_secrets_token_urlsafe",
    }
)

_MIN_SECRET_LENGTH = 32

#: SMS backends that exist. Anything else is a typo, and a typo that reaches
#: production means no user can log in.
_SMS_PROVIDERS = frozenset({"fake", "twilio"})

#: A Twilio auth token is exactly 32 hex characters.
_TWILIO_AUTH_TOKEN_PATTERN = re.compile(r"[0-9a-fA-F]{32}")


class Settings(BaseSettings):
    """Typed settings loaded from the environment.

    Locally these come from a git-ignored `.env` file. In production `.env`
    does not exist — real environment variables are injected by the host, and
    this same class reads them.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",  # explicit: Windows would default to cp1252
        extra="ignore",
        case_sensitive=False,
    )

    # --- Environment -------------------------------------------------------
    app_env: AppEnv = AppEnv.LOCAL
    app_name: str = "Agri Vehicle Rental API"
    app_version: str = "0.1.0"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    #: None = decide from app_env (see the `docs_enabled` property).
    enable_docs: bool | None = None

    # --- Logging -----------------------------------------------------------
    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE

    # --- Database ----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://agri:agri_local_password@localhost:5432/agri_local"
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: int = 30

    # --- Security ----------------------------------------------------------
    jwt_secret_key: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    #: Comma-separated; parsed by `cors_origins_list`.
    cors_origins: str = ""

    # --- Integrations ------------------------------------------------------
    #: "fake" needs no vendor account: the fake SMS provider logs the OTP to
    #: your terminal so you can log in locally, free, with no real messages.
    #: "twilio" sends a real SMS — production only, and it costs money per
    #: message, so it is never the default.
    sms_provider: str = "fake"
    #: "local" saves uploads to a folder on disk. Object storage comes later.
    storage_backend: str = "local"

    # --- Twilio ------------------------------------------------------------
    #: Only read when SMS_PROVIDER=twilio. Empty locally, which is why the
    #: fields have defaults: a developer with no Twilio account must still be
    #: able to boot the app.
    twilio_account_sid: str = ""
    twilio_auth_token: SecretStr = SecretStr("")
    #: The Twilio number the OTP is sent FROM, in E.164. This is the number you
    #: bought in the console under Phone Numbers > Manage > Active numbers.
    twilio_phone_number: str = ""
    #: Seconds to wait for Twilio before giving up. Kept short: the user is
    #: staring at a spinner, and a slow send is better failed than hung.
    twilio_timeout_seconds: float = 10.0

    #: The SMS wording. Configuration rather than a literal because Indian DLT
    #: rules require the delivered text to match a template registered with the
    #: operator character for character — see integrations/sms/twilio.py.
    #: `{code}` is required; `{minutes}` is optional.
    sms_otp_template: str = (
        "{code} is your verification code for Agri Vehicle Rental. "
        "It expires in {minutes} minutes. Do not share it with anyone."
    )

    # --- OTP policy --------------------------------------------------------
    #: 4 digits, matching what Indian users expect from most local apps. Only
    #: 10,000 possibilities, so OTP_MAX_ATTEMPTS below is what actually keeps
    #: guessing impractical — see the note in core/security.py.
    otp_length: int = 4
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_max_per_phone_per_hour: int = 3

    #: Development bypass: when set, this code logs in ANY phone number.
    #: Empty string disables it. The real OTP keeps working either way.
    #:
    #: This is an intentional, total authentication bypass, so it is guarded
    #: three ways: it is config-driven (never hardcoded), the app REFUSES TO
    #: START if it is set in production (see _enforce_production_rules), and
    #: every use is logged as a warning.
    otp_dev_bypass_code: str = ""

    # --- Search ------------------------------------------------------------
    default_search_radius_km: float = 25.0
    max_search_radius_km: float = 100.0

    # -----------------------------------------------------------------------
    # Derived values
    # -----------------------------------------------------------------------
    @property
    def is_local(self) -> bool:
        return self.app_env is AppEnv.LOCAL

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.PRODUCTION

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def docs_enabled(self) -> bool:
        """Serve /docs, /redoc and /openapi.json?

        On locally, off in production (the schema publishes your whole attack
        surface). An explicit ENABLE_DOCS overrides this.
        """
        if self.enable_docs is not None:
            return self.enable_docs
        return not self.is_production

    # -----------------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------------
    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}, got {value!r}")
        return upper

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        """Catch the most common first-run mistake.

        `postgresql://` picks the *synchronous* driver and then fails with a
        confusing error deep inside SQLAlchemy. Failing here saves an hour.
        """
        if not value.startswith("postgresql+asyncpg://"):
            scheme = value.split("://")[0] if "://" in value else value
            raise ValueError(
                "DATABASE_URL must use the async driver — it should start with "
                "'postgresql+asyncpg://', for example "
                "postgresql+asyncpg://agri:password@localhost:5432/agri_local "
                f"(got {scheme!r})"
            )
        return value

    @field_validator("sms_provider")
    @classmethod
    def _known_sms_provider(cls, value: str) -> str:
        provider = value.strip().lower()
        if provider not in _SMS_PROVIDERS:
            raise ValueError(f"SMS_PROVIDER must be one of {sorted(_SMS_PROVIDERS)}, got {value!r}")
        return provider

    @field_validator("sms_otp_template")
    @classmethod
    def _usable_sms_template(cls, value: str) -> str:
        """Fail at startup if the template cannot produce a message.

        Without this, a stray brace or a missing `{code}` only shows up as a
        crash — or worse, as an SMS with no code in it — on a real user's login.
        """
        if "{code}" not in value:
            raise ValueError("SMS_OTP_TEMPLATE must contain the placeholder {code}")
        try:
            value.format(code="0000", minutes=5)
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(
                "SMS_OTP_TEMPLATE is not a valid template. Only {code} and "
                f"{{minutes}} are available; literal braces must be doubled ({exc})"
            ) from exc
        return value

    @field_validator("otp_length")
    @classmethod
    def _sane_otp_length(cls, value: int) -> int:
        if not 4 <= value <= 8:
            raise ValueError(f"OTP_LENGTH must be between 4 and 8, got {value}")
        return value

    @model_validator(mode="after")
    def _require_twilio_credentials(self) -> Settings:
        """If Twilio is selected, demand everything it needs — in any environment.

        Checked at startup rather than on the first login attempt: a missing
        credential should crash the deploy, not silently break OTP delivery for
        every user until someone reads the logs.
        """
        if self.sms_provider != "twilio":
            return self

        missing: list[str] = []
        if not self.twilio_account_sid:
            missing.append("TWILIO_ACCOUNT_SID")
        if not self.twilio_auth_token.get_secret_value():
            missing.append("TWILIO_AUTH_TOKEN")
        if not self.twilio_phone_number:
            missing.append("TWILIO_PHONE_NUMBER")
        if missing:
            raise ValueError(
                "SMS_PROVIDER=twilio requires: " + ", ".join(missing) + ". "
                "Find the Account SID and Auth Token on the Twilio console dashboard."
            )

        if not self.twilio_account_sid.startswith("AC"):
            raise ValueError(
                "TWILIO_ACCOUNT_SID should start with 'AC' — a value starting "
                "with 'SK' is an API key, not the Account SID."
            )

        # Both values sit side by side in the Twilio console and both are 34/32
        # hex-ish characters, so copying the SID into both boxes is an easy slip.
        # Twilio's own answer is a bare "20003 Authenticate" on the first send,
        # which says nothing about the cause — so name it here instead.
        token = self.twilio_auth_token.get_secret_value()
        if token == self.twilio_account_sid or token.startswith("AC"):
            raise ValueError(
                "TWILIO_AUTH_TOKEN looks like the Account SID (it starts with "
                "'AC'). The auth token is a separate value: on the Twilio console "
                "dashboard, in the same Account Info panel, click 'Show' next to "
                "Auth Token to reveal it."
            )
        # A Twilio auth token is exactly 32 lowercase hex characters. Anything
        # else is a partial paste, a stray quote, or a different credential
        # entirely — all of which look identical to a wrong password from here.
        if not _TWILIO_AUTH_TOKEN_PATTERN.fullmatch(token):
            raise ValueError(
                "TWILIO_AUTH_TOKEN does not look like a Twilio auth token: it "
                "must be exactly 32 hexadecimal characters (0-9, a-f). Got "
                f"{len(token)} characters. Copy it from the Twilio console "
                "dashboard > Account Info > Auth Token > Show, with no quotes "
                "or spaces."
            )

        if not self.twilio_phone_number.startswith("+"):
            raise ValueError(
                "TWILIO_PHONE_NUMBER must be in E.164 form, e.g. +12025550123 "
                f"(got {self.twilio_phone_number!r})"
            )
        return self

    @model_validator(mode="after")
    def _enforce_production_rules(self) -> Settings:
        """Refuse to boot production with unsafe settings.

        Skipped locally so development stays frictionless.
        """
        if self.max_search_radius_km < self.default_search_radius_km:
            raise ValueError("MAX_SEARCH_RADIUS_KM must be >= DEFAULT_SEARCH_RADIUS_KM")

        if not self.is_production:
            return self

        secret = self.jwt_secret_key.get_secret_value()
        if secret in _PLACEHOLDER_SECRETS:
            raise ValueError(
                "JWT_SECRET_KEY is still a placeholder in production. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        if len(secret) < _MIN_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET_KEY must be at least {_MIN_SECRET_LENGTH} characters "
                f"in production (got {len(secret)})"
            )
        if self.debug:
            raise ValueError("DEBUG must be false in production — it can leak internals")
        if self.otp_dev_bypass_code:
            raise ValueError(
                "OTP_DEV_BYPASS_CODE must be empty in production. It is a complete "
                "authentication bypass — any phone number could be logged into with "
                "this code."
            )
        if self.sms_provider == "fake":
            raise ValueError(
                "SMS_PROVIDER=fake is not allowed in production — it logs the OTP "
                "instead of sending it, so nobody could log in. Set "
                "SMS_PROVIDER=twilio."
            )
        if "*" in self.cors_origins_list:
            raise ValueError("CORS_ORIGINS must list explicit origins in production, never '*'")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the settings singleton.

    Cached, so `.env` is parsed and validated once per process. This is also
    the FastAPI dependency for routes needing config, and tests can override
    it via `app.dependency_overrides`.
    """
    return Settings()
