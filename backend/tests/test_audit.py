"""Database-independent tests for audit-event modelling and recording.

No PostgreSQL connection is opened: the audit session factory is replaced with
an in-memory double, and the request session is a stub that fails loudly if the
audit code tries to commit or roll it back.
"""

import logging
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
)
from sqlalchemy.sql.sqltypes import Uuid as SqlUuid

import app.audit as audit_module
from app.audit import (
    EVENT_DOCUMENT_DELETE_DENIED,
    EVENT_LOGIN_FAILED,
    EVENT_LOGIN_SUCCEEDED,
    REASON_INVALID_CREDENTIALS,
    record_audit_event,
    record_login_success,
)
from app.db.base import Base
from app.models import AuditEvent
from app.models.audit_event import (
    OUTCOME_DENIED,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
)


class _Transaction:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.session.committed = exc_type is None
        return False


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _AuditSession:
    """Minimal sqlalchemy.orm.Session double."""

    instances: list["_AuditSession"] = []
    fail_on_add = False
    execute_result = None
    execute_error: Exception | None = None

    def __init__(self, bind=None):
        self.bind = bind
        self.added = []
        self.committed = False
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def begin(self):
        return _Transaction(self)

    def add(self, instance):
        if type(self).fail_on_add:
            raise RuntimeError("private audit insert detail")
        self.added.append(instance)

    def execute(self, statement):
        if type(self).execute_error is not None:
            raise type(self).execute_error
        return _ScalarResult(type(self).execute_result)


class _RequestSession:
    """The main request session: audit code may only read its bind."""

    def __init__(self):
        self.commit_calls = 0
        self.rollback_calls = 0

    def get_bind(self):
        return "verified-engine"

    def commit(self):
        self.commit_calls += 1
        raise AssertionError("audit must not commit the request session")

    def rollback(self):
        self.rollback_calls += 1
        raise AssertionError("audit must not roll back the request session")


@pytest.fixture(autouse=True)
def _audit_session_double(monkeypatch):
    _AuditSession.instances = []
    _AuditSession.fail_on_add = False
    _AuditSession.execute_result = None
    _AuditSession.execute_error = None
    monkeypatch.setattr(audit_module, "Session", _AuditSession)
    yield


def _constraints(table, constraint_type):
    return {
        constraint.name: constraint
        for constraint in table.constraints
        if isinstance(constraint, constraint_type)
    }


def _check_expression(constraint):
    return " ".join(str(constraint.sqltext).split())


def test_audit_event_registers_on_the_shared_base():
    assert AuditEvent.metadata is Base.metadata
    assert "audit_events" in Base.metadata.tables


def test_audit_event_columns_and_types():
    table = Base.metadata.tables["audit_events"]
    assert set(table.columns.keys()) == {
        "id",
        "created_at",
        "event_type",
        "outcome",
        "actor_user_id",
        "organization_id",
        "actor_role",
        "resource_type",
        "resource_id",
        "reason",
        "question_length",
        "citation_count",
    }
    assert isinstance(table.c.id.type, SqlUuid)
    assert isinstance(table.c.actor_user_id.type, SqlUuid)
    assert isinstance(table.c.organization_id.type, SqlUuid)
    assert isinstance(table.c.resource_id.type, SqlUuid)
    assert isinstance(table.c.event_type.type, String)
    assert table.c.event_type.type.length == 64
    assert table.c.outcome.type.length == 16
    assert table.c.actor_role.type.length == 16
    assert table.c.resource_type.type.length == 32
    assert table.c.reason.type.length == 64
    assert isinstance(table.c.question_length.type, Integer)
    assert isinstance(table.c.citation_count.type, Integer)


def test_audit_event_nullability_and_primary_key():
    table = Base.metadata.tables["audit_events"]
    assert {
        column.name for column in table.columns if column.nullable
    } == {
        "actor_user_id",
        "organization_id",
        "actor_role",
        "resource_type",
        "resource_id",
        "reason",
        "question_length",
        "citation_count",
    }
    primary_key = next(iter(_constraints(table, PrimaryKeyConstraint).values()))
    assert primary_key.name == "pk_audit_events"
    assert list(primary_key.columns.keys()) == ["id"]


def test_audit_event_has_no_foreign_keys():
    table = Base.metadata.tables["audit_events"]
    assert _constraints(table, ForeignKeyConstraint) == {}


def test_audit_event_check_constraints_and_indexes():
    table = Base.metadata.tables["audit_events"]
    checks = {
        name: _check_expression(constraint)
        for name, constraint in _constraints(table, CheckConstraint).items()
    }
    assert checks == {
        "ck_audit_events_event_type_not_blank": "btrim(event_type) <> ''",
        "ck_audit_events_outcome_valid": "outcome IN ('success', 'failure', 'denied')",
        "ck_audit_events_actor_role_valid": (
            "actor_role IS NULL OR actor_role IN ('owner', 'member')"
        ),
        "ck_audit_events_reason_not_blank": "reason IS NULL OR btrim(reason) <> ''",
        "ck_audit_events_question_length_nonnegative": (
            "question_length IS NULL OR question_length >= 0"
        ),
        "ck_audit_events_citation_count_nonnegative": (
            "citation_count IS NULL OR citation_count >= 0"
        ),
    }

    indexes = {
        (
            index.name,
            index.unique,
            tuple(column.name for column in index.columns),
        )
        for index in table.indexes
    }
    assert indexes == {
        ("ix_audit_events_created_at", False, ("created_at",)),
        (
            "ix_audit_events_event_type_created_at",
            False,
            ("event_type", "created_at"),
        ),
        (
            "ix_audit_events_organization_created_at",
            False,
            ("organization_id", "created_at"),
        ),
        (
            "ix_audit_events_actor_created_at",
            False,
            ("actor_user_id", "created_at"),
        ),
        ("ix_audit_events_resource", False, ("resource_type", "resource_id")),
    }
    assert all(isinstance(index, Index) for index in table.indexes)


