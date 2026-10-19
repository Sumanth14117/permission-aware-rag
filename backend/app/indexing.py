"""Embedding contracts and concrete Sentence Transformers/Qdrant adapters.

Vector metadata is only a search locator. Permission decisions remain in
PostgreSQL and must be rechecked by the retrieval repository before text use.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Protocol, Sequence
import uuid

from app.ingestion import DocumentChunk

EXPECTED_EMBEDDING_DIMENSION = 384


class EmbeddingError(ValueError):
    """Embedding failed or returned data that does not meet the contract."""


class IndexingError(RuntimeError):
    """Vector indexing failed; the message does not contain provider data."""


class EmbeddingProvider(Protocol):
    """Batch embedding contract implemented by local or remote providers."""

    @property
    def dimension(self) -> int:
        """The fixed number of values returned for each embedding."""

    def embed_batch(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        """Return one vector per input, preserving input order."""


class VectorIndex(Protocol):
    """Minimal adapter contract for an external or in-memory vector index."""

    def upsert(self, points: Sequence["VectorPoint"]) -> None:
        """Upsert a batch. A failed call may have partially applied."""

    def delete_document_points(
        self,
        document_id: uuid.UUID,
        organization_id: uuid.UUID,
        point_ids: Sequence[uuid.UUID],
    ) -> None:
        """Idempotently remove these points, scoped to their document/tenant."""


class SentenceTransformersEmbeddingProvider:
    """Lazy CPU adapter for the architecture's local Sentence Transformers model."""

    def __init__(self, model_name: str, *, batch_size: int = 32, model=None):
        if not model_name or type(batch_size) is not int or batch_size < 1:
            raise ValueError("embedding configuration is invalid")
        self.model_name = model_name
        self.batch_size = batch_size
        self._model = model

    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise EmbeddingError("Sentence Transformers support is unavailable") from None
            try:
                self._model = SentenceTransformer(self.model_name, device="cpu")
            except Exception:
                raise EmbeddingError("Embedding model could not be loaded") from None
        return self._model

    @property
    def dimension(self) -> int:
        try:
            dimension = self._get_model().get_sentence_embedding_dimension()
        except EmbeddingError:
            raise
        except Exception:
            raise EmbeddingError("Embedding model configuration is unavailable") from None
        if type(dimension) is not int or dimension != EXPECTED_EMBEDDING_DIMENSION:
            raise EmbeddingError(
                f"Embedding model must return {EXPECTED_EMBEDDING_DIMENSION} dimensions"
            )
        return dimension

    def embed_batch(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not texts:
            return ()
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise EmbeddingError("Embedding input is invalid")
        try:
            encoded = self._get_model().encode(
                list(texts),
                batch_size=self.batch_size,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            return tuple(tuple(row) for row in encoded.tolist())
        except EmbeddingError:
            raise
        except Exception:
            raise EmbeddingError("Embedding provider failed") from None


class QdrantVectorIndex:
    """Concrete synchronous Qdrant adapter for indexing and filtered search.

    The collection is never created implicitly. Call ``ensure_collection`` as
    an explicit provisioning step with the embedding model's dimension.
    """

    def __init__(self, client, collection_name: str):
        if not collection_name:
            raise ValueError("Qdrant collection name is required")
        self.client = client
        self.collection_name = collection_name

    @classmethod
    def from_settings(cls, settings) -> "QdrantVectorIndex":
        try:
            from qdrant_client import QdrantClient
        except ImportError:
            raise IndexingError("Qdrant support is unavailable") from None
        api_key = (
            settings.qdrant_api_key.get_secret_value()
            if settings.qdrant_api_key is not None
            else None
        )
        try:
            client = QdrantClient(url=settings.qdrant_url, api_key=api_key)
        except Exception:
            raise IndexingError("Qdrant client could not be initialized") from None
        return cls(client, settings.qdrant_collection_name)

    def ensure_collection(self, dimension: int) -> None:
        """Explicitly create/validate the collection and tenant filter indexes."""
        if type(dimension) is not int or dimension < 1:
            raise ValueError("vector dimension is invalid")
        try:
            from qdrant_client import models
        except ImportError:
            raise IndexingError("Qdrant support is unavailable") from None
        try:
            if not self.client.collection_exists(self.collection_name):
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=dimension, distance=models.Distance.COSINE
                    ),
                )
            else:
                info = self.client.get_collection(self.collection_name)
                vectors = info.config.params.vectors
                if not hasattr(vectors, "size") or vectors.size != dimension:
                    raise IndexingError("Qdrant collection dimension does not match the model")
            for field in ("organization_id", "document_id"):
                self.client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name=field,
                    field_schema=models.PayloadSchemaType.KEYWORD,
                )
        except IndexingError:
            raise
        except Exception:
            raise IndexingError("Qdrant collection is unavailable") from None

    def upsert(self, points: Sequence[VectorPoint]) -> None:
        if not points:
            return
        try:
            from qdrant_client.models import PointStruct
            self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    PointStruct(
                        id=str(point.id),
                        vector=list(point.vector),
                        payload=dict(point.metadata),
                    )
                    for point in points
                ],
                wait=True,
            )
        except ImportError:
            raise IndexingError("Qdrant support is unavailable") from None
        except Exception:
            raise IndexingError("Vector indexing failed") from None

    def delete_document_points(
        self,
        document_id: uuid.UUID,
        organization_id: uuid.UUID,
        point_ids: Sequence[uuid.UUID],
    ) -> None:
        if not point_ids:
            return
        try:
            from qdrant_client import models
            selector = models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.HasIdCondition(has_id=[str(point_id) for point_id in point_ids]),
                        models.FieldCondition(
                            key="organization_id",
                            match=models.MatchValue(value=str(organization_id)),
                        ),
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=str(document_id)),
                        ),
                    ]
                )
            )
            self.client.delete(
                collection_name=self.collection_name, points_selector=selector, wait=True
            )
        except ImportError:
            raise IndexingError("Qdrant support is unavailable") from None
        except Exception:
            raise IndexingError("Vector cleanup failed") from None

    def search(
        self,
        *,
        query_vector: Sequence[float],
        organization_id: uuid.UUID,
        allowed_document_ids: frozenset[uuid.UUID],
        limit: int,
    ):
        if not allowed_document_ids:
            return ()
        if type(limit) is not int or limit < 1:
            raise ValueError("search limit is invalid")
        try:
            from qdrant_client import models
            from app.retrieval import VectorSearchResult
            query_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="organization_id",
                        match=models.MatchValue(value=str(organization_id)),
                    ),
                    models.FieldCondition(
                        key="document_id",
                        match=models.MatchAny(
                            any=[str(document_id) for document_id in allowed_document_ids]
                        ),
                    ),
                ]
            )
            response = self.client.query_points(
                collection_name=self.collection_name,
                query=list(query_vector),
                query_filter=query_filter,
                limit=limit,
                with_payload=["organization_id", "document_id", "chunk_index"],
                with_vectors=False,
            )
            results = []
            for point in response.points:
                payload = point.payload or {}
                try:
                    chunk_index = payload["chunk_index"]
                    if type(chunk_index) is not int or chunk_index < 0:
                        continue
                    results.append(
                        (
                            uuid.UUID(str(point.id)),
                            uuid.UUID(str(payload["organization_id"])),
                            uuid.UUID(str(payload["document_id"])),
                            chunk_index,
                            float(point.score),
                        )
                    )
                except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
                    # Malformed payloads are discarded; they never carry text.
                    continue
            return tuple(
                VectorSearchResult(point_id, org_id, doc_id, chunk_index, score)
                for point_id, org_id, doc_id, chunk_index, score in results
            )
        except ImportError:
            raise IndexingError("Qdrant support is unavailable") from None
        except IndexingError:
            raise
        except Exception:
            raise IndexingError("Vector search failed") from None

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            raise IndexingError("Qdrant client could not be closed") from None


