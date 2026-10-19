"""Offline evaluation workflow for the permission-aware RAG pipeline (M7).

The harness is deliberately provider-agnostic: callers supply a ``run_case``
callable that executes one golden question through the pipeline and reports the
documents it retrieved, the documents it cited, and the answer text. Scoring is
pure so the same golden set can be reused by tests, manual runs, or CI without
contacting PostgreSQL, Qdrant, or an LLM.

Metrics
-------
``recall_at_k``
    Fraction of the question's expected documents that survived permission-aware
    retrieval. An empty expectation scores ``1.0`` (nothing to recall).
``citation_precision``
    Fraction of returned citations that point at an expected document. A clean
    refusal with no citations scores ``1.0`` only when nothing was expected.
``isolation``
    Boolean: no forbidden document reached retrieval or citations, and no
    forbidden term appears in the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable, Iterable

GOLDEN_SET_PATH = Path(__file__).with_name("golden_set.json")

__all__ = [
    "CaseOutcome",
    "CaseScore",
    "EvaluationCase",
    "EvaluationReport",
    "GOLDEN_SET_PATH",
    "citation_precision",
    "evaluate",
    "isolation_ok",
    "load_cases",
    "recall_at_k",
    "score_case",
]


@dataclass(frozen=True)
class EvaluationCase:
    """One golden question plus the documents it may and may not surface."""

    case_id: str
    question: str
    user_id: str
    organization_id: str
    expected_document_ids: frozenset[str]
    forbidden_document_ids: frozenset[str] = frozenset()
    forbidden_terms: frozenset[str] = frozenset()


@dataclass(frozen=True)
class CaseOutcome:
    """What the pipeline produced for one golden question."""

    retrieved_document_ids: frozenset[str]
    cited_document_ids: frozenset[str]
    answer: str


@dataclass(frozen=True)
class CaseScore:
    """Metric values for one golden question."""

    case_id: str
    recall_at_k: float
    citation_precision: float
    isolation: bool


@dataclass(frozen=True)
class EvaluationReport:
    """Aggregate metrics across the golden set."""

    case_scores: tuple[CaseScore, ...]
    recall_at_k: float
    citation_precision: float
    isolation_score: float

    @property
    def passed(self) -> bool:
        """True only when every case retrieved, cited, and stayed isolated."""
        return all(
            score.recall_at_k == 1.0
            and score.citation_precision == 1.0
            and score.isolation
            for score in self.case_scores
        )


def load_cases(path: Path = GOLDEN_SET_PATH) -> tuple[EvaluationCase, ...]:
    """Load the golden question set from JSON."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("Golden set must be a non-empty JSON list of cases")
    cases: list[EvaluationCase] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each golden case must be a JSON object")
        cases.append(
            EvaluationCase(
                case_id=str(item["id"]),
                question=str(item["question"]),
                user_id=str(item["user_id"]),
                organization_id=str(item["organization_id"]),
                expected_document_ids=frozenset(
                    str(value) for value in item.get("expected_document_ids", ())
                ),
                forbidden_document_ids=frozenset(
                    str(value) for value in item.get("forbidden_document_ids", ())
                ),
                forbidden_terms=frozenset(
                    str(value) for value in item.get("forbidden_terms", ())
                ),
            )
        )
    return tuple(cases)


def recall_at_k(expected: Iterable[str], retrieved: Iterable[str]) -> float:
    """Fraction of expected documents that appear in the retrieved set."""
    expected_set = set(expected)
    if not expected_set:
        return 1.0
    return len(expected_set & set(retrieved)) / len(expected_set)


def citation_precision(expected: Iterable[str], cited: Iterable[str]) -> float:
    """Fraction of citations that point at an expected document."""
    expected_set = set(expected)
    cited_set = set(cited)
    if not cited_set:
        return 1.0 if not expected_set else 0.0
    return len(expected_set & cited_set) / len(cited_set)


def isolation_ok(
    *,
    forbidden_document_ids: Iterable[str],
    retrieved_document_ids: Iterable[str],
    cited_document_ids: Iterable[str],
    forbidden_terms: Iterable[str],
    answer: str,
) -> bool:
    """True when nothing forbidden reached retrieval, citations, or the answer."""
    forbidden = set(forbidden_document_ids)
    if forbidden & set(retrieved_document_ids):
        return False
    if forbidden & set(cited_document_ids):
        return False
    lowered = answer.lower()
    return all(term.lower() not in lowered for term in forbidden_terms)


def score_case(case: EvaluationCase, outcome: CaseOutcome) -> CaseScore:
    """Score one golden question against one pipeline outcome."""
    return CaseScore(
        case_id=case.case_id,
        recall_at_k=recall_at_k(case.expected_document_ids, outcome.retrieved_document_ids),
        citation_precision=citation_precision(
            case.expected_document_ids, outcome.cited_document_ids
        ),
        isolation=isolation_ok(
            forbidden_document_ids=case.forbidden_document_ids,
            retrieved_document_ids=outcome.retrieved_document_ids,
            cited_document_ids=outcome.cited_document_ids,
            forbidden_terms=case.forbidden_terms,
            answer=outcome.answer,
        ),
    )


def evaluate(
    cases: Iterable[EvaluationCase],
    run_case: Callable[[EvaluationCase], CaseOutcome],
) -> EvaluationReport:
    """Run every golden question through ``run_case`` and aggregate its metrics."""
    case_scores = tuple(score_case(case, run_case(case)) for case in cases)
    count = len(case_scores) or 1
    return EvaluationReport(
        case_scores=case_scores,
        recall_at_k=sum(score.recall_at_k for score in case_scores) / count,
        citation_precision=sum(score.citation_precision for score in case_scores) / count,
        isolation_score=sum(1.0 for score in case_scores if score.isolation) / count,
    )
