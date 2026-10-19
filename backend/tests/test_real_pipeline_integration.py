"""Opt-in PostgreSQL + Qdrant pipeline integration test.

Run explicitly with:

    pytest --run-db tests/test_real_pipeline_integration.py

The test creates only uniquely named records and a unique Qdrant collection,
then removes them in finally blocks. It uses deterministic embeddings and a
recording fake LLM; no external LLM or embedding model is called.
"""

from __future__ import annotations

from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import delete, select

from app.answering import Citation, answer_question
from app.config import get_settings
from app.db.session import create_session_factory
from app.documents import TenantContext
from app.indexing import EXPECTED_EMBEDDING_DIMENSION, QdrantVectorIndex
from app.ingestion_service import SQLAlchemyIngestionPersistence, ingest_document
from app.models import (
    ROLE_MEMBER,
    Document,
    Membership,
    Organization,
    StoredDocumentChunk,
    User,
)
from app.models.document import STATUS_PENDING, VISIBILITY_ORG
from app.retrieval import SQLAlchemyRetrievalRepository


QUERY_VECTOR = (1.0,) + (0.0,) * (EXPECTED_EMBEDDING_DIMENSION - 1)


class DeterministicEmbedding:
    """Return the same valid 384-dimensional vector for every input."""

    dimension = EXPECTED_EMBEDDING_DIMENSION

    def embed_batch(self, texts):
        return [QUERY_VECTOR for _ in texts]


class RecordingLLM:
    """Local fake that answers from the chunks it actually receives."""

    def __init__(self):
        self.calls = []

    def generate(self, *, question, chunks, source_markers):
        self.calls.append((question, chunks, source_markers))
        return f"{source_markers[0]}{chunks[0].text}"


class RecordingVectorSearch:
    """Record retrieval search arguments and delegate to the real adapter."""

    def __init__(self, index):
        self.index = index
        self.calls = []

    def search(self, **kwargs):
        self.calls.append(kwargs)
        return self.index.search(**kwargs)


def _cleanup_qdrant(index, collection_name):
    try:
        if index.client.collection_exists(collection_name):
            index.client.delete_collection(collection_name=collection_name)
    finally:
        index.close()


@pytest.fixture
def qdrant_index():
    """Create and remove a test-only Qdrant collection."""
    settings = get_settings()
    collection_name = f"par_test_{uuid.uuid4().hex}"
    index = QdrantVectorIndex.from_settings(
        SimpleNamespace(
            qdrant_url=settings.qdrant_url,
            qdrant_api_key=settings.qdrant_api_key,
            qdrant_collection_name=collection_name,
        )
    )
    try:
        index.ensure_collection(EXPECTED_EMBEDDING_DIMENSION)
        yield index
    finally:
        _cleanup_qdrant(index, collection_name)


