"""Offline unit tests for Alembic transaction ownership.

All connection and Alembic objects are in-memory fakes. No database or network
connection is made.
"""

from contextlib import contextmanager

import pytest
from sqlalchemy.exc import DBAPIError

from app.db.migration_runtime import (
    run_migrations_with_engine,
    run_migrations_with_owned_transaction,
)
from app.db.migration_safety import MigrationSafetyError


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class FakeConnection:
    def __init__(self, database, execute_error=None):
        self.database = database
        self.execute_error = execute_error
        self.in_transaction = False
        self.commits = 0
        self.statements = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.closed = True
        self.in_transaction = False
        return False

    def execute(self, statement):
        self.statements.append(str(statement))
        if self.execute_error is not None:
            raise self.execute_error
        self.in_transaction = True
        return FakeResult(self.database)

    def commit(self):
        assert self.in_transaction
        self.commits += 1
        self.in_transaction = False


class FakeEngine:
    def __init__(self, connection=None, connect_error=None):
        self.connection = connection
        self.connect_error = connect_error
        self.connect_calls = 0
        self.disposed = False

    def connect(self):
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error
        return self.connection

    def dispose(self):
        self.disposed = True


class FakeMigrationContext:
    def __init__(self, connection, fail=False):
        self.connection = connection
        self.fail = fail
        self.configured = False
        self.migration_transaction_started = False
        self.migration_commits = 0
        self.migration_rollbacks = 0

    def configure(self, *, connection, target_metadata):
        assert connection is self.connection
        assert not connection.in_transaction
        self.configured = True

    @contextmanager
    def begin_transaction(self):
        assert self.configured
        assert not self.connection.in_transaction
        self.connection.in_transaction = True
        self.migration_transaction_started = True
        try:
            yield
        except BaseException:
            self.migration_rollbacks += 1
            self.connection.in_transaction = False
            raise
        else:
            self.migration_commits += 1
            self.connection.in_transaction = False

    def run_migrations(self):
        if self.fail:
            raise RuntimeError("migration failed")


def test_identity_check_transaction_is_committed_before_alembic_owns_transaction():
    connection = FakeConnection("permission_aware_rag_test")
    context = FakeMigrationContext(connection)

    run_migrations_with_owned_transaction(
        connection,
        context,
        target_metadata=object(),
        expected_database="permission_aware_rag_test",
    )

    assert connection.statements == ["SELECT current_database()"]
    assert connection.commits == 1
    assert context.migration_transaction_started
    assert context.migration_commits == 1
    assert context.migration_rollbacks == 0
    assert not connection.in_transaction


def test_engine_and_connection_are_cleaned_up_after_success():
    connection = FakeConnection("permission_aware_rag_test")
    engine = FakeEngine(connection)
    context = FakeMigrationContext(connection)

    run_migrations_with_engine(
        engine, context, target_metadata=object(), expected_database="permission_aware_rag_test"
    )

    assert connection.closed
    assert engine.disposed
    assert engine.connect_calls == 1
    assert connection.commits == 1
    assert context.migration_commits == 1


def test_identity_query_exception_cleans_up_without_configuring_alembic():
    connection = FakeConnection(
        "permission_aware_rag_test", execute_error=RuntimeError("identity query failed")
    )
    engine = FakeEngine(connection)
    context = FakeMigrationContext(connection)

    with pytest.raises(RuntimeError, match="identity query failed"):
        run_migrations_with_engine(
            engine, context, target_metadata=object(), expected_database="permission_aware_rag_test"
        )

    assert connection.closed
    assert engine.disposed
    assert connection.commits == 0
    assert not context.configured
    assert not context.migration_transaction_started


def test_identity_mismatch_cleans_up_without_configuring_or_running_migrations():
    connection = FakeConnection("permission_aware_rag")
    engine = FakeEngine(connection)
    context = FakeMigrationContext(connection)

    with pytest.raises(MigrationSafetyError):
        run_migrations_with_engine(
            engine,
            context,
            target_metadata=object(),
            expected_database="permission_aware_rag_test",
        )

    assert connection.closed
    assert engine.disposed
    assert connection.commits == 0
    assert not context.configured
    assert not context.migration_transaction_started
    assert context.migration_commits == 0
    assert context.migration_rollbacks == 0