@dataclass(frozen=True)
class EmbeddedChunk:
    chunk: DocumentChunk
    vector: tuple[float, ...]


@dataclass(frozen=True)
class VectorPoint:
    """Vector and non-authoritative lookup metadata; chunk text is not copied."""

    id: uuid.UUID
    vector: tuple[float, ...]
    metadata: Mapping[str, str | int]


def _validated_vector(raw: Sequence[float], dimension: int) -> tuple[float, ...]:
    try:
        values = tuple(raw)
    except (TypeError, ValueError):
        raise EmbeddingError("Embedding provider returned an invalid vector") from None
    if len(values) != dimension:
        raise EmbeddingError("Embedding provider returned an unexpected dimension")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise EmbeddingError("Embedding provider returned an invalid vector")
    try:
        vector = tuple(float(value) for value in values)
    except (OverflowError, TypeError, ValueError):
        raise EmbeddingError("Embedding provider returned an invalid vector") from None
    if any(not math.isfinite(value) for value in vector):
        raise EmbeddingError("Embedding provider returned an invalid vector")
    return vector


def embed_chunks(
    chunks: Sequence[DocumentChunk], provider: EmbeddingProvider
) -> list[EmbeddedChunk]:
    """Embed chunks in one ordered provider call and validate its full result."""
    if not chunks:
        return []
    if any(not isinstance(chunk.text, str) or not chunk.text.strip() for chunk in chunks):
        raise EmbeddingError("Cannot embed an empty document chunk")

    try:
        dimension = provider.dimension
        if type(dimension) is not int or dimension <= 0:
            raise ValueError
        raw_vectors = tuple(provider.embed_batch([chunk.text for chunk in chunks]))
    except Exception:
        raise EmbeddingError("Embedding provider failed") from None

    if len(raw_vectors) != len(chunks):
        raise EmbeddingError("Embedding provider returned an unexpected batch size")
    return [
        EmbeddedChunk(chunk, _validated_vector(raw, dimension))
        for chunk, raw in zip(chunks, raw_vectors, strict=True)
    ]


