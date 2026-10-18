"""Fail-closed validation of Alembic migration targets.

This module never opens a database connection, never executes SQL, and never
prints a database URL or credential. Alembic's env.py must call
``resolve_migration_target`` before building an engine.

Target selection is explicit: ``dev`` or ``test``. There is no default.
Both ``DATABASE_URL`` and ``TEST_DATABASE_URL`` are required so the selected
database can be proven distinct from the other.

These checks are configuration/identity validation, not a SQL security
boundary.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy.engine import URL, make_url

ALLOWED_TARGETS = frozenset({"dev", "test"})
DEV_DATABASE_NAME = "permission_aware_rag"
TEST_DATABASE_NAME = "permission_aware_rag_test"
REQUIRED_DRIVER = "postgresql+psycopg"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Query keys that could silently retarget the connection.
FORBIDDEN_QUERY_KEYS = frozenset(
    {
        "host",
        "hostaddr",
        "port",
        "dbname",
        "database",
        "service",
        "options",
        "user",
        "password",
        "passfile",
    }
)

_URL_LABEL = {
    "dev": "DATABASE_URL",
    "test": "TEST_DATABASE_URL",
}

if DEV_DATABASE_NAME == TEST_DATABASE_NAME:  # pragma: no cover
    raise RuntimeError("development and test database names must differ")


class MigrationSafetyError(Exception):
    """A migration target rule failed. Messages never contain secrets or URLs."""


@dataclass(frozen=True)
class ResolvedMigrationTarget:
    """Where migrations would run. Credentials are not included in repr."""

    target: str
    host: str
    port: int
    database: str
    sqlalchemy_url: str = field(repr=False, compare=False)


def require_target(raw: str | None) -> str:
    """Return ``dev`` or ``test``. Missing or unknown values are errors."""
    if raw is None or not str(raw).strip():
        raise MigrationSafetyError(
            "Migration target is required. Pass -x target=dev or -x target=test."
        )
    value = str(raw).strip().lower()
    if value not in ALLOWED_TARGETS:
        raise MigrationSafetyError(
            f"Unknown migration target {value!r}. Allowed values: 'dev', 'test'."
        )
    return value


def target_from_x_arguments(x_arguments: Mapping[str, str] | None) -> str:
    """Read Alembic ``-x target=...`` (as_dictionary=True). No implicit default."""
    if not x_arguments:
        return require_target(None)
    return require_target(x_arguments.get("target"))


def expected_database_name(target: str) -> str:
    name = require_target(target)
    if name == "dev":
        return DEV_DATABASE_NAME
    return TEST_DATABASE_NAME


def describe_target(resolved: ResolvedMigrationTarget) -> str:
    """Safe to print: host:port/database (no credentials)."""
    return f"{resolved.host}:{resolved.port}/{resolved.database}"


def _canonical_host(host: str) -> str:
    folded = host.strip().lower().strip("[]")
    return "localhost" if folded in LOCAL_HOSTS else folded


def _normalized_identity(url: URL) -> tuple[str, int, str]:
    return (
        _canonical_host(url.host or ""),
        int(url.port or 0),
        (url.database or "").lower(),
    )


def _parse_url(label: str, raw: str | None) -> URL:
    if raw is None or not raw.strip():
        raise MigrationSafetyError(
            f"{label} is not set (or is empty); refusing to select a migration target"
        )
    try:
        url = make_url(raw.strip())
    except Exception:
        raise MigrationSafetyError(f"{label} could not be parsed as a database URL") from None
    if url.drivername != REQUIRED_DRIVER:
        raise MigrationSafetyError(f"{label} must use the '{REQUIRED_DRIVER}' driver")
    if not url.host or not url.port or not url.database:
        raise MigrationSafetyError(
            f"{label} must explicitly include host, port and database name"
        )
    forbidden = sorted(key for key in url.query if key.lower() in FORBIDDEN_QUERY_KEYS)
    if forbidden:
        raise MigrationSafetyError(
            f"{label} must not use connection-override query parameters "
            f"({', '.join(forbidden)})"
        )
    return url


def resolve_migration_target(
    target: str,
    *,
    database_url: str | None,
    test_database_url: str | None,
) -> ResolvedMigrationTarget:
    """Validate the target and URL pair. Does not connect and does not execute SQL."""
    selected = require_target(target)
    other = "test" if selected == "dev" else "dev"

    selected_label = _URL_LABEL[selected]
    other_label = _URL_LABEL[other]
    selected_raw = database_url if selected == "dev" else test_database_url
    other_raw = test_database_url if selected == "dev" else database_url

    selected_url = _parse_url(selected_label, selected_raw)
    other_url = _parse_url(other_label, other_raw)

    if _normalized_identity(selected_url) == _normalized_identity(other_url):
        raise MigrationSafetyError(
            "DATABASE_URL and TEST_DATABASE_URL point at the same database"
        )

    expected = expected_database_name(selected)
    other_expected = expected_database_name(other)
    if selected_url.database != expected:
        raise MigrationSafetyError(
            f"{selected_label} must target the database named exactly '{expected}' "
            f"for migration target '{selected}'"
        )
    if other_url.database != other_expected:
        raise MigrationSafetyError(
            f"{other_label} must target the database named exactly '{other_expected}'"
        )
    if _canonical_host(selected_url.host or "") != "localhost":
        raise MigrationSafetyError(
            f"{selected_label} host must be local (localhost, 127.0.0.1 or ::1)"
        )
    if _canonical_host(other_url.host or "") != "localhost":
        raise MigrationSafetyError(
            f"{other_label} host must be local (localhost, 127.0.0.1 or ::1)"
        )

    # selected_raw is non-empty: _parse_url already rejected None/blank.
    assert selected_raw is not None
    return ResolvedMigrationTarget(
        target=selected,
        host=(selected_url.host or "").lower(),
        port=int(selected_url.port or 0),
        database=selected_url.database,
        sqlalchemy_url=selected_raw.strip(),
    )
