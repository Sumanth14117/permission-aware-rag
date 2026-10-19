"""Synchronous document extraction, chunk persistence, embedding, and indexing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence
import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.indexing import (
    EmbeddedChunk,
    EmbeddingProvider,
    VectorIndex,
    VectorPoint,
    build_vector_points,
    embed_chunks,
    index_embedded_chunks,
)
from app.ingestion import chunk_document_text, extract_document_text
from app.models import Document, StoredDocumentChunk
from app.models.document import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PROCESSING,
    STATUS_READY,
)


class IngestionConflictError(RuntimeError):
    """The persisted document is missing or is not pending ingestion."""


class IngestionServiceError(RuntimeError):
    """Ingestion failed; details and document contents are intentionally hidden."""


@dataclass(frozen=True)
class DocumentSnapshot:
    id: uuid.UUID
    organization_id: uuid.UUID
    content_type: str


@dataclass(frozen=True)
class IngestionResult:
    document_id: uuid.UUID
    organization_id: uuid.UUID
    chunk_count: int
    vector_point_ids: tuple[uuid.UUID, ...]


class IngestionPersistence(Protocol):
    def claim_pending_document(self, document_id: uuid.UUID) -> DocumentSnapshot:
        """Atomically transition a persisted pending document to processing."""

    def save_chunks_and_mark_ready(
        self, document: DocumentSnapshot, chunks: Sequence[StoredDocumentChunk]
    ) -> None:
        """Persist all chunk rows and mark the same document ready atomically."""

    def mark_failed(self, document: DocumentSnapshot) -> None:
        """Mark processing failed and remove any partially persisted chunk rows."""


class SQLAlchemyIngestionPersistence:
    """PostgreSQL persistence implementation; methods own their transactions."""

    def __init__(self, db: Session):
        self.db = db

    def claim_pending_document(self, document_id: uuid.UUID) -> DocumentSnapshot:
        with self.db.begin():
            document = self.db.execute(
                select(Document)
                .where(Document.id == document_id)
                .with_for_update()
            ).scalar_one_or_none()
            if document is None or document.status != STATUS_PENDING:
                raise IngestionConflictError("Document is not available for ingestion")
            document.status = STATUS_PROCESSING
            self.db.flush()
            return DocumentSnapshot(document.id, document.organization_id, document.content_type)

    def save_chunks_and_mark_ready(
        self, document: DocumentSnapshot, chunks: Sequence[StoredDocumentChunk]
    ) -> None:
        with self.db.begin():
            persisted = self.db.execute(
                select(Document)
                .where(
                    Document.id == document.id,
                    Document.organization_id == document.organization_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if persisted is None or persisted.status != STATUS_PROCESSING:
                raise IngestionConflictError("Document processing state changed")
            self.db.add_all(chunks)
            persisted.status = STATUS_READY
            self.db.flush()

    def mark_failed(self, document: DocumentSnapshot) -> None:
        with self.db.begin():
            persisted = self.db.execute(
                select(Document)
                .where(
                    Document.id == document.id,
                    Document.organization_id == document.organization_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if persisted is not None and persisted.status in {
                STATUS_PROCESSING,
                STATUS_READY,
            }:
                self.db.execute(
                    delete(StoredDocumentChunk).where(
                        StoredDocumentChunk.document_id == document.id,
                        StoredDocumentChunk.organization_id == document.organization_id,
                    )
                )
                persisted.status = STATUS_FAILED
                self.db.flush()


def map_persisted_chunks(
    chunks: Sequence[EmbeddedChunk], points: Sequence[VectorPoint]
) -> list[StoredDocumentChunk]:
    """Map ingestion output to ORM rows, rejecting identity/metadata drift."""
    if len(chunks) != len(points):
        raise ValueError("chunk and vector point counts do not match")
    expected = build_vector_points(chunks)
    if list(points) != expected:
        raise ValueError("vector point identity does not match its source chunk")
    return [
        StoredDocumentChunk(
            document_id=chunk.chunk.document_id,
            organization_id=chunk.chunk.organization_id,
            chunk_index=chunk.chunk.chunk_index,
            text=chunk.chunk.text,
            vector_point_id=point.id,
        )
        for chunk, point in zip(chunks, points, strict=True)
    ]


def ingest_document(
    document_id: uuid.UUID,
    content: bytes,
    *,
    persistence: IngestionPersistence,
    embedding_provider: EmbeddingProvider,
    vector_index: VectorIndex,
    chunk_size_chars: int,
    chunk_overlap_chars: int,
) -> IngestionResult:
    """Process one server-persisted pending document as a single service flow.

    The document's organization and media type come from persisted metadata,
    never request arguments. Vector writes happen before the final SQL commit;
    SQL failure triggers compensating point deletion. Retrieval must still
    require PostgreSQL status and permission checks before exposing any text.
    """
    try:
        document = persistence.claim_pending_document(document_id)
    except IngestionConflictError:
        raise
    except Exception:
        raise IngestionServiceError("Document ingestion is unavailable") from None

    indexed_point_ids: tuple[uuid.UUID, ...] = ()
    try:
        extracted = extract_document_text(content, document.content_type)
        # The snapshot's IDs came from persisted metadata; no organization ID
        # is accepted from the request or passed into this service separately.
        chunks = chunk_document_text(
            document,
            extracted,
            chunk_size_chars=chunk_size_chars,
            chunk_overlap_chars=chunk_overlap_chars,
        )
        provision = getattr(vector_index, "ensure_collection", None)
        if callable(provision):
            provision(embedding_provider.dimension)
        embedded = embed_chunks(chunks, embedding_provider)
        points = build_vector_points(embedded)
        indexed_point_ids = tuple(index_embedded_chunks(embedded, vector_index))
        rows = map_persisted_chunks(embedded, points)
        persistence.save_chunks_and_mark_ready(document, rows)
        return IngestionResult(
            document.id, document.organization_id, len(rows), indexed_point_ids
        )
    except Exception:
        cleanup_confirmed = True
        if indexed_point_ids:
            try:
                vector_index.delete_document_points(
                    document.id, document.organization_id, indexed_point_ids
                )
            except Exception:
                cleanup_confirmed = False
        state_confirmed = True
        try:
            persistence.mark_failed(document)
        except Exception:
            state_confirmed = False
        if not cleanup_confirmed or not state_confirmed:
            raise IngestionServiceError(
                "Document ingestion failed and recovery could not be confirmed"
            ) from None
        raise IngestionServiceError("Document ingestion failed") from None