def test_audit_event_has_no_columns_that_could_hold_secrets_or_content():
    columns = set(Base.metadata.tables["audit_events"].columns.keys())
    forbidden = {
        "email",
        "password",
        "password_hash",
        "token",
        "session_token",
        "csrf_token",
        "original_filename",
        "filename",
        "content",
        "document_text",
        "question",
        "answer",
        "text",
    }
    assert columns.isdisjoint(forbidden)


def test_record_audit_event_writes_all_safe_fields():
    db = _RequestSession()
    actor_id, organization_id, resource_id = (
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
    )

    recorded = record_audit_event(
        db,
        event_type=EVENT_DOCUMENT_DELETE_DENIED,
        outcome=OUTCOME_DENIED,
        actor_user_id=actor_id,
        organization_id=organization_id,
        actor_role="member",
        resource_type="document",
        resource_id=resource_id,
        reason="not_found_or_forbidden",
        question_length=None,
        citation_count=None,
    )

    assert recorded is True
    assert db.commit_calls == 0
    assert db.rollback_calls == 0
    (audit_session,) = _AuditSession.instances
    assert audit_session.bind == "verified-engine"
    assert audit_session.committed is True
    (event,) = audit_session.added
    assert event.event_type == EVENT_DOCUMENT_DELETE_DENIED
    assert event.outcome == OUTCOME_DENIED
    assert event.actor_user_id == actor_id
    assert event.organization_id == organization_id
    assert event.actor_role == "member"
    assert event.resource_type == "document"
    assert event.resource_id == resource_id
    assert event.reason == "not_found_or_forbidden"


def test_record_audit_event_uses_a_separate_session_and_transaction():
    db = _RequestSession()

    record_audit_event(
        db,
        event_type=EVENT_LOGIN_FAILED,
        outcome=OUTCOME_FAILURE,
        reason=REASON_INVALID_CREDENTIALS,
    )

    assert db.commit_calls == 0
    assert db.rollback_calls == 0
    assert _AuditSession.instances[0].bind is not db


def test_record_audit_event_swallows_failures_and_warns_safely(caplog):
    _AuditSession.fail_on_add = True
    db = _RequestSession()

    with caplog.at_level(logging.WARNING, logger="app.audit"):
        recorded = record_audit_event(
            db,
            event_type=EVENT_LOGIN_FAILED,
            outcome=OUTCOME_FAILURE,
            reason=REASON_INVALID_CREDENTIALS,
        )

    assert recorded is False
    assert db.commit_calls == 0
    assert db.rollback_calls == 0
    assert "audit event was not recorded" in caplog.text
    assert EVENT_LOGIN_FAILED in caplog.text
    assert "private audit insert detail" not in caplog.text


def test_record_audit_event_without_a_session_warns_and_returns_false(caplog):
    with caplog.at_level(logging.WARNING, logger="app.audit"):
        recorded = record_audit_event(
            None,
            event_type=EVENT_LOGIN_FAILED,
            outcome=OUTCOME_FAILURE,
            reason=REASON_INVALID_CREDENTIALS,
        )

    assert recorded is False
    assert _AuditSession.instances == []
    assert "no_session" in caplog.text


def test_record_login_success_resolves_organization_and_role_from_membership():
    user_id, organization_id = uuid.uuid4(), uuid.uuid4()
    _AuditSession.execute_result = SimpleNamespace(
        organization_id=organization_id, role="member"
    )
    db = _RequestSession()

    assert record_login_success(db, user_id=user_id) is True

    assert db.commit_calls == 0
    assert db.rollback_calls == 0
    assert len(_AuditSession.instances) == 2
    (event,) = _AuditSession.instances[-1].added
    assert event.event_type == EVENT_LOGIN_SUCCEEDED
    assert event.outcome == OUTCOME_SUCCESS
    assert event.actor_user_id == user_id
    assert event.organization_id == organization_id
    assert event.actor_role == "member"


def test_record_login_success_records_without_context_when_lookup_fails(caplog):
    _AuditSession.execute_error = RuntimeError("lookup failed")
    db = _RequestSession()
    user_id = uuid.uuid4()

    with caplog.at_level(logging.WARNING, logger="app.audit"):
        assert record_login_success(db, user_id=user_id) is True

    (event,) = _AuditSession.instances[-1].added
    assert event.event_type == EVENT_LOGIN_SUCCEEDED
    assert event.actor_user_id == user_id
    assert event.organization_id is None
    assert event.actor_role is None
    assert db.commit_calls == 0
    assert db.rollback_calls == 0
