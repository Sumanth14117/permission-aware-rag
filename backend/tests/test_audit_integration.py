"""Opt-in PostgreSQL integration test for the real audit-recording service.

Run explicitly with:

    pytest --run-db tests/test_audit_integration.py

It uses only the existing ``verified_test_engine`` fixture, so every connection
is confirmed to be ``permission_aware_rag_test`` on a local host. It creates one
uniquely named organization/user/membership, records real audit events through
``app.audit``, verifies the persisted fields, and removes only its own rows in a
``finally`` block.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select, text

from app.audit import (
    EVENT_LOGIN_SUCCEEDED,
    EVENT_QA_SUCCEEDED,
    record_audit_event,
    record_login_success,
)
from app.db.session import create_session_factory
from app.models import (
    ROLE_OWNER,
    AuditEvent,
    Membership,
    Organization,
    User,
)
from app.models.audit_event import OUTCOME_SUCCESS
from tests.db_safety import EXPECTED_TEST_DB

# Column names that must never exist on the audit table.
SENSITIVE_COLUMN_NAMES = frozenset(
    {
        "email",
        "password",
        "password_hash",
        "token",
        "session_token",
        "csrf_token",
        "original_filename",
        "filename",
        "content",
        "question",
        "answer",
        "text",
    }
)


@pytest.mark.db
def test_real_audit_service_persists_safe_fields_only(verified_test_engine):
    factory = create_session_factory(verified_test_engine)
    suffix = uuid.uuid4().hex
    organization_id = uuid.uuid4()
    user_id = uuid.uuid4()
    email = f"audit-integration-{suffix}@example.com"
    password_hash = f"SENSITIVE-PASSWORD-HASH-{suffix}"
    question = f"SENSITIVE-QUESTION-{suffix}"

    try:
        # The verified fixture is the safety gate; assert its identity once more.
        with verified_test_engine.connect() as connection:
            assert (
                connection.execute(text("SELECT current_database()")).scalar_one()
                == EXPECTED_TEST_DB
            )

        with factory() as session:
            session.add(
                Organization(id=organization_id, name=f"Audit integration {suffix}")
            )
            session.flush()
            session.add(
                User(id=user_id, email=email, password_hash=password_hash)
            )
            session.flush()
            session.add(
                Membership(
                    user_id=user_id,
                    organization_id=organization_id,
                    role=ROLE_OWNER,
                )
            )
            session.commit()

        with factory() as session:
            assert (
                record_audit_event(
                    session,
                    event_type=EVENT_QA_SUCCEEDED,
                    outcome=OUTCOME_SUCCESS,
                    actor_user_id=user_id,
                    organization_id=organization_id,
                    actor_role=ROLE_OWNER,
                    question_length=len(question),
                    citation_count=2,
                )
                is True
            )
            # Also exercises membership-derived organization/role resolution.
            assert record_login_success(session, user_id=user_id) is True

        with factory() as session:
            rows = session.execute(
                select(AuditEvent).where(AuditEvent.actor_user_id == user_id)
            ).scalars().all()

        assert len(rows) == 2
        by_type = {row.event_type: row for row in rows}
        assert set(by_type) == {EVENT_QA_SUCCEEDED, EVENT_LOGIN_SUCCEEDED}

        qa_event = by_type[EVENT_QA_SUCCEEDED]
        assert qa_event.outcome == OUTCOME_SUCCESS
        assert qa_event.actor_user_id == user_id
        assert qa_event.organization_id == organization_id
        assert qa_event.actor_role == ROLE_OWNER
        assert qa_event.question_length == len(question)
        assert qa_event.citation_count == 2
        assert qa_event.resource_id is None
        assert qa_event.reason is None
        assert qa_event.created_at.tzinfo is not None

        login_event = by_type[EVENT_LOGIN_SUCCEEDED]
        assert login_event.outcome == OUTCOME_SUCCESS
        assert login_event.actor_user_id == user_id
        assert login_event.organization_id == organization_id
        assert login_event.actor_role == ROLE_OWNER
        assert login_event.question_length is None
        assert login_event.citation_count is None
        assert login_event.created_at.tzinfo is not None

        # The table has no column capable of holding secrets or content, and the
        # sensitive values that exist in users (email, password hash) or only in
        # the request (question) are never persisted in these rows.
        column_names = tuple(AuditEvent.__table__.columns.keys())
        assert set(column_names).isdisjoint(SENSITIVE_COLUMN_NAMES)
        persisted_text = " ".join(
            str(getattr(row, name)) for row in rows for name in column_names
        )
        assert question not in persisted_text
        assert password_hash not in persisted_text
        assert email not in persisted_text
    finally:
        with factory() as session:
            session.execute(
                delete(AuditEvent).where(AuditEvent.actor_user_id == user_id)
            )
            session.commit()
        with factory() as session:
            session.execute(delete(Membership).where(Membership.user_id == user_id))
            session.execute(delete(User).where(User.id == user_id))
            session.execute(
                delete(Organization).where(Organization.id == organization_id)
            )
            session.commit()
