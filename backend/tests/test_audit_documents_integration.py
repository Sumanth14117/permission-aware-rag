"""Opt-in PostgreSQL integration test for document and QA audit recording.

Run explicitly with:

    pytest --run-db tests/test_audit_documents_integration.py

It uses only the existing ``verified_test_engine`` fixture, which proves every
connection targets ``permission_aware_rag_test`` on a local host. It drives the
real ``documents.delete_document`` and ``qa.ask_question`` handlers so the real
``app.audit`` service writes to PostgreSQL, verifies the persisted events, and
removes only its own rows in a ``finally`` block (foreign-key-safe order).

No Qdrant or external LLM service is contacted: only the embedding provider, the
Qdrant client constructor, and answer generation are replaced with local fakes.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select, text

import app.qa as qa_module
from app.answering import Answer, Citation
from app.audit import (
    EVENT_DOCUMENT_DELETE_DENIED,
    EVENT_QA_SUCCEEDED,
    REASON_NOT_FOUND_OR_FORBIDDEN,
)
from app.db.session import create_session_factory
from app.documents import TenantContext, delete_document
from app.models import (
    ROLE_OWNER,
    AuditEvent,
    Document,
    Membership,
    Organization,
    User,
)
from app.models.audit_event import OUTCOME_DENIED, OUTCOME_SUCCESS
from app.models.document import STATUS_PENDING, VISIBILITY_ORG
from tests.db_safety import EXPECTED_TEST_DB


class _FakeVectorIndex:
    def close(self):
        pass


def _settings(upload_root):
    return SimpleNamespace(
        llm_provider="mock",
        embedding_model_name="unused-for-test",
        embedding_batch_size=2,
        qdrant_url="http://unused",
        qdrant_api_key=None,
        qdrant_collection_name="chunks",
        upload_root=upload_root,
    )


def _persisted_rows(factory, actor_user_id):
    with factory() as session:
        return list(
            session.execute(
                select(AuditEvent).where(AuditEvent.actor_user_id == actor_user_id)
            ).scalars().all()
        )


@pytest.mark.db
def test_document_and_qa_audit_persist_without_content(
    verified_test_engine, monkeypatch, tmp_path
):
    factory = create_session_factory(verified_test_engine)
    suffix = uuid.uuid4().hex
    org_a_id, org_b_id = uuid.uuid4(), uuid.uuid4()
    user_a_id, user_b_id = uuid.uuid4(), uuid.uuid4()
    doc_b_id = uuid.uuid4()
    filename_sentinel = f"SENSITIVE-FILENAME-{suffix}.txt"
    question_sentinel = f"SENSITIVE-QUESTION-{suffix}"
    answer_sentinel = f"SENSITIVE-ANSWER-{suffix}"

    try:
        # The verified fixture is the safety gate; assert its identity once more.
        with verified_test_engine.connect() as connection:
            assert (
                connection.execute(text("SELECT current_database()")).scalar_one()
                == EXPECTED_TEST_DB
            )

        with factory() as session:
            session.add_all(
                [
                    Organization(id=org_a_id, name=f"Audit doc A {suffix}"),
                    Organization(id=org_b_id, name=f"Audit doc B {suffix}"),
                    User(
                        id=user_a_id,
                        email=f"audit-doc-a-{suffix}@example.com",
                        password_hash=f"SENSITIVE-PASSWORD-HASH-{suffix}",
                    ),
                    User(
                        id=user_b_id,
                        email=f"audit-doc-b-{suffix}@example.com",
                        password_hash=f"SENSITIVE-PASSWORD-HASH-{suffix}",
                    ),
                ]
            )
            session.flush()
            session.add_all(
                [
                    Membership(
                        user_id=user_a_id, organization_id=org_a_id, role=ROLE_OWNER
                    ),
                    Membership(
                        user_id=user_b_id, organization_id=org_b_id, role=ROLE_OWNER
                    ),
                    Document(
                        id=doc_b_id,
                        organization_id=org_b_id,
                        uploaded_by_user_id=user_b_id,
                        original_filename=filename_sentinel,
                        content_type="text/plain",
                        size_bytes=len(b"content"),
                        visibility=VISIBILITY_ORG,
                        status=STATUS_PENDING,
                    ),
                ]
            )
            session.commit()

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(settings=_settings(tmp_path)))
        )

        # ---- cross-tenant delete through the real handler --------------------
        with factory() as session:
            context = TenantContext(
                user_id=user_a_id,
                organization_id=org_a_id,
                role=ROLE_OWNER,
                db=session,
            )
            with pytest.raises(HTTPException) as error:
                delete_document(doc_b_id, request, context)

        assert error.value.status_code == 404
        assert error.value.detail == "Document not found"

        rows = _persisted_rows(factory, user_a_id)
        assert len(rows) == 1
        deletion = rows[0]
        assert deletion.event_type == EVENT_DOCUMENT_DELETE_DENIED
        assert deletion.outcome == OUTCOME_DENIED
        assert deletion.actor_user_id == user_a_id
        assert deletion.organization_id == org_a_id
        assert deletion.actor_role == ROLE_OWNER
        assert deletion.resource_type == "document"
        assert deletion.resource_id == doc_b_id
        assert deletion.reason == REASON_NOT_FOUND_OR_FORBIDDEN
        assert deletion.created_at.tzinfo is not None

        # The denial must not have deleted the other tenant's document.
        with factory() as session:
            assert session.get(Document, doc_b_id) is not None

        # ---- QA through the real handler -------------------------------------
        monkeypatch.setattr(
            qa_module,
            "SentenceTransformersEmbeddingProvider",
            lambda *args, **kwargs: object(),
        )
        monkeypatch.setattr(
            qa_module.QdrantVectorIndex,
            "from_settings",
            lambda settings: _FakeVectorIndex(),
        )
        monkeypatch.setattr(
            qa_module,
            "answer_question",
            lambda *args, **kwargs: Answer(
                answer_sentinel,
                (Citation(str(uuid.uuid4()), 0), Citation(str(uuid.uuid4()), 1)),
            ),
        )

        question = f"{question_sentinel} what is in the document?"
        with factory() as session:
            context = TenantContext(
                user_id=user_a_id,
                organization_id=org_a_id,
                role=ROLE_OWNER,
                db=session,
            )
            result = qa_module.ask_question(
                qa_module.QuestionRequest(question=question), request, context
            )

        assert result["answer"] == answer_sentinel
        assert len(result["citations"]) == 2

        rows = _persisted_rows(factory, user_a_id)
        assert len(rows) == 2
        by_type = {row.event_type: row for row in rows}
        qa_event = by_type[EVENT_QA_SUCCEEDED]
        assert qa_event.outcome == OUTCOME_SUCCESS
        assert qa_event.actor_user_id == user_a_id
        assert qa_event.organization_id == org_a_id
        assert qa_event.actor_role == ROLE_OWNER
        assert qa_event.question_length == len(question)
        assert qa_event.citation_count == 2
        assert qa_event.resource_id is None
        assert qa_event.reason is None
        assert qa_event.created_at.tzinfo is not None

        # Neither question text, answer text, nor the document filename is
        # stored anywhere in the persisted audit columns.
        column_names = tuple(AuditEvent.__table__.columns.keys())
        persisted_text = " ".join(
            str(getattr(row, name)) for row in rows for name in column_names
        )
        for sentinel in (question_sentinel, answer_sentinel, filename_sentinel):
            assert sentinel not in persisted_text
    finally:
        with factory() as session:
            session.execute(
                delete(AuditEvent).where(AuditEvent.actor_user_id == user_a_id)
            )
            session.commit()
        with factory() as session:
            session.execute(delete(Document).where(Document.id == doc_b_id))
            session.execute(
                delete(Membership).where(Membership.user_id.in_((user_a_id, user_b_id)))
            )
            session.execute(delete(User).where(User.id.in_((user_a_id, user_b_id))))
            session.execute(
                delete(Organization).where(Organization.id.in_((org_a_id, org_b_id)))
            )
            session.commit()
