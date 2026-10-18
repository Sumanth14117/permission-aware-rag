"""Centralised application configuration.

All settings are read from environment variables, or from the .env file at the
repository root. Nothing else in the codebase should read os.environ directly.

Security notes
--------------
* Secrets and database URLs are SecretStr, so printing or logging a Settings
  object shows '**********' instead of the real value.
* hide_input_in_errors=True stops pydantic validation errors from echoing the
  offending value (which could be a secret) in startup tracebacks.
* This module never connects to a database and never prints anything.
"""

from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py lives at <repo>/backend/app/config.py, so:
#   parents[0] = app, parents[1] = backend, parents[2] = repository root.
# Resolving relative to this file means the .env is found no matter which
# directory uvicorn, alembic or pytest is started from.
REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"

# Only the psycopg 3 driver is installed. A plain "postgresql://" URL would make
# SQLAlchemy look for psycopg2, so we require the explicit driver name.
_REQUIRED_DB_PREFIX = "postgresql+psycopg://"

# Cookie names end up in HTTP headers, so keep them to safe token characters.
_COOKIE_NAME_PATTERN = r"^[A-Za-z0-9_-]+$"

_PLACEHOLDER_SECRET_PREFIX = "change-me"
_MIN_SECRET_LENGTH = 16
_MIN_SECRET_LENGTH_SECURE = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        # .env also holds Qdrant, embedding and LLM settings for later
        # milestones. Ignore anything this milestone doesn't declare.
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    # --- Database -----------------------------------------------------------
    # No defaults: if these are missing we want a loud failure, not a guess.
    database_url: SecretStr
    # Only tests need this; the safety module (tests/db_safety.py) enforces that
    # it is present and distinct from database_url before any test connects.
    test_database_url: SecretStr | None = None

    # --- Sessions and cookies ----------------------------------------------
    session_cookie_name: str = Field(default="par_session", pattern=_COOKIE_NAME_PATTERN)
    session_ttl_hours: int = Field(default=24, ge=1, le=720)
    # Secure-by-default: a missing COOKIE_SECURE must not silently weaken cookies.
    cookie_secure: bool = True

    # --- CORS / Origin allowlist -------------------------------------------
    # Kept as the raw comma-separated string (as written in .env) and exposed
    # as a tuple via cors_origin_list. A list[str] field would make
    # pydantic-settings expect JSON instead of comma-separated values.
    cors_origins: str

    # --- CSRF ---------------------------------------------------------------
    csrf_cookie_name: str = Field(default="par_csrf", pattern=_COOKIE_NAME_PATTERN)
    csrf_secret: SecretStr

    # ------------------------------------------------------------------ validators
    @field_validator("test_database_url", mode="before")
    @classmethod
    def _empty_test_url_is_none(cls, value: object) -> object:
        # "TEST_DATABASE_URL=" in .env arrives as "". Treat it as "not set" so
        # the test safeguards report it as missing.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("database_url", "test_database_url")
    @classmethod
    def _require_psycopg_scheme(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().startswith(_REQUIRED_DB_PREFIX):
            # Deliberately do not include the value in the message.
            raise ValueError("database URL must start with 'postgresql+psycopg://'")
        return value

    @field_validator("cors_origins")
    @classmethod
    def _canonicalise_origins(cls, value: str) -> str:
        """Validate and normalise the Origin allowlist (exact origins only)."""
        origins: list[str] = []
        for raw in value.split(","):
            item = raw.strip()
            if not item:
                continue
            if item == "*" or "*" in item:
                raise ValueError("wildcard origins are not allowed in CORS_ORIGINS")
            parts = urlsplit(item)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError("each CORS origin must look like http(s)://host[:port]")
            if parts.username or parts.password or parts.query or parts.fragment:
                raise ValueError("CORS origins must not contain credentials, query or fragment")
            if parts.path not in ("", "/"):
                raise ValueError("CORS origins must not contain a path")
            # Canonical origin form: lowercase scheme://host[:port], no trailing slash.
            origins.append(f"{parts.scheme.lower()}://{parts.netloc.lower()}")
        if not origins:
            raise ValueError("CORS_ORIGINS must contain at least one origin")
        # De-duplicate while keeping order.
        return ",".join(dict.fromkeys(origins))

    @model_validator(mode="after")
    def _check_cross_field_rules(self) -> "Settings":
        if self.session_cookie_name == self.csrf_cookie_name:
            raise ValueError("session and CSRF cookie names must be different")

        secret = self.csrf_secret.get_secret_value()
        if len(secret) < _MIN_SECRET_LENGTH:
            raise ValueError("CSRF_SECRET is too short")
        # Production-like mode (Secure cookies) must not run with a weak or
        # placeholder secret. Local HTTP development may use the placeholder.
        if self.cookie_secure and (
            len(secret) < _MIN_SECRET_LENGTH_SECURE
            or secret.lower().startswith(_PLACEHOLDER_SECRET_PREFIX)
        ):
            raise ValueError(
                "CSRF_SECRET must be a long random value (32+ chars, not the "
                "placeholder) when COOKIE_SECURE is true"
            )
        return self

    # ------------------------------------------------------------------ helpers
    @property
    def cors_origin_list(self) -> tuple[str, ...]:
        """The canonical allowed origins, e.g. ('http://localhost:5173', ...)."""
        return tuple(self.cors_origins.split(","))

    @property
    def session_ttl(self) -> timedelta:
        return timedelta(hours=self.session_ttl_hours)


@lru_cache
def get_settings() -> Settings:
    """Load settings once per process.

    Tests should construct Settings(...) directly with explicit values (and
    _env_file=None) instead of relying on the developer's real .env.
    """
    return Settings()