"""Unit tests for app.db.migration_safety.

No database connection and no network access: every check is pure URL/target
logic against in-memory strings. Alembic CLI is not invoked.
"""

import ast
from pathlib import Path

import pytest

from app.db.migration_safety import (
    DEV_DATABASE_NAME,
    TEST_DATABASE_NAME,
    MigrationSafetyError,
    describe_target,
    require_target,
    resolve_migration_target,
    target_from_x_arguments,
)

SECRET = "S3cretPW-do-not-leak"
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def url(
    *,
    driver="postgresql+psycopg",
    user="rag",
    password=SECRET,
    host="localhost",
    port="5432",
    database="permission_aware_rag",
    query="",
):
    auth = f"{user}:{password}@" if user else ""
    hostport = f"{host}:{port}" if port else host
    path = f"/{database}" if database is not None else ""
    return f"{driver}://{auth}{hostport}{path}{query}"


DEV = url(database=DEV_DATABASE_NAME)
TEST = url(database=TEST_DATABASE_NAME)


def _assert_redacted(exc: BaseException) -> None:
    message = str(exc)
    assert SECRET not in message
    assert "://" not in message


def test_missing_target_rejected():
    with pytest.raises(MigrationSafetyError) as info:
        require_target(None)
    assert "required" in str(info.value).lower()
    _assert_redacted(info.value)


def test_blank_target_rejected():
    with pytest.raises(MigrationSafetyError):
        require_target("   ")


def test_unknown_target_rejected():
    with pytest.raises(MigrationSafetyError) as info:
        require_target("staging")
    assert "unknown" in str(info.value).lower()
    _assert_redacted(info.value)


def test_missing_target_in_x_arguments_rejected():
    with pytest.raises(MigrationSafetyError) as info:
        target_from_x_arguments({})
    assert "required" in str(info.value).lower()
    with pytest.raises(MigrationSafetyError):
        target_from_x_arguments(None)


def test_x_arguments_accepts_explicit_targets():
    assert target_from_x_arguments({"target": "dev"}) == "dev"
    assert target_from_x_arguments({"target": "TEST"}) == "test"


def test_missing_url_rejected():
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target("dev", database_url=None, test_database_url=TEST)
    assert "DATABASE_URL" in str(info.value)
    _assert_redacted(info.value)

    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target("test", database_url=DEV, test_database_url="")
    assert "TEST_DATABASE_URL" in str(info.value)
    _assert_redacted(info.value)


def test_wrong_database_name_rejected():
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "dev",
            database_url=url(database="wrong_dev"),
            test_database_url=TEST,
        )
    assert DEV_DATABASE_NAME in str(info.value)
    _assert_redacted(info.value)

    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "test",
            database_url=DEV,
            test_database_url=url(database="wrong_test"),
        )
    assert TEST_DATABASE_NAME in str(info.value)
    _assert_redacted(info.value)


def test_cross_use_of_names_rejected():
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "dev",
            database_url=url(database=TEST_DATABASE_NAME),
            test_database_url=TEST,
        )
    _assert_redacted(info.value)

    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "test",
            database_url=DEV,
            test_database_url=url(database=DEV_DATABASE_NAME),
        )
    _assert_redacted(info.value)


def test_identical_development_and_test_urls_rejected():
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target("dev", database_url=DEV, test_database_url=DEV)
    assert "same database" in str(info.value)
    _assert_redacted(info.value)


def test_localhost_aliases_still_count_as_the_same_database():
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "dev",
            database_url=url(host="127.0.0.1", database=DEV_DATABASE_NAME),
            test_database_url=url(host="localhost", database=DEV_DATABASE_NAME),
        )
    assert "same database" in str(info.value)
    _assert_redacted(info.value)


def test_non_local_hostname_rejected():
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "dev",
            database_url=url(host="db.example.com", database=DEV_DATABASE_NAME),
            test_database_url=TEST,
        )
    assert "local" in str(info.value).lower()
    _assert_redacted(info.value)


def test_missing_port_rejected():
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "dev",
            database_url=url(port="", database=DEV_DATABASE_NAME),
            test_database_url=TEST,
        )
    assert "port" in str(info.value).lower()
    _assert_redacted(info.value)


def test_valid_development_target_accepted():
    resolved = resolve_migration_target(
        "dev", database_url=DEV, test_database_url=TEST
    )
    assert resolved.target == "dev"
    assert resolved.database == DEV_DATABASE_NAME
    assert resolved.port == 5432
    assert SECRET not in repr(resolved)
    assert SECRET not in describe_target(resolved)
    assert "://" not in describe_target(resolved)


def test_valid_test_target_accepted():
    resolved = resolve_migration_target(
        "test", database_url=DEV, test_database_url=TEST
    )
    assert resolved.target == "test"
    assert resolved.database == TEST_DATABASE_NAME
    assert describe_target(resolved) == f"localhost:5432/{TEST_DATABASE_NAME}"


