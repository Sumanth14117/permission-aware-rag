from __future__ import annotations

from dataclasses import dataclass
import uuid
from uuid import UUID, uuid4

import pytest

from app.documents import TenantContext
from app.models.document import STATUS_READY, VISIBILITY_ORG, VISIBILITY_RESTRICTED
from app.models.membership import ROLE_MEMBER, ROLE_OWNER
from app.retrieval import (
    AuthorizedChunk,
    ReadScope,
    RetrievalAuthorizationError,
    RetrievalUnavailableError,
    VectorSearchResult,
    retrieve_chunks,
)


ORG_A = UUID("9d71d83f-8887-43cc-a7ca-89443d887e1f")
ORG_B = UUID("c699a142-d44a-4f7a-8d44-f6a8c599c065")
USER = UUID("d25803b6-e0af-4f4c-aee7-a1c35d3feb48")
DOC_A = UUID("f1654aad-2084-46dd-a75d-c5d3d106ce0c")
DOC_B = UUID("984830e4-d7c6-4fd8-a60d-c10d8286a51a")


@dataclass(frozen=True)
class FakeDocument:
    id: UUID
    organization_id: UUID
    visibility: str = VISIBILITY_ORG
    status: str = STATUS_READY


class FakeRepository:
    def __init__(self, *, role=ROLE_MEMBER):
        self.membership = (ORG_A, role)
        self.documents = {}
        self.grants = set()
        self.text = {}
        self.chunk_reads = []
        self.fail_scope = False
        self.fail_chunk = False

    def add_document(self, document, *texts):
        self.documents[document.id] = document
        for index, text in enumerate(texts):
            self.text[(document.id, index)] = text

    def load_read_scope(self, user_id):
        if self.fail_scope:
            raise RuntimeError("sensitive database error")
        if user_id != USER or self.membership is None:
            return None
        organization_id, role = self.membership
        readable = frozenset(
            document.id
            for document in self.documents.values()
            if document.organization_id == organization_id
            and document.status == STATUS_READY
            and (
                document.visibility == VISIBILITY_ORG
                or role == ROLE_OWNER
                or (document.id, user_id) in self.grants
            )
        )
        return ReadScope(user_id, organization_id, role, readable)

    def load_authorized_chunk(self, *, user_id, organization_id, document_id, chunk_index):
        self.chunk_reads.append((document_id, chunk_index))
        if self.fail_chunk:
            raise RuntimeError("sensitive storage error")
        scope = self.load_read_scope(user_id)
        document = self.documents.get(document_id)
        if (
            scope is None
            or scope.organization_id != organization_id
            or document is None
            or document.organization_id != organization_id
            or document.status != STATUS_READY
            or document_id not in scope.readable_document_ids
        ):
            return None
        text = self.text.get((document_id, chunk_index))
        if text is None:
            return None
        return AuthorizedChunk(
            organization_id,
            document_id,
            chunk_index,
            text,
            uuid.uuid5(document_id, f"chunk:{chunk_index}"),
        )


