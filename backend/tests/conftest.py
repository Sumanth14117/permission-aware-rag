"""Pytest configuration for the whole suite.

Three layers keep the test run away from the development database:

  A. Environment guard (runs when this file is imported; opens no connection).
     Inside the pytest process, DATABASE_URL is overwritten with the verified
     TEST URL, or with a deliberately unreachable URL if the safety checks
     fail. Real environment variables beat .env in app.config, so the
     application's own settings can no longer reach the development database
     during tests. Your .env file and your shell are not changed.

  B. `verified_test_engine` is the ONLY route to a database. It re-runs the
     checks, builds the engine, confirms SELECT current_database(), and
     aborts the whole session (pytest.exit) on any problem. It never creates
     or migrates anything. Tests marked `db` must use it and are skipped
     unless pytest is started with --run-db.

  C. An autouse fixture makes the application's get_engine() /
     get_session_factory() raise, so an accidental use of DATABASE_URL fails
     loudly instead of connecting.
"""

import os

import pytest

from app.config import get_settings
from tests.db_safety import (
    EXPECTED_TEST_DB,
    DatabaseSafetyError,
    build_verified_engine,
    check_test_target,
    connect_and_verify,
    read_database_urls,
)

# Fixed values so results do not depend on the developer's real secrets.
_TEST_SETTINGS = {
    "CSRF_SECRET": "test-only-csrf-secret-never-used-outside-pytest-0123456789",
    "CORS_ORIGINS": "http://localhost:5173,http://localhost:8000",
    "COOKIE_SECURE": "false",
    "SESSION_TTL_HOURS": "24",
    "SESSION_COOKIE_NAME": "par_session",
    "CSRF_COOKIE_NAME": "par_csrf",
}

# Port 1 on loopback refuses connections immediately. Used only when the safety
# checks fail, so that accidental use of the application settings fails harmlessly.
_UNREACHABLE_URL = "postgresql+psycopg://nobody:nopass@127.0.0.1:1/permission_aware_rag_unreachable"


def _install_test_environment() -> tuple[str | None, str | None, str | None]:
    dev_url, test_url = read_database_urls()
    failure: str | None = None
    try:
        check_test_target(dev_url, test_url)
    except DatabaseSafetyError as exc:
        failure = str(exc)

    os.environ["DATABASE_URL"] = test_url if (failure is None and test_url) else _UNREACHABLE_URL
    os.environ.update(_TEST_SETTINGS)
    get_settings.cache_clear()
    return dev_url, test_url, failure


# Captured BEFORE DATABASE_URL is overwritten; used only by verified_test_engine.
_DEV_URL, _TEST_URL, _SAFETY_FAILURE = _install_test_environment()


# ------------------------------------------------------------------ pytest hooks
def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-db",
        action="store_true",
        default=False,
        help="Run tests marked 'db' against the verified test database (opt-in).",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "db: needs the verified PostgreSQL test database (skipped unless --run-db is given)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_db = config.getoption("--run-db")
    for item in items:
        if item.get_closest_marker("db") is None:
            continue
        if "verified_test_engine" not in getattr(item, "fixturenames", ()):
            raise pytest.UsageError(
                f"{item.nodeid} is marked 'db' but does not use the 'verified_test_engine' fixture"
            )
        if not run_db:
            item.add_marker(pytest.mark.skip(reason="database tests are opt-in: pass --run-db"))


# --------------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def _forbid_application_engine(monkeypatch: pytest.MonkeyPatch):
    """Layer C: the application's own engine factories must never run in tests."""

    def _blocked(*args, **kwargs):
        raise DatabaseSafetyError(
            "The application engine is forbidden in tests; use the verified_test_engine fixture"
        )

    monkeypatch.setattr("app.db.session.get_engine", _blocked)
    monkeypatch.setattr("app.db.session.get_session_factory", _blocked)


@pytest.fixture(scope="session")
def verified_test_engine():
    """Layer B: a verified engine for permission_aware_rag_test (and nothing else)."""
    if _SAFETY_FAILURE is not None:
        pytest.exit(f"Refusing to run database tests. {_SAFETY_FAILURE}", returncode=3)

    try:
        engine = build_verified_engine(_DEV_URL, _TEST_URL)
    except DatabaseSafetyError as exc:
        pytest.exit(f"Refusing to run database tests. {exc}", returncode=3)

    try:
        connect_and_verify(engine, EXPECTED_TEST_DB)
    except DatabaseSafetyError as exc:  # includes MissingTestDatabaseError
        engine.dispose()
        pytest.exit(f"Refusing to run database tests. {exc}", returncode=3)

    try:
        yield engine
    finally:
        engine.dispose()