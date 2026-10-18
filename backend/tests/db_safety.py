"""Fail-closed safety checks for the PostgreSQL *test* database.

Rules enforced here:
  * TEST_DATABASE_URL and DATABASE_URL must both be present.
  * Both must use the psycopg 3 driver and spell out host, port and database,
    with no query parameters that could silently change the target.
  * They must not identify the same server + database after normalisation
    (localhost / 127.0.0.1 / ::1 are treated as the same host).
  * The test database must be named exactly 'permission_aware_rag_test'
    and live on a local host.
  * After connecting, SELECT current_database() must return that same name.

This module never creates, drops, truncates, resets or migrates anything, and it
never puts a password or a full database URL into an error message or output.
The pure checks do no I/O, so they are unit-tested without a database.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from sqlalchemy import Engine, event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError

from app.config import ENV_FILE
from app.db.session import create_db_engine

EXPECTED_TEST_DB = "permission_aware_rag_test"
DEV_DATABASE_NAME = "permission_aware_rag"
REQUIRED_DRIVER = "postgresql+psycopg"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# Query parameters that could override the host/port/database/credentials that
# appear in the URL. With these present we could not know the real target.
FORBIDDEN_QUERY_KEYS = frozenset(
    {"host", "hostaddr", "port", "dbname", "database", "service", "options",
     "user", "password", "passfile"}
)

if EXPECTED_TEST_DB == DEV_DATABASE_NAME:  # pragma: no cover - constant sanity check
    raise RuntimeError("test and development database names must differ")


class DatabaseSafetyError(Exception):
    """A database safety rule failed. Messages never contain secrets or URLs."""


class MissingTestDatabaseError(DatabaseSafetyError):
    """The test database does not exist. The message explains the manual fix."""


@dataclass(frozen=True)
class DatabaseTarget:
    """Where tests will connect. Deliberately has no user name or password."""

    host: str
    port: int
    database: str


# --------------------------------------------------------------------- reading
def _lookup(mapping: Mapping[str, str | None], key: str) -> tuple[bool, str | None]:
    """Case-insensitive lookup, like pydantic-settings (case_sensitive=False)."""
    for name, value in mapping.items():
        if name.upper() == key:
            return True, value
    return False, None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def read_database_urls(
    environ: Mapping[str, str] | None = None,
    env_file: Path | str | None = None,
) -> tuple[str | None, str | None]:
    """Return (DATABASE_URL, TEST_DATABASE_URL) as the application would see them.

    Same precedence as pydantic-settings: a real environment variable beats the
    .env file, and a variable that is set-but-empty still beats it (so it counts
    as missing). Blank values become None. Nothing is printed.
    """
    env = os.environ if environ is None else environ
    path = ENV_FILE if env_file is None else env_file
    file_values = dotenv_values(dotenv_path=path, encoding="utf-8")  # {} if no file

    found: list[str | None] = []
    for key in ("DATABASE_URL", "TEST_DATABASE_URL"):
        present, value = _lookup(env, key)
        if not present:
            _, value = _lookup(file_values, key)
        found.append(_clean(value))
    return found[0], found[1]


# ----------------------------------------------------------------- pure checks
def _canonical_host(host: str) -> str:
    h = host.strip().lower().strip("[]")
    return "localhost" if h in LOCAL_HOSTS else h


def normalized_identity(url: URL) -> tuple[str, int, str]:
    """(host, port, database) with local-host aliases and letter case folded.

    The user name and password are ignored on purpose: a different login to the
    same database is still the same database.
    """
    return (
        _canonical_host(url.host or ""),
        int(url.port or 0),
        (url.database or "").lower(),
    )


def _parse_url(label: str, raw: str) -> URL:
    try:
        url = make_url(raw.strip())
    except Exception:  # never echo the raw value or the parser's message
        raise DatabaseSafetyError(f"{label} could not be parsed as a database URL") from None
    if url.drivername != REQUIRED_DRIVER:
        raise DatabaseSafetyError(f"{label} must use the '{REQUIRED_DRIVER}' driver")
    if not url.host or not url.port or not url.database:
        raise DatabaseSafetyError(
            f"{label} must explicitly include host, port and database name"
        )
    forbidden = sorted(k for k in url.query if k.lower() in FORBIDDEN_QUERY_KEYS)
    if forbidden:
        raise DatabaseSafetyError(
            f"{label} must not use connection-override query parameters ({', '.join(forbidden)})"
        )
    return url


def check_test_target(dev_url: str | None, test_url: str | None) -> DatabaseTarget:
    """Validate the URL pair. Raises DatabaseSafetyError on ANY doubt (fail closed)."""
    if test_url is None or not test_url.strip():
        raise DatabaseSafetyError(
            "TEST_DATABASE_URL is not set (or is empty); refusing to run database tests"
        )
    if dev_url is None or not dev_url.strip():
        raise DatabaseSafetyError(
            "DATABASE_URL is not set (or is empty); cannot confirm the test database "
            "is different from it"
        )

    test = _parse_url("TEST_DATABASE_URL", test_url)
    dev = _parse_url("DATABASE_URL", dev_url)

    if normalized_identity(test) == normalized_identity(dev):
        raise DatabaseSafetyError(
            "TEST_DATABASE_URL and DATABASE_URL point at the same database"
        )
    if test.database != EXPECTED_TEST_DB:
        # The exact-name rule also rules out the development database name.
        raise DatabaseSafetyError(
            f"TEST_DATABASE_URL must target the database named exactly '{EXPECTED_TEST_DB}'"
        )
    if dev.database == EXPECTED_TEST_DB:
        raise DatabaseSafetyError(
            "DATABASE_URL must not point at the test database name"
        )
    if _canonical_host(test.host or "") != "localhost":
        raise DatabaseSafetyError(
            "TEST_DATABASE_URL host must be local (localhost, 127.0.0.1 or ::1)"
        )
    return DatabaseTarget(host=(test.host or "").lower(), port=int(test.port or 0), database=test.database)


def describe_target(target: DatabaseTarget) -> str:
    """'host:port/database' - safe to print (no credentials)."""
    return f"{target.host}:{target.port}/{target.database}"


def missing_database_instructions() -> str:
    return (
        f"The test database '{EXPECTED_TEST_DB}' does not exist. Nothing was created.\n"
        "Create it ONCE, manually (replace 'rag' with your POSTGRES_USER if it differs):\n"
        f'  docker exec par-postgres psql -U rag -d postgres -c "CREATE DATABASE {EXPECTED_TEST_DB};"\n'
        "Then run the tests again."
    )


# ---------------------------------------------------------- connection checks
def verify_connected_database(connection: Any, expected: str = EXPECTED_TEST_DB) -> None:
    """Read-only: SELECT current_database() must equal the expected name."""
    actual = connection.execute(text("SELECT current_database()")).scalar_one()
    if actual != expected:
        raise DatabaseSafetyError(
            f"Connected to database '{actual}' but expected '{expected}'. Refusing to continue."
        )


def make_connect_verifier(expected: str):
    """Pool 'connect' listener: re-check the database of EVERY new connection."""

    def _verify(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SELECT current_database()")
            row = cursor.fetchone()
        finally:
            cursor.close()
            dbapi_connection.rollback()  # end the implicit transaction we started
        actual = row[0] if row else None
        if actual != expected:
            try:
                dbapi_connection.close()
            finally:
                raise DatabaseSafetyError(
                    f"New connection reached database '{actual}', expected '{expected}'"
                ) from None

    return _verify


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_FORBIDDEN_SQL = re.compile(
    r"\b(DROP\s+DATABASE|CREATE\s+DATABASE|DROP\s+SCHEMA|TRUNCATE)\b", re.IGNORECASE
)


def find_forbidden_statement(sql: str) -> str | None:
    """Return the forbidden keyword found in `sql`, or None.

    A tripwire against ACCIDENTS, not a security boundary: a deny-list cannot
    catch everything (e.g. dynamic SQL inside a function).
    """
    cleaned = _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", sql))
    match = _FORBIDDEN_SQL.search(cleaned)
    if match is None:
        return None
    return " ".join(match.group(1).upper().split())


def reject_forbidden_statement(
    conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
) -> None:
    """'before_cursor_execute' listener: refuse database-destroying statements."""
    found = find_forbidden_statement(statement)
    if found is not None:
        raise DatabaseSafetyError(f"Blocked statement in tests: {found}")


def build_verified_engine(dev_url: str | None, test_url: str | None) -> Engine:
    """Validate the URLs FIRST, then build the test engine. Does not connect.

    This is the only way the test suite builds an engine, so no engine can exist
    for a target that failed check_test_target.
    """
    target = check_test_target(dev_url, test_url)
    assert test_url is not None  # check_test_target guarantees this
    engine = create_db_engine(test_url.strip())
    event.listen(engine, "connect", make_connect_verifier(target.database))
    event.listen(engine, "before_cursor_execute", reject_forbidden_statement)
    return engine


_MISSING_DB_MESSAGE = re.compile(r'database\s+"[^"]*"\s+does not exist', re.IGNORECASE)


def is_missing_database_error(exc: BaseException) -> bool:
    """True if `exc` (or its .orig) means 'that database does not exist'."""
    for candidate in (exc, getattr(exc, "orig", None)):
        if candidate is None:
            continue
        if getattr(candidate, "sqlstate", None) == "3D000":
            return True
        if _MISSING_DB_MESSAGE.search(str(candidate)):
            return True
    return False


def connect_and_verify(engine: Any, expected: str = EXPECTED_TEST_DB) -> None:
    """Open one connection and confirm current_database(). Creates nothing."""
    try:
        with engine.connect() as connection:
            verify_connected_database(connection, expected)
    except DatabaseSafetyError:
        raise
    except DBAPIError as exc:
        if is_missing_database_error(exc):
            raise MissingTestDatabaseError(missing_database_instructions()) from None
        # Driver text can contain host/user details, so it is deliberately dropped.
        raise DatabaseSafetyError(
            "Could not connect to the test database. Check that the par-postgres "
            "container is running and that the host, port and credentials in "
            "TEST_DATABASE_URL are correct."
        ) from None


# ------------------------------------------------------------------------ CLI
def main(
    environ: Mapping[str, str] | None = None,
    env_file: Path | str | None = None,
) -> int:
    """`python -m tests.db_safety`: validate the configuration. NO connection."""
    dev_url, test_url = read_database_urls(environ, env_file)
    try:
        target = check_test_target(dev_url, test_url)
    except DatabaseSafetyError as exc:
        print(f"DB SAFETY CHECK FAILED: {exc}")
        return 1
    dev = make_url(dev_url or "")
    print("DB safety check passed (configuration only - no database connection was made).")
    print(f"  tests will use : {describe_target(target)}")
    print(f"  development DB : {dev.host}:{dev.port}/{dev.database}  (never used by tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())