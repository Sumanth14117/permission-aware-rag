import pytest

from app.answering import Answer, Citation, MockLLMProvider, answer_question
from tests.test_retrieval import (
    DOC_A, DOC_B, ORG_A, FakeDocument, FakeEmbedding, FakeRepository, FakeVectorSearch,
    candidate, context,
)


class FakeLLM:
    def __init__(self, answer=None, build=None):
        self.answer = answer
        self.build = build
        self.calls = []

    def generate(self, *, question, chunks, source_markers):
        self.calls.append((question, chunks, source_markers))
        if self.build is not None:
            return self.build(source_markers)
        return self.answer


def marker_with_index(marker, index):
    """Return a request marker with its final index replaced."""
    return f"{marker.rsplit(':', 1)[0]}:{index}]]"


def test_answer_uses_authorized_chunks_and_returns_document_chunk_citations():
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A), "authorized source")
    vector = FakeVectorSearch([candidate(DOC_A)])
    llm = FakeLLM(build=lambda markers: f"The answer is 42.{markers[0]}")

    result = answer_question(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(), vector_search=vector, llm_provider=llm,
    )

    assert result == Answer("The answer is 42.", (Citation(str(DOC_A), 0),))
    assert len(llm.calls) == 1
    assert [chunk.text for chunk in llm.calls[0][1]] == ["authorized source"]
    assert len(llm.calls[0][2]) == 1


def test_citations_resolve_only_supplied_source_markers_in_reference_order():
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A), "first source", "second source")
    vector = FakeVectorSearch(
        [candidate(DOC_A, chunk_index=1), candidate(DOC_A, chunk_index=0)]
    )
    llm = FakeLLM(build=lambda markers: f"Second{markers[0]}, first{markers[1]}.")

    result = answer_question(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(), vector_search=vector, llm_provider=llm,
    )

    assert result == Answer(
        "Second, first.",
        (Citation(str(DOC_A), 1), Citation(str(DOC_A), 0)),
    )
    assert [chunk.text for chunk in llm.calls[0][1]] == ["second source", "first source"]


def test_unknown_markers_and_invented_identifiers_are_not_cited():
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A), "authorized source")
    llm = FakeLLM(build=lambda markers: (
        f"Authorized{markers[0]}, unknown{marker_with_index(markers[0], 9)}, "
        "and invented [document_id=deadbeef, chunk_index=7]."
    ))

    result = answer_question(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(),
        vector_search=FakeVectorSearch([candidate(DOC_A)]),
        llm_provider=llm,
    )

    unknown_marker = marker_with_index(llm.calls[0][2][0], 9)
    assert result.citations == (Citation(str(DOC_A), 0),)
    assert unknown_marker not in result.answer


def test_unauthorized_chunks_never_enter_the_prompt_or_citations():
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A), "authorized source")
    repository.add_document(
        FakeDocument(DOC_B, ORG_A, visibility="restricted"), "unauthorized secret"
    )
    llm = FakeLLM(build=lambda markers: (
        f"Authorized{markers[0]}, unavailable{marker_with_index(markers[0], 2)}."
    ))

    result = answer_question(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(),
        vector_search=FakeVectorSearch([candidate(DOC_A), candidate(DOC_B)]),
        llm_provider=llm,
    )

    assert [chunk.text for chunk in llm.calls[0][1]] == ["authorized source"]
    assert result.citations == (Citation(str(DOC_A), 0),)
    assert "unauthorized secret" not in result.answer


def test_literal_citation_like_text_in_document_content_is_preserved_and_not_cited():
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A), "Document says [S1].")
    vector = FakeVectorSearch([candidate(DOC_A)])

    result = answer_question(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(), vector_search=vector,
        llm_provider=MockLLMProvider(),
    )

    assert result == Answer("Document says [S1].", (Citation(str(DOC_A), 0),))


def test_duplicate_source_markers_are_deduplicated():
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A), "authorized source")
    llm = FakeLLM(build=lambda markers: f"Again{markers[0]} and once more{markers[0]}.")

    result = answer_question(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(),
        vector_search=FakeVectorSearch([candidate(DOC_A)]),
        llm_provider=llm,
    )

    assert result == Answer("Again and once more.", (Citation(str(DOC_A), 0),))


@pytest.mark.parametrize(
    "answer",
    ["No source marker.", "[S0] zero.", "[S999] missing.", "[S01] non-canonical."],
)
def test_malformed_or_missing_markers_are_preserved_and_not_cited(answer):
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A), "authorized source")
    llm = FakeLLM(answer=answer)

    result = answer_question(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(),
        vector_search=FakeVectorSearch([candidate(DOC_A)]),
        llm_provider=llm,
    )

    assert result == Answer(answer, ())


def test_extremely_long_numeric_marker_is_preserved_without_error():
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A), "authorized source")
    llm = FakeLLM(build=lambda markers: (
        f"Answer {marker_with_index(markers[0], '9' * 5000)}."
    ))

    result = answer_question(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(),
        vector_search=FakeVectorSearch([candidate(DOC_A)]),
        llm_provider=llm,
    )

    assert result.citations == ()
    assert "9" * 5000 in result.answer


def test_answer_whitespace_is_preserved_when_markers_are_removed():
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A), "authorized source")
    llm = FakeLLM(build=lambda markers: f"Line  1\t{markers[0]}  text .")

    result = answer_question(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(),
        vector_search=FakeVectorSearch([candidate(DOC_A)]),
        llm_provider=llm,
    )

    assert result == Answer("Line  1\t  text .", (Citation(str(DOC_A), 0),))


def test_answer_whitespace_is_preserved_without_markers():
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A), "authorized source")
    answer = "Line  1\ttext ."
    llm = FakeLLM(answer=answer)

    result = answer_question(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(),
        vector_search=FakeVectorSearch([candidate(DOC_A)]),
        llm_provider=llm,
    )

    assert result == Answer(answer, ())


def test_answer_does_not_call_llm_when_no_authorized_chunks_exist():
    repository = FakeRepository()
    repository.add_document(FakeDocument(DOC_A, ORG_A, visibility="restricted"), "secret")
    # Candidate is not visible to this member, so retrieval rejects it.
    vector = FakeVectorSearch([candidate(DOC_A)])
    llm = FakeLLM(answer="unused")

    result = answer_question(
        context(), "question", repository=repository,
        embedding_provider=FakeEmbedding(), vector_search=vector, llm_provider=llm,
    )

    assert result.citations == ()
    assert "could not find" in result.answer
    assert llm.calls == []


def test_answer_skips_llm_when_search_returns_no_chunks():
    llm = FakeLLM(answer="unused")
    result = answer_question(
        context(), "question", repository=FakeRepository(),
        embedding_provider=FakeEmbedding(), vector_search=FakeVectorSearch(),
        llm_provider=llm,
    )
    assert result.citations == ()
    assert llm.calls == []
