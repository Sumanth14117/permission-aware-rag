"""Unit tests for tests/db_safety.py and the conftest guard.

NO database connection and NO network access is needed or made: every check is
either pure string/URL logic or runs against in-memory fakes. (One test builds
an SQLAlchemy engine object, which is lazy and does not connect.)
"""

import os

import pytest
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.config import get_settings
from tests import db_safety
from tests.db_safety import (
    DEV_DATABASE_NAME,
    EXPECTED_TEST_DB,
    DatabaseSafetyError,
    DatabaseTarget,
    MissingTestDatabaseError,
    build_verified_engine,
    check_test_target,
    connect_and_verify,
    describe_target,
    find_forbidden_statement,
    is_missing_database_error,
    make_connect_verifier,
    missing_database_instructions,
    normalized_identity,
    read_database_urls,
    reject_forbidden_statement,
    verify_connected_database,
)

SECRET = "S3cretPW-do-not-leak"


def url(*, driver="postgresql+psycopg", user="rag", password=SECRET, host="localhost",
        port="5432", database="permission_aware_rag_test", query=""):
    auth = f"{user}:{password}@" if user else ""
    hostport = f"{host}:{port}" if port else host
    path = f"/{database}" if database is not None else ""
    return f"{driver}://{auth}{hostport}{path}{query}"


DEV = url(database="permission_aware_rag")
TEST = url()


# ------------------------------------------------------------- check_test_target
def test_valid_pair_is_accepted():
    assert check_test_target(DEV, TEST) == DatabaseTarget("localhost", 5432, EXPECTED_TEST_DB)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]", "LOCALHOST"])
def test_local_hosts_are_accepted(host):
    assert check_test_target(DEV, url(host=host)).database == EXPECTED_TEST_DB


REJECTED = [
    pytest.param(DEV, None, "is not set", id="test-url-missing"),
    pytest.param(DEV, "", "is not set", id="test-url-empty"),
    pytest.param(DEV, "   ", "is not set", id="test-url-blank"),
    pytest.param(None, TEST, "is not set", id="dev-url-missing"),
    pytest.param("", TEST, "is not set", id="dev-url-empty"),
    pytest.param(DEV, "not a url at all", "could not be parsed", id="test-url-unparseable"),
    pytest.param(DEV, url(driver="postgresql"), "driver", id="driver-plain-postgresql"),
    pytest.param(DEV, url(driver="postgresql+psycopg2"), "driver", id="driver-psycopg2"),
    pytest.param(DEV, url(host=""), "explicitly include", id="no-host"),
    pytest.param(DEV, url(port=""), "explicitly include", id="no-port"),
    pytest.param(DEV, url(database=None), "explicitly include", id="no-database"),
    pytest.param(DEV, url(query="?host=evil.example.com"), "query parameters", id="query-host"),
    pytest.param(DEV, url(query="?dbname=permission_aware_rag"), "query parameters", id="query-dbname"),
    pytest.param(DEV, url(query="?options=-csearch_path%3Dpublic"), "query parameters", id="query-options"),
    pytest.param(url(query="?service=prod"), TEST, "query parameters", id="dev-url-query-override"),
    pytest.param(DEV, DEV, "same database", id="identical-urls"),
    pytest.param(DEV, url(host="127.0.0.1", database="permission_aware_rag"), "same database", id="localhost-vs-127"),
    pytest.param(DEV, url(host="[::1]", database="permission_aware_rag"), "same database", id="localhost-vs-ipv6"),
    pytest.param(DEV, url(user="other", password="x", database="permission_aware_rag"), "same database", id="different-credentials"),
    pytest.param(DEV, url(database="Permission_Aware_Rag"), "same database", id="letter-case-differs"),
    pytest.param(DEV, url(database="permission_aware_rag_test2"), "named exactly", id="wrong-name-suffix"),
    pytest.param(DEV, url(database="PERMISSION_AWARE_RAG_TEST"), "named exactly", id="wrong-name-case"),
    pytest.param(url(port="5433", database="permission_aware_rag"),
                 url(database="permission_aware_rag"), "named exactly", id="test-url-uses-dev-name"),
    pytest.param(url(port="5433", database=EXPECTED_TEST_DB), TEST,
                 "must not point at the test database", id="dev-url-uses-test-name"),
    pytest.param(DEV, url(host="db.example.com"), "local", id="remote-hostname"),
    pytest.param(DEV, url(host="10.0.0.5"), "local", id="remote-ip"),
]


@pytest.mark.parametrize(("dev", "test", "fragment"), REJECTED)
def test_unsafe_configuration_is_rejected_without_leaking_secrets(dev, test, fragment):
    with pytest.raises(DatabaseSafetyError) as info:
        check_test_target(dev, test)
    message = str(info.value)
    assert fragment in message
    assert SECRET not in message
    assert "://" not in message  # no database URL, even partial


