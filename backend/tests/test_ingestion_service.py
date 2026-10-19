from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest
from sqlalchemy.sql.sqltypes import Text, Uuid

from app.ingestion_service import (
    DocumentSnapshot,
    IngestionServiceError,
    ingest_document,
    map_persisted_chunks,
)
from app.db.base import Base
from app.models import StoredDocumentChunk
from app.models.document import STATUS_FAILED, STATUS_PENDING, STATUS_PROCESSING, STATUS_READY


DOC_ID = UUID("ec246b2a-5a46-4ba9-9cab-4e08316ce4cb")
ORG_ID = UUID("8d65b0cc-0aae-47e3-a083-b9e2b16c4699")


class FakePersistence:
    def __init__(self, *, fail_save=False):
        self.status = STATUS_PENDING
        self.fail_save = fail_save
        self.snapshot = DocumentSnapshot(DOC_ID, ORG_ID, "text/plain")
        self.rows = []
        self.calls = []

    def claim_pending_document(self, document_id):
        self.calls.append("claim")
        assert document_id == DOC_ID
        if self.status != STATUS_PENDING:
            raise RuntimeError("not pending")
        self.status = STATUS_PROCESSING
        return self.snapshot

    def save_chunks_and_mark_ready(self, document, chunks):
        self.calls.append("save_ready")
        if self.fail_save:
            raise RuntimeError("database details must not escape")
        assert self.status == STATUS_PROCESSING
        self.rows = list(chunks)
        self.status = STATUS_READY

    def mark_failed(self, document):
        self.calls.append("failed")
        self.rows.clear()
        self.status = STATUS_FAILED


class FakeEmbedding:
    dimension = 2

    def embed_batch(self, texts):
        return [[float(index + 1), 0.5] for index, _ in enumerate(texts)]


class FakeVectorIndex:
    def __init__(self, *, fail=False):
        self.points = {}
        self.fail = fail
        self.deleted = []
        self.collection_dimensions = []

    def ensure_collection(self, dimension):
        self.collection_dimensions.append(dimension)

    def upsert(self, points):
        for point in points:
            self.points[point.id] = point
            if self.fail:
                raise RuntimeError("vector service detail")

    def delete_document_points(self, document_id, organization_id, point_ids):
        self.deleted.append((document_id, organization_id, tuple(point_ids)))
        for point_id in point_ids:
            self.points.pop(point_id, None)


def _run(persistence=None, index=None):
    persistence = persistence or FakePersistence()
    index = index or FakeVectorIndex()
    result = ingest_document(
        DOC_ID,
        b"abcdefgh",
        persistence=persistence,
        embedding_provider=FakeEmbedding(),
        vector_index=index,
        chunk_size_chars=5,
        chunk_overlap_chars=1,
    )
    return result, persistence, index


def test_ingestion_connects_extraction_chunking_embedding_persistence_and_indexing():
    result, persistence, index = _run()

    assert result.document_id == DOC_ID
    assert result.organization_id == ORG_ID
    assert result.chunk_count == 2
    assert persistence.status == STATUS_READY
    assert [row.text for row in persistence.rows] == ["abcde", "efgh"]
    assert [row.chunk_index for row in persistence.rows] == [0, 1]
    assert all(row.organization_id == ORG_ID for row in persistence.rows)
    assert {row.vector_point_id for row in persistence.rows} == set(index.points)
    assert index.collection_dimensions == [2]


def test_mapping_preserves_chunk_text_identity_and_vector_point_id():
    from app.indexing import EmbeddedChunk, build_vector_points
    from app.ingestion import DocumentChunk

    chunks = [
        DocumentChunk(DOC_ID, ORG_ID, 0, "chunk one"),
        DocumentChunk(DOC_ID, ORG_ID, 1, "chunk two"),
    ]
    embedded = [EmbeddedChunk(chunk, (0.1, 0.2)) for chunk in chunks]
    points = build_vector_points(embedded)

    rows = map_persisted_chunks(embedded, points)

    assert [(row.document_id, row.organization_id, row.chunk_index, row.text) for row in rows] == [
        (DOC_ID, ORG_ID, 0, "chunk one"),
        (DOC_ID, ORG_ID, 1, "chunk two"),
    ]
    assert [row.vector_point_id for row in rows] == [point.id for point in points]


def test_chunk_table_metadata_keeps_tenant_scoped_composite_identity():
    table = Base.metadata.tables["document_chunks"]
    assert StoredDocumentChunk.metadata is Base.metadata
    assert tuple(table.primary_key.columns.keys()) == (
        "document_id", "organization_id", "chunk_index"
    )
    assert isinstance(table.c.vector_point_id.type, Uuid)
    assert isinstance(table.c.text.type, Text)
    assert {
        tuple(element.target_fullname for element in constraint.elements)
        for constraint in table.foreign_key_constraints
    } == {("documents.id", "documents.organization_id")}
    assert {
        constraint.ondelete for constraint in table.foreign_key_constraints
    } == {"CASCADE"}


def test_chunk_mapping_rejects_vector_metadata_identity_drift():
    from app.indexing import EmbeddedChunk, build_vector_points
    from app.ingestion import DocumentChunk

    embedded = [EmbeddedChunk(DocumentChunk(DOC_ID, ORG_ID, 0, "chunk"), (0.1, 0.2))]
    point = build_vector_points(embedded)[0]
    tampered = replace(point, metadata={**point.metadata, "organization_id": str(UUID(int=0))})

    with pytest.raises(ValueError, match="identity"):
        map_persisted_chunks(embedded, [tampered])


def test_vector_failure_marks_document_failed_and_compensates_partial_points():
    persistence = FakePersistence()
    index = FakeVectorIndex(fail=True)

    with pytest.raises(IngestionServiceError, match="Document ingestion failed"):
        ingest_document(
            DOC_ID, b"abcdefgh", persistence=persistence,
            embedding_provider=FakeEmbedding(), vector_index=index,
            chunk_size_chars=5, chunk_overlap_chars=1,
        )

    assert persistence.status == STATUS_FAILED
    assert index.points == {}
    assert index.deleted[0][0:2] == (DOC_ID, ORG_ID)


def test_database_persistence_failure_removes_indexed_points_and_fails_closed():
    persistence = FakePersistence(fail_save=True)
    index = FakeVectorIndex()

    with pytest.raises(IngestionServiceError) as error:
        ingest_document(
            DOC_ID, b"abcdefgh", persistence=persistence,
            embedding_provider=FakeEmbedding(), vector_index=index,
            chunk_size_chars=5, chunk_overlap_chars=1,
        )

    assert "database details" not in str(error.value)
    assert persistence.status == STATUS_FAILED
    assert index.points == {}


def test_provider_failure_marks_document_failed_without_index_writes():
    class BrokenEmbedding(FakeEmbedding):
        def embed_batch(self, texts):
            raise RuntimeError("private chunk text")

    persistence = FakePersistence()
    index = FakeVectorIndex()
    with pytest.raises(IngestionServiceError) as error:
        ingest_document(
            DOC_ID, b"secret document", persistence=persistence,
            embedding_provider=BrokenEmbedding(), vector_index=index,
            chunk_size_chars=50, chunk_overlap_chars=2,
        )
    assert "secret document" not in str(error.value)
    assert persistence.status == STATUS_FAILED
    assert index.points == {}
