from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.indexing import (
    EmbeddingError,
    IndexingError,
    SentenceTransformersEmbeddingProvider,
    VectorPoint,
    build_vector_points,
    embed_chunks,
    index_embedded_chunks,
)
from app.ingestion import DocumentChunk


ORG_ID = UUID("f69aaf8e-c041-4d09-90a8-6f32f629d888")
DOC_ID = UUID("1b08a4bb-e783-4e4b-a822-afd35f6e17f4")


def chunks(*texts: str) -> list[DocumentChunk]:
    return [DocumentChunk(DOC_ID, ORG_ID, i, text) for i, text in enumerate(texts)]


class FakeEmbeddingProvider:
    dimension = 3

    def __init__(self):
        self.batches: list[list[str]] = []

    def embed_batch(self, texts):
        self.batches.append(list(texts))
        return [[float(i), float(i + 1), float(i + 2)] for i, _ in enumerate(texts)]


class FailingProvider(FakeEmbeddingProvider):
    def embed_batch(self, texts):
        raise RuntimeError("provider error containing sensitive request data")


def test_embed_chunks_batches_in_order_and_preserves_chunk_identity():
    provider = FakeEmbeddingProvider()
    source = chunks("first passage", "second passage")

    embedded = embed_chunks(source, provider)

    assert provider.batches == [["first passage", "second passage"]]
    assert [item.chunk for item in embedded] == source
    assert [item.vector for item in embedded] == [(0.0, 1.0, 2.0), (1.0, 2.0, 3.0)]


def test_sentence_transformers_adapter_uses_batch_cpu_model_contract():
    class Encoded:
        def tolist(self):
            return [[0.6] * 384, [0.8] * 384]

    class Model:
        def __init__(self):
            self.call = None

        def get_sentence_embedding_dimension(self):
            return 384

        def encode(self, texts, **kwargs):
            self.call = (texts, kwargs)
            return Encoded()

    model = Model()
    provider = SentenceTransformersEmbeddingProvider("fake-local-model", model=model)

    assert provider.dimension == 384
    assert provider.embed_batch(["one", "two"]) == ((0.6,) * 384, (0.8,) * 384)
    assert model.call[0] == ["one", "two"]
    assert model.call[1]["normalize_embeddings"] is True
    assert model.call[1]["show_progress_bar"] is False


def test_sentence_transformers_adapter_rejects_non_384_dimension():
    class Model:
        def get_sentence_embedding_dimension(self):
            return 768

    provider = SentenceTransformersEmbeddingProvider("unexpected-model", model=Model())

    with pytest.raises(EmbeddingError, match="384 dimensions"):
        _ = provider.dimension


def test_empty_input_does_not_call_provider_or_index():
    provider = FakeEmbeddingProvider()
    assert embed_chunks([], provider) == []
    assert provider.batches == []
    assert index_embedded_chunks([], FakeIndex()) == []


def test_empty_chunk_is_rejected_before_provider_call():
    provider = FakeEmbeddingProvider()
    with pytest.raises(EmbeddingError, match="empty document chunk"):
        embed_chunks(chunks("  "), provider)
    assert provider.batches == []


def test_provider_failure_is_redacted():
    with pytest.raises(EmbeddingError, match="Embedding provider failed") as error:
        embed_chunks(chunks("private document text"), FailingProvider())
    assert "sensitive request data" not in str(error.value)
    assert "private document text" not in str(error.value)


@pytest.mark.parametrize(
    "provider",
    [
        type("WrongCount", (FakeEmbeddingProvider,), {"embed_batch": lambda self, texts: []})(),
        type("WrongDimension", (FakeEmbeddingProvider,), {"embed_batch": lambda self, texts: [[1.0]]})(),
        type("NonFinite", (FakeEmbeddingProvider,), {"embed_batch": lambda self, texts: [[float("nan")] * 3]})(),
        type("NonNumeric", (FakeEmbeddingProvider,), {"embed_batch": lambda self, texts: [[1.0, "bad", 3.0]]})(),
        type("Overflow", (FakeEmbeddingProvider,), {"embed_batch": lambda self, texts: [[10**10000, 1, 2]]})(),
    ],
)
def test_invalid_provider_vectors_are_rejected(provider):
    with pytest.raises(EmbeddingError):
        embed_chunks(chunks("passage"), provider)


def test_point_metadata_preserves_document_organization_and_chunk_identity():
    embedded = embed_chunks(chunks("passage"), FakeEmbeddingProvider())

    point = build_vector_points(embedded)[0]

    assert point.metadata == {
        "document_id": str(DOC_ID),
        "organization_id": str(ORG_ID),
        "chunk_index": 0,
    }
    assert point.id == build_vector_points(embedded)[0].id
    assert "text" not in point.metadata


class FakeIndex:
    def __init__(self, *, fail_after_first=False, fail_cleanup=False):
        self.points: dict[UUID, VectorPoint] = {}
        self.fail_after_first = fail_after_first
        self.fail_cleanup = fail_cleanup
        self.upsert_calls = 0
        self.delete_calls = []

    def upsert(self, points):
        self.upsert_calls += 1
        for point in points:
            self.points[point.id] = point
            if self.fail_after_first:
                raise RuntimeError("index failure")

    def delete_document_points(self, document_id, organization_id, point_ids):
        self.delete_calls.append((document_id, organization_id, list(point_ids)))
        if self.fail_cleanup:
            raise RuntimeError("cleanup failure")
        for point_id in point_ids:
            point = self.points.get(point_id)
            if point and point.metadata["document_id"] == str(document_id) and point.metadata[
                "organization_id"
            ] == str(organization_id):
                del self.points[point_id]


def test_indexing_upserts_one_batch_and_returns_stable_ids():
    embedded = embed_chunks(chunks("one", "two"), FakeEmbeddingProvider())
    index = FakeIndex()

    ids = index_embedded_chunks(embedded, index)

    assert ids == [point.id for point in index.points.values()]
    assert index.upsert_calls == 1
    assert len(index.points) == 2


def test_partial_index_failure_removes_all_document_points():
    embedded = embed_chunks(chunks("one", "two"), FakeEmbeddingProvider())
    index = FakeIndex(fail_after_first=True)

    with pytest.raises(IndexingError, match="partial points were removed"):
        index_embedded_chunks(embedded, index)

    assert index.points == {}
    assert index.delete_calls[0][0:2] == (DOC_ID, ORG_ID)
    assert len(index.delete_calls[0][2]) == 2


def test_cleanup_failure_reports_uncertain_partial_index_without_details():
    embedded = embed_chunks(chunks("private text"), FakeEmbeddingProvider())
    index = FakeIndex(fail_after_first=True, fail_cleanup=True)

    with pytest.raises(IndexingError, match="cleanup could not be confirmed") as error:
        index_embedded_chunks(embedded, index)

    assert "private text" not in str(error.value)
    assert "cleanup failure" not in str(error.value)


def test_index_batch_cannot_mix_documents_or_organizations():
    source = chunks("one", "two")
    source[1] = DocumentChunk(uuid4(), ORG_ID, 1, "two")
    embedded = embed_chunks(source, FakeEmbeddingProvider())
    index = FakeIndex()

    with pytest.raises(ValueError, match="one document and organization"):
        index_embedded_chunks(embedded, index)
    assert index.upsert_calls == 0