def test_describe_target_has_no_credentials():
    text_ = describe_target(check_test_target(DEV, TEST))
    assert text_ == f"localhost:5432/{EXPECTED_TEST_DB}"
    assert SECRET not in text_


# ------------------------------------------------------------ normalized_identity
def test_identity_ignores_credentials_host_aliases_and_case():
    base = normalized_identity(make_url(url(database="permission_aware_rag")))
    assert base == normalized_identity(make_url(url(user="x", password="y", database="permission_aware_rag")))
    assert base == normalized_identity(make_url(url(host="127.0.0.1", database="permission_aware_rag")))
    assert base == normalized_identity(make_url(url(host="[::1]", database="permission_aware_rag")))
    assert base == normalized_identity(make_url(url(database="Permission_Aware_Rag")))


def test_identity_differs_for_different_port_or_database():
    base = normalized_identity(make_url(DEV))
    assert base != normalized_identity(make_url(url(port="5433", database="permission_aware_rag")))
    assert base != normalized_identity(make_url(TEST))


# ------------------------------------------------------------ read_database_urls
def test_environment_beats_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("DATABASE_URL=file-dev\nTEST_DATABASE_URL=file-test\n", encoding="utf-8")
    dev, test = read_database_urls({"DATABASE_URL": "env-dev"}, env_file)
    assert (dev, test) == ("env-dev", "file-test")


def test_empty_environment_value_counts_as_missing_and_still_wins(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_DATABASE_URL=file-test\n", encoding="utf-8")
    assert read_database_urls({"TEST_DATABASE_URL": ""}, env_file) == (None, None)


def test_missing_env_file_gives_none(tmp_path):
    assert read_database_urls({}, tmp_path / "does-not-exist.env") == (None, None)


def test_keys_are_case_insensitive_and_values_are_stripped(tmp_path):
    dev, test = read_database_urls(
        {"database_url": "  a  ", "Test_Database_Url": " b "}, tmp_path / "none.env"
    )
    assert (dev, test) == ("a", "b")


# --------------------------------------------------- connection-time verification
class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _FakeConnection:
    def __init__(self, dbname):
        self.dbname = dbname
        self.statements: list[str] = []

    def execute(self, statement):
        self.statements.append(str(statement))
        return _FakeResult(self.dbname)


def test_verify_connected_database_accepts_expected_and_runs_only_a_select():
    conn = _FakeConnection(EXPECTED_TEST_DB)
    verify_connected_database(conn)
    assert conn.statements == ["SELECT current_database()"]


def test_verify_connected_database_rejects_other_database():
    with pytest.raises(DatabaseSafetyError) as info:
        verify_connected_database(_FakeConnection(DEV_DATABASE_NAME))
    assert "://" not in str(info.value)


class _FakeCursor:
    def __init__(self, owner):
        self.owner = owner

    def execute(self, sql):
        self.owner.statements.append(sql)

    def fetchone(self):
        return (self.owner.dbname,)

    def close(self):
        self.owner.cursor_closed = True


class _FakeDBAPIConnection:
    def __init__(self, dbname):
        self.dbname = dbname
        self.statements: list[str] = []
        self.rolled_back = False
        self.closed = False
        self.cursor_closed = False

    def cursor(self):
        return _FakeCursor(self)

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_connect_verifier_accepts_expected_database():
    raw = _FakeDBAPIConnection(EXPECTED_TEST_DB)
    make_connect_verifier(EXPECTED_TEST_DB)(raw, None)
    assert raw.statements == ["SELECT current_database()"]
    assert raw.rolled_back and raw.cursor_closed and not raw.closed


def test_connect_verifier_rejects_and_closes_wrong_database():
    raw = _FakeDBAPIConnection(DEV_DATABASE_NAME)
    with pytest.raises(DatabaseSafetyError):
        make_connect_verifier(EXPECTED_TEST_DB)(raw, None)
    assert raw.closed and raw.rolled_back


# ----------------------------------------------------------------- SQL tripwire
@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("DROP DATABASE permission_aware_rag", "DROP DATABASE"),
        ("drop   database x", "DROP DATABASE"),
        ("CREATE DATABASE x", "CREATE DATABASE"),
        ("DROP SCHEMA public CASCADE", "DROP SCHEMA"),
        ("TRUNCATE TABLE users", "TRUNCATE"),
        ("DROP/**/DATABASE x", "DROP DATABASE"),
        ("-- harmless comment\nTRUNCATE users", "TRUNCATE"),
    ],
)
def test_forbidden_statements_are_found(sql, expected):
    assert find_forbidden_statement(sql) == expected


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "SELECT current_database()",
        "INSERT INTO users (email) VALUES (%(email)s)",
        "DELETE FROM users WHERE id = 1",
        "SELECT truncated_at FROM sessions",
    ],
)
def test_ordinary_statements_are_allowed(sql):
    assert find_forbidden_statement(sql) is None