class FakeEmbedding:
    dimension = 2

    def __init__(self):
        self.calls = []
        self.fail = False

    def embed_batch(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("private query in provider error")
        return [[0.25, 0.75] for _ in texts]


class FakeVectorSearch:
    def __init__(self, results=()):
        self.results = tuple(results)
        self.calls = []
        self.fail = False
        self.before_return = None

    def search(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("private vector-service details")
        if self.before_return:
            self.before_return()
        return self.results


def candidate(document_id, organization_id=ORG_A, chunk_index=0):
    return VectorSearchResult(
        uuid.uuid5(document_id, f"chunk:{chunk_index}"),
        organization_id,
        document_id,
        chunk_index,
        0.9,
    )


def context(*, org=ORG_B, role=ROLE_OWNER):
    # Org and role intentionally disagree with the authoritative membership.
    return TenantContext(user_id=USER, organization_id=org, role=role, db=None)


def test_search_is_filtered_to_authoritative_membership_and_tenant():
    repository = FakeRepository(role=ROLE_MEMBER)
    repository.add_document(FakeDocument(DOC_A, ORG_A), "tenant A text")
    repository.add_document(FakeDocument(DOC_B, ORG_B), "tenant B secret")
    vector = FakeVectorSearch(
        [candidate(DOC_A), candidate(DOC_B, ORG_B)]
    )

    result = retrieve_chunks(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(), vector_search=vector,
    )

    assert [item.text for item in result] == ["tenant A text"]
    assert vector.calls[0]["organization_id"] == ORG_A
    assert vector.calls[0]["allowed_document_ids"] == frozenset({DOC_A})
    assert repository.chunk_reads == [(DOC_A, 0)]


def test_member_can_retrieve_granted_restricted_document_only():
    repository = FakeRepository(role=ROLE_MEMBER)
    repository.add_document(
        FakeDocument(DOC_A, ORG_A, VISIBILITY_RESTRICTED), "granted restricted text"
    )
    repository.add_document(
        FakeDocument(DOC_B, ORG_A, VISIBILITY_RESTRICTED), "ungranted secret"
    )
    repository.grants.add((DOC_A, USER))
    vector = FakeVectorSearch([candidate(DOC_A), candidate(DOC_B)])

    result = retrieve_chunks(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(), vector_search=vector,
    )

    assert [item.text for item in result] == ["granted restricted text"]
    assert vector.calls[0]["allowed_document_ids"] == frozenset({DOC_A})
    assert repository.chunk_reads == [(DOC_A, 0)]


def test_owner_can_retrieve_restricted_document_without_reader_grant():
    repository = FakeRepository(role=ROLE_OWNER)
    repository.add_document(
        FakeDocument(DOC_A, ORG_A, VISIBILITY_RESTRICTED), "owner-readable text"
    )

    result = retrieve_chunks(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(), vector_search=FakeVectorSearch([candidate(DOC_A)]),
    )

    assert [item.text for item in result] == ["owner-readable text"]


def test_permission_is_rechecked_after_vector_search_before_text_is_loaded():
    repository = FakeRepository(role=ROLE_MEMBER)
    repository.add_document(
        FakeDocument(DOC_A, ORG_A, VISIBILITY_RESTRICTED), "revoked private text"
    )
    repository.grants.add((DOC_A, USER))
    vector = FakeVectorSearch([candidate(DOC_A)])
    vector.before_return = lambda: repository.grants.clear()

    result = retrieve_chunks(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(), vector_search=vector,
    )

    assert result == []
    assert repository.chunk_reads == [(DOC_A, 0)]


def test_invalid_vector_candidates_are_discarded_before_repository_text_lookup():
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A), "authorized")
    repository.add_document(
        FakeDocument(DOC_B, ORG_A, VISIBILITY_RESTRICTED), "not in allowed scope"
    )
    vector = FakeVectorSearch([candidate(DOC_A), candidate(DOC_B)])

    result = retrieve_chunks(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(), vector_search=vector,
    )

    assert [item.text for item in result] == ["authorized"]
    assert repository.chunk_reads == [(DOC_A, 0)]


def test_stale_vector_point_id_is_discarded_after_authoritative_chunk_lookup():
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A), "current authorized text")
    stale = VectorSearchResult(uuid4(), ORG_A, DOC_A, 0, 0.9)

    result = retrieve_chunks(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(), vector_search=FakeVectorSearch([stale]),
    )

    assert result == []
    assert repository.chunk_reads == [(DOC_A, 0)]


@pytest.mark.parametrize(
    "malformed",
    [
        VectorSearchResult(uuid4(), ORG_A, [], 0, 0.9),
        VectorSearchResult(uuid4(), [], DOC_A, 0, 0.9),
        VectorSearchResult(uuid4(), ORG_A, DOC_A, True, 0.9),
        VectorSearchResult(uuid4(), ORG_A, DOC_A, 0, float("nan")),
    ],
)
def test_malformed_candidate_metadata_is_discarded_without_chunk_lookup(malformed):
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A), "authorized text")

    result = retrieve_chunks(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(), vector_search=FakeVectorSearch([malformed]),
    )

    assert result == []
    assert repository.chunk_reads == []


def test_no_readable_documents_returns_empty_without_embedding_or_search():
    repository = FakeRepository()
    embedding = FakeEmbedding()
    vector = FakeVectorSearch([candidate(DOC_A)])

    assert retrieve_chunks(
        context(), "question", repository=repository,
        embedding_provider=embedding, vector_search=vector,
    ) == []
    assert embedding.calls == []
    assert vector.calls == []


def test_empty_search_results_return_no_chunks():
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A), "not returned")

    assert retrieve_chunks(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(), vector_search=FakeVectorSearch(),
    ) == []


@pytest.mark.parametrize("failure", ["embedding", "vector", "repository"])
def test_provider_and_storage_failures_are_sanitized(failure):
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A), "private document text")
    embedding = FakeEmbedding()
    vector = FakeVectorSearch([candidate(DOC_A)])
    if failure == "embedding":
        embedding.fail = True
    elif failure == "vector":
        vector.fail = True
    else:
        repository.fail_chunk = True

    with pytest.raises(RetrievalUnavailableError) as error:
        retrieve_chunks(
            context(), "private query", repository=repository,
            embedding_provider=embedding, vector_search=vector,
        )
    assert "private" not in str(error.value)
    assert "sensitive" not in str(error.value)


def test_missing_membership_fails_before_embedding_or_search():
    repository = FakeRepository()
    repository.membership = None
    embedding = FakeEmbedding()
    vector = FakeVectorSearch()

    with pytest.raises(RetrievalAuthorizationError):
        retrieve_chunks(
            context(), "question", repository=repository,
            embedding_provider=embedding, vector_search=vector,
        )
    assert embedding.calls == []
    assert vector.calls == []