def _seed_pending_documents(
    factory,
    *,
    org_a_id,
    org_b_id,
    user_a_id,
    user_b_id,
    doc_a_id,
    doc_b_id,
    text_a,
    text_b,
):
    suffix = uuid.uuid4().hex
    with factory() as session:
        session.add_all(
            [
                Organization(id=org_a_id, name=f"Real pipeline A {suffix}"),
                Organization(id=org_b_id, name=f"Real pipeline B {suffix}"),
                User(
                    id=user_a_id,
                    email=f"real-pipeline-a-{suffix}@example.com",
                    password_hash="test-only-password-hash",
                ),
                User(
                    id=user_b_id,
                    email=f"real-pipeline-b-{suffix}@example.com",
                    password_hash="test-only-password-hash",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                Membership(user_id=user_a_id, organization_id=org_a_id, role=ROLE_MEMBER),
                Membership(user_id=user_b_id, organization_id=org_b_id, role=ROLE_MEMBER),
                Document(
                    id=doc_a_id,
                    organization_id=org_a_id,
                    uploaded_by_user_id=user_a_id,
                    original_filename="a.txt",
                    content_type="text/plain",
                    size_bytes=len(text_a.encode("utf-8")),
                    visibility=VISIBILITY_ORG,
                    status=STATUS_PENDING,
                ),
                Document(
                    id=doc_b_id,
                    organization_id=org_b_id,
                    uploaded_by_user_id=user_b_id,
                    original_filename="b.txt",
                    content_type="text/plain",
                    size_bytes=len(text_b.encode("utf-8")),
                    visibility=VISIBILITY_ORG,
                    status=STATUS_PENDING,
                ),
            ]
        )
        session.commit()


def _ingest(factory, document_id, text, index, embedding_provider):
    with factory() as session:
        return ingest_document(
            document_id,
            text.encode("utf-8"),
            persistence=SQLAlchemyIngestionPersistence(session),
            embedding_provider=embedding_provider,
            vector_index=index,
            chunk_size_chars=1000,
            chunk_overlap_chars=0,
        )


def _cleanup_database(
    factory, *, document_ids, user_ids, organization_ids
):
    """Delete only this test's records, attempting every FK-safe step.

    Failures are collected and re-raised after every step has been attempted.
    """
    statements = (
        (
            "document chunks",
            delete(StoredDocumentChunk).where(
                StoredDocumentChunk.document_id.in_(document_ids)
            ),
        ),
        ("documents", delete(Document).where(Document.id.in_(document_ids))),
        (
            "memberships",
            delete(Membership).where(Membership.user_id.in_(user_ids)),
        ),
        ("users", delete(User).where(User.id.in_(user_ids))),
        (
            "organizations",
            delete(Organization).where(Organization.id.in_(organization_ids)),
        ),
    )
    failures = []
    for label, statement in statements:
        try:
            with factory() as session:
                session.execute(statement)
                session.commit()
        except Exception as error:
            failures.append((label, error))
    if failures:
        raise ExceptionGroup(
            "Database cleanup failed: " + ", ".join(label for label, _ in failures),
            [error for _, error in failures],
        )


@pytest.mark.db
def test_real_postgres_and_qdrant_pipeline_excludes_other_tenant(
    verified_test_engine, qdrant_index
):
    factory = create_session_factory(verified_test_engine)
    suffix = uuid.uuid4().hex
    org_a_id = uuid.uuid4()
    org_b_id = uuid.uuid4()
    user_a_id = uuid.uuid4()
    user_b_id = uuid.uuid4()
    doc_a_id = uuid.uuid4()
    doc_b_id = uuid.uuid4()
    text_a = f"Organization A real pipeline answer {suffix}."
    text_b = f"Organization B real pipeline secret {suffix}."
    document_ids = (doc_a_id, doc_b_id)
    user_ids = (user_a_id, user_b_id)
    organization_ids = (org_a_id, org_b_id)

    try:
        _seed_pending_documents(
            factory,
            org_a_id=org_a_id,
            org_b_id=org_b_id,
            user_a_id=user_a_id,
            user_b_id=user_b_id,
            doc_a_id=doc_a_id,
            doc_b_id=doc_b_id,
            text_a=text_a,
            text_b=text_b,
        )

        embedding_provider = DeterministicEmbedding()
        ingestion_a = _ingest(
            factory, doc_a_id, text_a, qdrant_index, embedding_provider
        )
        ingestion_b = _ingest(
            factory, doc_b_id, text_b, qdrant_index, embedding_provider
        )
        assert ingestion_a.chunk_count == 1
        assert ingestion_b.chunk_count == 1

        a_hits = qdrant_index.search(
            query_vector=QUERY_VECTOR,
            organization_id=org_a_id,
            allowed_document_ids=frozenset({doc_a_id}),
            limit=5,
        )
        b_hits = qdrant_index.search(
            query_vector=QUERY_VECTOR,
            organization_id=org_b_id,
            allowed_document_ids=frozenset({doc_b_id}),
            limit=5,
        )
        assert {hit.point_id for hit in a_hits} == set(ingestion_a.vector_point_ids)
        assert {hit.point_id for hit in b_hits} == set(ingestion_b.vector_point_ids)
        assert {hit.document_id for hit in a_hits} == {doc_a_id}
        assert {hit.document_id for hit in b_hits} == {doc_b_id}

        with factory() as session:
            persisted = session.execute(
                select(StoredDocumentChunk).where(
                    StoredDocumentChunk.document_id.in_(document_ids)
                )
            ).scalars().all()
        rows_by_document = {row.document_id: row for row in persisted}
        row_a = rows_by_document[doc_a_id]
        row_b = rows_by_document[doc_b_id]
        assert row_a.text == text_a
        assert row_b.text == text_b

        vector_search = RecordingVectorSearch(qdrant_index)
        llm = RecordingLLM()
        with factory() as session:
            result = answer_question(
                TenantContext(
                    user_id=user_a_id,
                    organization_id=org_a_id,
                    role=ROLE_MEMBER,
                    db=None,
                ),
                "What is the answer?",
                repository=SQLAlchemyRetrievalRepository(session),
                embedding_provider=embedding_provider,
                vector_search=vector_search,
                llm_provider=llm,
            )

        assert len(vector_search.calls) == 1
        assert vector_search.calls[0]["organization_id"] == org_a_id
        assert vector_search.calls[0]["allowed_document_ids"] == frozenset(
            {doc_a_id}
        )
        assert result.answer == row_a.text
        assert result.citations == (
            Citation(str(row_a.document_id), row_a.chunk_index),
        )
        assert all(
            citation.document_id != str(doc_b_id) for citation in result.citations
        )
        assert len(llm.calls) == 1
        assert [chunk.document_id for chunk in llm.calls[0][1]] == [row_a.document_id]
        assert [chunk.text for chunk in llm.calls[0][1]] == [row_a.text]
        assert text_b not in result.answer
    finally:
        # Qdrant cleanup is owned by the fixture and still runs if this raises.
        _cleanup_database(
            factory,
            document_ids=document_ids,
            user_ids=user_ids,
            organization_ids=organization_ids,
        )