def test_listener_blocks_forbidden_and_allows_select():
    with pytest.raises(DatabaseSafetyError):
        reject_forbidden_statement(None, None, "DROP DATABASE x", None, None, False)
    assert reject_forbidden_statement(None, None, "SELECT 1", None, None, False) is None


# ------------------------------------------------------------- build_verified_engine
def test_build_verified_engine_validates_before_creating_anything():
    with pytest.raises(DatabaseSafetyError):
        build_verified_engine(DEV, DEV)
    with pytest.raises(DatabaseSafetyError):
        build_verified_engine(DEV, None)


def test_build_verified_engine_targets_test_db_and_does_not_connect():
    engine = build_verified_engine(DEV, TEST)  # lazy: no connection is opened
    try:
        assert engine.url.database == EXPECTED_TEST_DB
        assert event.contains(engine, "before_cursor_execute", reject_forbidden_statement)
    finally:
        engine.dispose()


# --------------------------------------------------- missing database detection
class _FakeDriverError(Exception):
    def __init__(self, message, sqlstate=None):
        super().__init__(message)
        self.sqlstate = sqlstate


def test_missing_database_detected_by_sqlstate():
    assert is_missing_database_error(_FakeDriverError("whatever", sqlstate="3D000"))


def test_missing_database_detected_by_message():
    err = _FakeDriverError(f'connection failed: FATAL:  database "{EXPECTED_TEST_DB}" does not exist')
    assert is_missing_database_error(err)


@pytest.mark.parametrize(
    "message",
    ['FATAL:  password authentication failed for user "rag"', "connection refused", "timeout expired"],
)
def test_other_connection_errors_are_not_reported_as_missing_database(message):
    assert not is_missing_database_error(_FakeDriverError(message))


class _FakeEngine:
    def __init__(self, error=None, dbname=EXPECTED_TEST_DB):
        self.error = error
        self.dbname = dbname

    def connect(self):
        if self.error is not None:
            raise self.error
        return _FakeContext(_FakeConnection(self.dbname))


class _FakeContext:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *exc_info):
        return False


def _operational_error(message):
    return OperationalError("SELECT 1", {}, _FakeDriverError(message))


def test_connect_and_verify_succeeds_for_expected_database():
    connect_and_verify(_FakeEngine())


def test_connect_and_verify_reports_missing_database_with_manual_instructions():
    engine = _FakeEngine(error=_operational_error(f'FATAL:  database "{EXPECTED_TEST_DB}" does not exist'))
    with pytest.raises(MissingTestDatabaseError) as info:
        connect_and_verify(engine)
    assert f"CREATE DATABASE {EXPECTED_TEST_DB};" in str(info.value)


def test_connect_and_verify_hides_driver_details_for_other_failures():
    engine = _FakeEngine(error=_operational_error('FATAL:  password authentication failed for user "rag"'))
    with pytest.raises(DatabaseSafetyError) as info:
        connect_and_verify(engine)
    assert not isinstance(info.value, MissingTestDatabaseError)
    assert "FATAL" not in str(info.value)
    assert "rag" not in str(info.value).replace("par-postgres", "")


def test_connect_and_verify_rejects_wrong_database():
    with pytest.raises(DatabaseSafetyError):
        connect_and_verify(_FakeEngine(dbname=DEV_DATABASE_NAME))


def test_missing_database_instructions_are_manual_and_non_destructive():
    message = missing_database_instructions()
    assert f"CREATE DATABASE {EXPECTED_TEST_DB};" in message
    assert "par-postgres" in message and "-d postgres" in message
    assert "DROP" not in message.upper()


# ------------------------------------------------------------------ CLI (main)
def test_main_reports_success_without_secrets(capsys, tmp_path):
    code = db_safety.main({"DATABASE_URL": DEV, "TEST_DATABASE_URL": TEST}, tmp_path / "none.env")
    out = capsys.readouterr().out
    assert code == 0
    assert f"localhost:5432/{EXPECTED_TEST_DB}" in out
    assert SECRET not in out and "://" not in out


def test_main_reports_failure_without_secrets(capsys, tmp_path):
    code = db_safety.main({"DATABASE_URL": DEV, "TEST_DATABASE_URL": DEV}, tmp_path / "none.env")
    out = capsys.readouterr().out
    assert code == 1
    assert "FAILED" in out and SECRET not in out and "://" not in out


# ------------------------------------------------------------ conftest guard layers
def test_application_settings_never_point_at_the_development_database():
    settings_url = make_url(get_settings().database_url.get_secret_value())
    assert settings_url.database != DEV_DATABASE_NAME
    assert make_url(os.environ["DATABASE_URL"]).database != DEV_DATABASE_NAME


def test_application_engine_factories_are_blocked_during_tests():
    from app.db import session as app_session

    with pytest.raises(DatabaseSafetyError):
        app_session.get_engine()
    with pytest.raises(DatabaseSafetyError):
        app_session.get_session_factory()