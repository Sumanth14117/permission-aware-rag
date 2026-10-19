from __future__ import annotations

from uuid import UUID

from app.answering import Citation, answer_question
from app.ingestion_service import DocumentSnapshot, ingest_document
from app.models.document import STATUS_PENDING, STATUS_PROCESSING
from app.retrieval import VectorSearchResult
from tests.test_answering import FakeLLM
from tests.test_ingestion_service import FakeEmbedding, FakePersistence, FakeVectorIndex
from tests.test_retrieval import (
    DOC_A, DOC_B, ORG_A, ORG_B, FakeDocument, FakeRepository, FakeVectorSearch, context,
)


class ChunkEchoLLM(FakeLLM):
    """Fake LLM that builds its answer from the chunks it actually receives."""

    def generate(self, *, question, chunks, source_markers):
        self.calls.append((question, chunks, source_markers))
        return f"{source_markers[0]}{chunks[0].text}"


class PersistenceForDocument(FakePersistence):
    """Reuse the ingestion fake with a caller-selected document and tenant."""

    def __init__(self, document_id: UUID, organization_id: UUID):
        super().__init__()
        self.snapshot = DocumentSnapshot(document_id, organization_id, "text/plain")

    def claim_pending_document(self, document_id):
        self.calls.append("claim")
        if document_id != self.snapshot.id or self.status != STATUS_PENDING:
            raise RuntimeError("not pending")
        self.status = STATUS_PROCESSING
        return self.snapshot


def candidates_from_index(index: FakeVectorIndex) -> list[VectorSearchResult]:
    """Turn indexed points into the candidates the retrieval boundary receives."""
    return [
        VectorSearchResult(
            point.id,
            UUID(point.metadata["organization_id"]),
            UUID(point.metadata["document_id"]),
            point.metadata["chunk_index"],
            0.9,
        )
        for point in index.points.values()
    ]


def repository_from_rows(rows) -> FakeRepository:
    """Populate the retrieval fake from the actual rows persisted by ingestion."""
    repository = FakeRepository()
    for row in rows:
        repository.documents[row.document_id] = FakeDocument(
            row.document_id, row.organization_id
        )
        repository.text[(row.document_id, row.chunk_index)] = row.text
    return repository


def ingest_text(document_id, organization_id, text, index):
    persistence = PersistenceForDocument(document_id, organization_id)
    ingest_document(
        document_id,
        text.encode("utf-8"),
        persistence=persistence,
        embedding_provider=FakeEmbedding(),
        vector_index=index,
        chunk_size_chars=1000,
        chunk_overlap_chars=0,
    )
    return persistence


def test_ingested_document_flows_through_retrieval_to_cited_answer():
    index = FakeVectorIndex()
    text_a = "Organization A answer text."
    text_b = "Organization B secret text."

    persistence_a = ingest_text(DOC_A, ORG_A, text_a, index)
    persistence_b = ingest_text(DOC_B, ORG_B, text_b, index)
    rows_a = persistence_a.rows
    rows_b = persistence_b.rows
    row_a = rows_a[0]
    row_b = rows_b[0]

    assert len(index.points) == 2
    assert {UUID(point.metadata["organization_id"]) for point in index.points.values()} == {
        ORG_A, ORG_B
    }
    assert [row.text for row in rows_a] == [text_a]
    assert [row.text for row in rows_b] == [text_b]
    assert {row.vector_point_id for row in rows_a + rows_b} == set(index.points)

    repository = repository_from_rows(rows_a + rows_b)
    assert repository.text[(row_b.document_id, row_b.chunk_index)] == text_b
    candidates = candidates_from_index(index)
    assert {candidate.document_id for candidate in candidates} == {DOC_A, DOC_B}
    vector = FakeVectorSearch(candidates)
    llm = ChunkEchoLLM()

    result = answer_question(
        context(),
        "What is the answer?",
        repository=repository,
        embedding_provider=FakeEmbedding(),
        vector_search=vector,
        llm_provider=llm,
    )

    assert {item.document_id for item in vector.results} == {DOC_A, DOC_B}
    assert len(vector.calls) == 1
    assert vector.calls[0]["organization_id"] == ORG_A
    assert vector.calls[0]["allowed_document_ids"] == frozenset({row_a.document_id})
    assert repository.chunk_reads == [(row_a.document_id, row_a.chunk_index)]

    assert result.answer == text_a
    assert result.citations == (Citation(str(row_a.document_id), row_a.chunk_index),)
    assert all(citation.document_id != str(row_b.document_id) for citation in result.citations)
    assert len(llm.calls) == 1
    assert [chunk.text for chunk in llm.calls[0][1]] == [text_a]
    assert all(chunk.document_id != row_b.document_id for chunk in llm.calls[0][1])
    assert text_b not in result.answer


def test_empty_readable_scope_skips_vector_search_and_llm():
    index = FakeVectorIndex()
    ingest_text(DOC_B, ORG_B, "Organization B secret text.", index)

    vector = FakeVectorSearch(candidates_from_index(index))
    llm = FakeLLM(answer="unused")

    result = answer_question(
        context(),
        "question",
        repository=FakeRepository(),
        embedding_provider=FakeEmbedding(),
        vector_search=vector,
        llm_provider=llm,
    )

    assert result.citations == ()
    assert "could not find" in result.answer
    assert vector.calls == []
    assert llm.calls == []
