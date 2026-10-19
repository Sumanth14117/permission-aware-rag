"""Fail-closed retrieval orchestration with authoritative permission checks.

The repository protocol intentionally requires both an authoritative read
scope and a per-chunk authorization recheck. Vector payloads are candidate
locators only; text is loaded from the repository after that recheck.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol, Sequence
import uuid

from sqlalchemy import select

from app.documents import TenantContext
from app.indexing import EmbeddingError, EmbeddingProvider, embed_query
from app.models import (
    Document,
    DocumentPermission,
    Membership,
    ROLE_MEMBER,
    ROLE_OWNER,
    StoredDocumentChunk,
)
from app.models.document import STATUS_READY, VISIBILITY_ORG


class RetrievalError(RuntimeError):
    """Base retrieval failure with no provider, query, or document details."""


class RetrievalUnavailableError(RetrievalError):
    """Embedding, vector search, or authoritative storage is unavailable."""


class RetrievalAuthorizationError(RetrievalError):
    """The authenticated user has no valid server-side organization scope."""


@dataclass(frozen=True)
class ReadScope:
    """Server-derived membership and readable, ready document identifiers."""

    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str
    readable_document_ids: frozenset[uuid.UUID]


@dataclass(frozen=True)
class VectorSearchResult:
    """Untrusted vector candidate metadata; it never contains trusted text."""

    point_id: uuid.UUID
    organization_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    score: float


@dataclass(frozen=True)
class AuthorizedChunk:
    """Chunk text returned by an authoritative repository permission check."""

    organization_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    text: str
    vector_point_id: uuid.UUID


@dataclass(frozen=True)
class RetrievedChunk:
    point_id: uuid.UUID
    organization_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    text: str
    score: float


class RetrievalRepository(Protocol):
    """Authoritative membership/document/permission and chunk-text boundary.

    Implementations must derive role and organization from persisted
    membership. ``load_authorized_chunk`` must re-read membership, document
    status/visibility, and restricted-reader grants before loading text.
    It must return None for any mismatch; it must never read vector payload
    text because vector metadata is not an authorization source.
    """

    def load_read_scope(self, user_id: uuid.UUID) -> ReadScope | None:
        """Return the user's current membership and readable READY documents.

        Include organization-visible documents and restricted documents for
        owners or explicitly granted members only.
        """

    def load_authorized_chunk(
        self,
        *,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        document_id: uuid.UUID,
        chunk_index: int,
    ) -> AuthorizedChunk | None:
        """Re-check authoritative access and fetch text for one candidate."""


class VectorSearcher(Protocol):
    def search(
        self,
        *,
        query_vector: Sequence[float],
        organization_id: uuid.UUID,
        allowed_document_ids: frozenset[uuid.UUID],
        limit: int,
    ) -> Sequence[VectorSearchResult]:
        """Search with mandatory tenant and allowed-document filters."""


class SQLAlchemyRetrievalRepository:
    """Load retrieval scope and text from current authoritative SQL records."""

    def __init__(self, db):
        self.db = db

    def load_read_scope(self, user_id: uuid.UUID) -> ReadScope | None:
        membership = self.db.execute(
            select(Membership).where(Membership.user_id == user_id)
        ).scalar_one_or_none()
        if membership is None:
            return None
        readable_query = select(Document.id).where(
            Document.organization_id == membership.organization_id,
            Document.status == STATUS_READY,
        )
        if membership.role != ROLE_OWNER:
            readable_query = readable_query.where(
                (Document.visibility == VISIBILITY_ORG)
                | Document.id.in_(
                    select(DocumentPermission.document_id).where(
                        DocumentPermission.organization_id == membership.organization_id,
                        DocumentPermission.user_id == user_id,
                    )
                )
            )
        readable_ids = frozenset(self.db.execute(readable_query).scalars().all())
        return ReadScope(
            user_id, membership.organization_id, membership.role, readable_ids
        )

    def load_authorized_chunk(
        self, *, user_id: uuid.UUID, organization_id: uuid.UUID,
        document_id: uuid.UUID, chunk_index: int,
    ) -> AuthorizedChunk | None:
        membership = self.db.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.organization_id == organization_id,
            )
        ).scalar_one_or_none()
        if membership is None:
            return None
        document = self.db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.organization_id == organization_id,
                Document.status == STATUS_READY,
            )
        ).scalar_one_or_none()
        if document is None:
            return None
        if document.visibility != VISIBILITY_ORG and membership.role != ROLE_OWNER:
            grant = self.db.execute(
                select(DocumentPermission.document_id).where(
                    DocumentPermission.document_id == document_id,
                    DocumentPermission.organization_id == organization_id,
                    DocumentPermission.user_id == user_id,
                )
            ).scalar_one_or_none()
            if grant is None:
                return None
        chunk = self.db.execute(
            select(StoredDocumentChunk).where(
                StoredDocumentChunk.document_id == document_id,
                StoredDocumentChunk.organization_id == organization_id,
                StoredDocumentChunk.chunk_index == chunk_index,
            )
        ).scalar_one_or_none()
        if chunk is None:
            return None
        return AuthorizedChunk(
            organization_id,
            document_id,
            chunk_index,
            chunk.text,
            chunk.vector_point_id,
        )


def _valid_scope(scope: ReadScope | None, user_id: uuid.UUID) -> bool:
    return bool(
        isinstance(scope, ReadScope)
        and scope.user_id == user_id
        and isinstance(scope.organization_id, uuid.UUID)
        and isinstance(scope.role, str)
        and scope.role in {ROLE_OWNER, ROLE_MEMBER}
        and isinstance(scope.readable_document_ids, frozenset)
        and all(isinstance(item, uuid.UUID) for item in scope.readable_document_ids)
    )


def _valid_candidate(candidate: VectorSearchResult, scope: ReadScope) -> bool:
    if not (
        isinstance(candidate.point_id, uuid.UUID)
        and isinstance(candidate.organization_id, uuid.UUID)
        and isinstance(candidate.document_id, uuid.UUID)
        and candidate.organization_id == scope.organization_id
        and candidate.document_id in scope.readable_document_ids
        and type(candidate.chunk_index) is int
        and candidate.chunk_index >= 0
        and isinstance(candidate.score, (int, float))
        and not isinstance(candidate.score, bool)
    ):
        return False
    try:
        return math.isfinite(float(candidate.score))
    except (OverflowError, TypeError, ValueError):
        return False


def retrieve_chunks(
    authenticated_user: TenantContext,
    question: str,
    *,
    repository: RetrievalRepository,
    embedding_provider: EmbeddingProvider,
    vector_search: VectorSearcher,
    limit: int = 5,
) -> list[RetrievedChunk]:
    """Return only currently authorized chunks relevant to a user question.

    Only ``authenticated_user.user_id`` is used as identity input; organization
    and role are re-derived from the authoritative repository. Vector results
    are pre-filtered by that scope and every returned chunk is independently
    re-authorized before its text is loaded. This function does not call an
    LLM.
    """
    if not isinstance(authenticated_user, TenantContext):
        raise RetrievalAuthorizationError("Authenticated user is unavailable")
    user_id = authenticated_user.user_id
    if not isinstance(user_id, uuid.UUID):
        raise RetrievalAuthorizationError("Authenticated user is unavailable")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be empty")
    if type(limit) is not int or limit < 1 or limit > 100:
        raise ValueError("retrieval limit is invalid")

    try:
        scope = repository.load_read_scope(user_id)
    except Exception:
        raise RetrievalUnavailableError("Retrieval storage is unavailable") from None
    if not _valid_scope(scope, user_id):
        raise RetrievalAuthorizationError("Organization membership is required")
    assert scope is not None
    if not scope.readable_document_ids:
        return []

    try:
        query_vector = embed_query(question, embedding_provider)
    except EmbeddingError:
        raise RetrievalUnavailableError("Question embedding is unavailable") from None

    try:
        candidates = tuple(
            vector_search.search(
                query_vector=query_vector,
                organization_id=scope.organization_id,
                allowed_document_ids=scope.readable_document_ids,
                limit=limit,
            )
        )
    except Exception:
        raise RetrievalUnavailableError("Vector search is unavailable") from None

    retrieved: list[RetrievedChunk] = []
    seen: set[tuple[uuid.UUID, int]] = set()
    for candidate in candidates:
        if not isinstance(candidate, VectorSearchResult) or not _valid_candidate(candidate, scope):
            continue
        identity = (candidate.document_id, candidate.chunk_index)
        if identity in seen:
            continue
        seen.add(identity)
        try:
            authorized = repository.load_authorized_chunk(
                user_id=user_id,
                organization_id=scope.organization_id,
                document_id=candidate.document_id,
                chunk_index=candidate.chunk_index,
            )
        except Exception:
            raise RetrievalUnavailableError("Retrieval storage is unavailable") from None
        if (
            not isinstance(authorized, AuthorizedChunk)
            or authorized.organization_id != scope.organization_id
            or authorized.document_id != candidate.document_id
            or authorized.chunk_index != candidate.chunk_index
            or authorized.vector_point_id != candidate.point_id
            or not isinstance(authorized.text, str)
            or not authorized.text.strip()
        ):
            continue
        retrieved.append(
            RetrievedChunk(
                point_id=candidate.point_id,
                organization_id=scope.organization_id,
                document_id=candidate.document_id,
                chunk_index=candidate.chunk_index,
                text=authorized.text,
                score=float(candidate.score),
            )
        )
    return retrieved