def test_alembic_configuration_exception_cleans_up_after_identity_commit():
    connection = FakeConnection("permission_aware_rag_test")
    engine = FakeEngine(connection)
    context = FakeMigrationContext(connection)

    def fail_configuration(*, connection, target_metadata):
        assert not connection.in_transaction
        raise RuntimeError("configuration failed")

    context.configure = fail_configuration

    with pytest.raises(RuntimeError, match="configuration failed"):
        run_migrations_with_engine(
            engine, context, target_metadata=object(), expected_database="permission_aware_rag_test"
        )

    assert connection.commits == 1
    assert connection.closed
    assert engine.disposed
    assert not context.migration_transaction_started


def test_connection_failure_still_disposes_engine():
    engine = FakeEngine(connect_error=RuntimeError("connect failed"))

    with pytest.raises(RuntimeError, match="connect failed"):
        run_migrations_with_engine(
            engine,
            FakeMigrationContext(None),
            target_metadata=object(),
            expected_database="permission_aware_rag_test",
        )

    assert engine.disposed
    assert engine.connect_calls == 1


def test_dbapi_connection_error_is_redacted_and_engine_is_disposed():
    engine = FakeEngine(
        connect_error=DBAPIError("connect", {}, Exception("secret details"))
    )

    with pytest.raises(MigrationSafetyError) as error:
        run_migrations_with_engine(
            engine,
            FakeMigrationContext(None),
            target_metadata=object(),
            expected_database="permission_aware_rag_test",
        )

    assert "secret details" not in str(error.value)
    assert engine.disposed


def test_dbapi_errors_are_redacted_and_resources_are_cleaned_up():
    connection = FakeConnection(
        "permission_aware_rag_test",
        execute_error=DBAPIError("SELECT current_database()", {}, Exception("secret details")),
    )
    engine = FakeEngine(connection)
    context = FakeMigrationContext(connection)

    with pytest.raises(MigrationSafetyError) as error:
        run_migrations_with_engine(
            engine, context, target_metadata=object(), expected_database="permission_aware_rag_test"
        )

    assert "secret details" not in str(error.value)
    assert connection.closed
    assert engine.disposed
    assert not context.configured


def test_migration_failure_rolls_back_alembic_owned_transaction():
    connection = FakeConnection("permission_aware_rag")
    context = FakeMigrationContext(connection, fail=True)

    with pytest.raises(RuntimeError, match="migration failed"):
        run_migrations_with_owned_transaction(
            connection,
            context,
            target_metadata=object(),
            expected_database="permission_aware_rag",
        )

    assert connection.commits == 1
    assert context.migration_commits == 0
    assert context.migration_rollbacks == 1
    assert not connection.in_transaction


def test_migration_failure_closes_connection_and_disposes_engine():
    connection = FakeConnection("permission_aware_rag_test")
    engine = FakeEngine(connection)
    context = FakeMigrationContext(connection, fail=True)

    with pytest.raises(RuntimeError, match="migration failed"):
        run_migrations_with_engine(
            engine, context, target_metadata=object(), expected_database="permission_aware_rag_test"
        )

    assert connection.commits == 1
    assert context.migration_rollbacks == 1
    assert connection.closed
    assert engine.disposed


def test_identity_mismatch_rejected_before_commit_or_migration():
    connection = FakeConnection("permission_aware_rag")
    context = FakeMigrationContext(connection)

    with pytest.raises(MigrationSafetyError):
        run_migrations_with_owned_transaction(
            connection,
            context,
            target_metadata=object(),
            expected_database="permission_aware_rag_test",
        )

    assert connection.commits == 0
    assert not context.configured
    assert not context.migration_transaction_started