def test_local_host_aliases_accepted_for_selected_target():
    resolved = resolve_migration_target(
        "dev",
        database_url=url(host="127.0.0.1", database=DEV_DATABASE_NAME),
        test_database_url=TEST,
    )
    assert resolved.database == DEV_DATABASE_NAME


def test_error_messages_redact_credentials():
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target("dev", database_url=DEV, test_database_url=DEV)
    _assert_redacted(info.value)


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_production_code_does_not_import_tests_package():
    safety = BACKEND_ROOT / "app" / "db" / "migration_safety.py"
    env_py = BACKEND_ROOT / "alembic" / "env.py"
    for path in (safety, env_py):
        for name in _imported_modules(path):
            assert not name.startswith("tests"), f"{path} imports {name}"


@pytest.mark.parametrize(
    "query",
    [
        "?host=evil.example.com",
        "?dbname=other",
        "?port=5433",
        "?options=-csearch_path%3Dpublic",
        "?service=prod",
    ],
)
def test_forbidden_query_parameters_rejected(query):
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "dev",
            database_url=url(query=query),
            test_database_url=TEST,
        )
    assert "connection-override query parameters" in str(info.value)
    _assert_redacted(info.value)

    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "dev",
            database_url=DEV,
            test_database_url=url(database=TEST_DATABASE_NAME, query=query),
        )
    assert "connection-override query parameters" in str(info.value)
    _assert_redacted(info.value)


@pytest.mark.parametrize(
    "driver",
    [
        "postgresql",
        "postgresql+psycopg2",
        "sqlite",
        "postgresql+asyncpg",
    ],
)
def test_unsupported_drivers_rejected(driver):
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "dev",
            database_url=url(driver=driver),
            test_database_url=TEST,
        )
    assert "driver" in str(info.value).lower()
    _assert_redacted(info.value)

    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "test",
            database_url=DEV,
            test_database_url=url(driver=driver, database=TEST_DATABASE_NAME),
        )
    assert "driver" in str(info.value).lower()
    _assert_redacted(info.value)


@pytest.mark.parametrize(
    "bad_url",
    [
        "not-a-valid-url",
        "://missing-scheme",
        f"postgresql+psycopg://rag:{SECRET}@localhost:not_a_port/permission_aware_rag",
    ],
)
def test_malformed_url_rejected_without_leaking_secrets(bad_url):
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "dev",
            database_url=bad_url,
            test_database_url=TEST,
        )
    assert "could not be parsed" in str(info.value).lower()
    _assert_redacted(info.value)


def test_missing_host_rejected():
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "dev",
            database_url=url(host=""),
            test_database_url=TEST,
        )
    assert "explicitly include host, port and database name" in str(info.value)
    _assert_redacted(info.value)

    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "test",
            database_url=DEV,
            test_database_url=url(host="", database=TEST_DATABASE_NAME),
        )
    assert "explicitly include host, port and database name" in str(info.value)
    _assert_redacted(info.value)


def test_missing_database_name_rejected():
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "dev",
            database_url=url(database=None),
            test_database_url=TEST,
        )
    assert "explicitly include host, port and database name" in str(info.value)
    _assert_redacted(info.value)

    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "test",
            database_url=DEV,
            test_database_url=url(database=None),
        )
    assert "explicitly include host, port and database name" in str(info.value)
    _assert_redacted(info.value)


def test_non_local_hostname_rejected_for_target_test():
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "test",
            database_url=DEV,
            test_database_url=url(host="db.example.com", database=TEST_DATABASE_NAME),
        )
    assert "local" in str(info.value).lower()
    _assert_redacted(info.value)


def test_non_local_unselected_host_rejected():
    # Target is dev, but test URL has non-local host
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "dev",
            database_url=DEV,
            test_database_url=url(host="remote.example.com", database=TEST_DATABASE_NAME),
        )
    assert "TEST_DATABASE_URL host must be local" in str(info.value)
    _assert_redacted(info.value)

    # Target is test, but dev URL has non-local host
    with pytest.raises(MigrationSafetyError) as info:
        resolve_migration_target(
            "test",
            database_url=url(host="remote.example.com", database=DEV_DATABASE_NAME),
            test_database_url=TEST,
        )
    assert "DATABASE_URL host must be local" in str(info.value)
    _assert_redacted(info.value)


def test_ipv6_loopback_accepted():
    resolved_bracketed = resolve_migration_target(
        "dev",
        database_url=url(host="[::1]", database=DEV_DATABASE_NAME),
        test_database_url=TEST,
    )
    assert resolved_bracketed.database == DEV_DATABASE_NAME
    assert resolved_bracketed.target == "dev"

