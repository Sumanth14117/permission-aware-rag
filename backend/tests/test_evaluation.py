from __future__ import annotations

from uuid import UUID, uuid5

from app.answering import answer_question
from app.models.membership import ROLE_MEMBER
from app.retrieval import VectorSearchResult
from eval.evaluate import (
    CaseOutcome,
    EvaluationCase,
    citation_precision,
    evaluate,
    isolation_ok,
    load_cases,
    recall_at_k,
    score_case,
)
from tests.test_retrieval import (
    DOC_A,
    DOC_B,
    ORG_A,
    ORG_B,
    USER,
    FakeDocument,
    FakeEmbedding,
    FakeRepository,
    FakeVectorSearch,
    context,
)


AUTHORIZED_TEXT = "Organization A travel policy: book economy and file within 30 days."
FOREIGN_ORGANIZATION_ID = ORG_B


class RecordingLLM:
    """Fake LLM that records the chunks that actually reached generation."""

    def __init__(self):
        self.chunks: tuple = ()
        self.calls = []

    def generate(self, *, question, chunks, source_markers):
        self.calls.append((question, chunks, source_markers))
        assert chunks, "generation must never run without authorized chunks"
        self.chunks = tuple(chunks)
        return f"{source_markers[0]}{chunks[0].text}"


def _candidate(document_id: UUID, organization_id: UUID, *, score: float) -> VectorSearchResult:
    return VectorSearchResult(
        uuid5(document_id, "chunk:0"),
        organization_id,
        document_id,
        0,
        score,
    )


def _build_runner(case: EvaluationCase) -> CaseOutcome:
    """Run one golden question through the real retrieval + answering boundary.

    The fake vector search is deliberately hostile: it returns every expected
    document *and* every forbidden document with a higher score. Only the
    repository re-authorization inside ``answer_question`` may drop the
    forbidden candidates, so a passing outcome proves the isolation contract.
    """
    organization_id = UUID(case.organization_id)
    repository = FakeRepository()
    repository.membership = (organization_id, ROLE_MEMBER)
    candidates: list[VectorSearchResult] = []

    for value in sorted(case.expected_document_ids):
        document_id = UUID(value)
        repository.add_document(
            FakeDocument(document_id, organization_id), AUTHORIZED_TEXT
        )
        candidates.append(_candidate(document_id, organization_id, score=0.90))

    for value in sorted(case.forbidden_document_ids):
        candidates.append(
            _candidate(UUID(value), FOREIGN_ORGANIZATION_ID, score=0.99)
        )

    vector = FakeVectorSearch(candidates)
    llm = RecordingLLM()
    result = answer_question(
        context(),
        case.question,
        repository=repository,
        embedding_provider=FakeEmbedding(),
        vector_search=vector,
        llm_provider=llm,
    )

    return CaseOutcome(
        retrieved_document_ids=frozenset(str(chunk.document_id) for chunk in llm.chunks),
        cited_document_ids=frozenset(citation.document_id for citation in result.citations),
        answer=result.answer,
    )


def test_golden_set_scores_pass_through_permission_aware_pipeline():
    cases = load_cases()
    assert len(cases) >= 2
    assert {case.case_id for case in cases} >= {
        "org-wide-read-recall",
        "empty-read-scope-refusal",
    }
    # The in-memory repository fake recognizes a single fixed user; keep the
    # golden set honest about that rather than silently rewriting case actors.
    assert {case.user_id for case in cases} == {str(USER)}

    report = evaluate(cases, _build_runner)

    assert report.recall_at_k == 1.0
    assert report.citation_precision == 1.0
    assert report.isolation_score == 1.0
    assert report.passed is True
    assert all(score.isolation for score in report.case_scores)


def test_isolation_fails_when_a_forbidden_document_is_retrieved():
    assert (
        isolation_ok(
            forbidden_document_ids={str(DOC_B)},
            retrieved_document_ids={str(DOC_A), str(DOC_B)},
            cited_document_ids={str(DOC_A)},
            forbidden_terms={"Organization B confidential pricing"},
            answer="clean answer",
        )
        is False
    )


def test_isolation_fails_when_a_forbidden_term_leaks_into_the_answer():
    assert (
        isolation_ok(
            forbidden_document_ids={str(DOC_B)},
            retrieved_document_ids={str(DOC_A)},
            cited_document_ids={str(DOC_A)},
            forbidden_terms={"Organization B confidential pricing"},
            answer="Organization B confidential pricing is 42.",
        )
        is False
    )


def test_recall_and_citation_precision_detect_a_missed_answer():
    case = EvaluationCase(
        case_id="unit-miss",
        question="question",
        user_id=str(USER),
        organization_id=str(ORG_A),
        expected_document_ids=frozenset({str(DOC_A)}),
        forbidden_document_ids=frozenset({str(DOC_B)}),
        forbidden_terms=frozenset({"Organization B confidential pricing"}),
    )
    outcome = CaseOutcome(
        retrieved_document_ids=frozenset(),
        cited_document_ids=frozenset(),
        answer="I could not find an answer in the documents you can access.",
    )

    score = score_case(case, outcome)

    assert score.recall_at_k == 0.0
    assert score.citation_precision == 0.0
    assert score.isolation is True
    assert recall_at_k(case.expected_document_ids, outcome.retrieved_document_ids) == 0.0
    assert citation_precision(case.expected_document_ids, outcome.cited_document_ids) == 0.0


def test_clean_refusal_scores_full_precision_when_nothing_was_expected():
    assert citation_precision(expected=(), cited=()) == 1.0
    assert recall_at_k(expected=(), retrieved=()) == 1.0