def embed_query(text: str, provider: EmbeddingProvider) -> tuple[float, ...]:
    """Embed one non-empty retrieval query using the shared provider contract."""
    if not isinstance(text, str) or not text.strip():
        raise EmbeddingError("Retrieval question is empty")
    try:
        dimension = provider.dimension
        if type(dimension) is not int or dimension <= 0:
            raise ValueError
        vectors = tuple(provider.embed_batch([text]))
    except Exception:
        raise EmbeddingError("Embedding provider failed") from None
    if len(vectors) != 1:
        raise EmbeddingError("Embedding provider returned an unexpected batch size")
    return _validated_vector(vectors[0], dimension)


def _point_id(chunk: DocumentChunk) -> uuid.UUID:
    if (
        not isinstance(chunk.document_id, uuid.UUID)
        or not isinstance(chunk.organization_id, uuid.UUID)
        or type(chunk.chunk_index) is not int
        or chunk.chunk_index < 0
    ):
        raise ValueError("chunk identity is invalid")
    return uuid.uuid5(chunk.document_id, f"chunk:{chunk.chunk_index}")


def build_vector_points(chunks: Sequence[EmbeddedChunk]) -> list[VectorPoint]:
    """Build deterministic index points with server-derived tenant metadata."""
    points: list[VectorPoint] = []
    for embedded in chunks:
        chunk = embedded.chunk
        if not embedded.vector or any(not math.isfinite(value) for value in embedded.vector):
            raise ValueError("embedding vector is invalid")
        point_id = _point_id(chunk)
        points.append(
            VectorPoint(
                id=point_id,
                vector=embedded.vector,
                metadata={
                    "document_id": str(chunk.document_id),
                    "organization_id": str(chunk.organization_id),
                    "chunk_index": chunk.chunk_index,
                },
            )
        )
    return points


def index_embedded_chunks(
    chunks: Sequence[EmbeddedChunk], index: VectorIndex
) -> list[uuid.UUID]:
    """Upsert one document's chunks and compensate if a batch partially fails.

    Point IDs are deterministic, so cleanup can safely target every intended
    point even if the adapter failed after applying only part of the batch.
    """
    if not chunks:
        return []
    points = build_vector_points(chunks)
    first = chunks[0].chunk
    if any(
        item.chunk.document_id != first.document_id
        or item.chunk.organization_id != first.organization_id
        for item in chunks
    ):
        raise ValueError("an indexing batch must contain one document and organization")

    point_ids = [point.id for point in points]
    try:
        index.upsert(points)
    except Exception:
        try:
            index.delete_document_points(first.document_id, first.organization_id, point_ids)
        except Exception:
            raise IndexingError(
                "Vector indexing failed and partial-point cleanup could not be confirmed"
            ) from None
        raise IndexingError("Vector indexing failed; partial points were removed") from None
    return point_ids
