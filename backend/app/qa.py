"""Authenticated question answering API."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from app.answering import (
    LLMError,
    MockLLMProvider,
    OpenAICompatibleProvider,
    answer_question,
)
from app.audit import (
    EVENT_QA_DENIED,
    EVENT_QA_FAILED,
    EVENT_QA_SUCCEEDED,
    REASON_MEMBERSHIP_REQUIRED,
    REASON_SERVICE_UNAVAILABLE,
    record_audit_event,
)
from app.documents import TenantContext, get_tenant_context
from app.indexing import IndexingError, QdrantVectorIndex, SentenceTransformersEmbeddingProvider
from app.models.audit_event import OUTCOME_DENIED, OUTCOME_FAILURE, OUTCOME_SUCCESS
from app.retrieval import (
    RetrievalAuthorizationError,
    RetrievalUnavailableError,
    SQLAlchemyRetrievalRepository,
)

router = APIRouter(prefix="/qa", tags=["question answering"])


class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=10_000)


def _record_qa_event(
    context: TenantContext,
    *,
    event_type: str,
    outcome: str,
    question_length: int,
    citation_count: int | None = None,
    reason: str | None = None,
) -> None:
    """Record QA shape only: never the question, answer, or retrieved text."""
    record_audit_event(
        context.db,
        event_type=event_type,
        outcome=outcome,
        actor_user_id=context.user_id,
        organization_id=context.organization_id,
        actor_role=context.role,
        reason=reason,
        question_length=question_length,
        citation_count=citation_count,
    )


@router.post("")
def ask_question(
    payload: QuestionRequest,
    request: Request,
    context: TenantContext = Depends(get_tenant_context),
) -> dict[str, object]:
    """Answer from chunks authorized for the session authenticated user."""
    settings = request.app.state.settings
    question_length = len(payload.question)
    vector_search = None
    try:
        if settings.llm_provider == "mock":
            llm_provider = MockLLMProvider()
        elif settings.llm_provider == "openai" and settings.openai_api_key is not None:
            llm_provider = OpenAICompatibleProvider(
                api_key=settings.openai_api_key.get_secret_value(),
                base_url=settings.openai_base_url,
                model=settings.openai_model,
            )
        else:
            _record_qa_event(
                context,
                event_type=EVENT_QA_FAILED,
                outcome=OUTCOME_FAILURE,
                question_length=question_length,
                reason=REASON_SERVICE_UNAVAILABLE,
            )
            raise HTTPException(status_code=503, detail="Answer service is unavailable")

        embedding_provider = SentenceTransformersEmbeddingProvider(
            settings.embedding_model_name, batch_size=settings.embedding_batch_size
        )
        vector_search = QdrantVectorIndex.from_settings(settings)
        result = answer_question(
            context,
            payload.question,
            repository=SQLAlchemyRetrievalRepository(context.db),
            embedding_provider=embedding_provider,
            vector_search=vector_search,
            llm_provider=llm_provider,
        )
    except RetrievalAuthorizationError:
        _record_qa_event(
            context,
            event_type=EVENT_QA_DENIED,
            outcome=OUTCOME_DENIED,
            question_length=question_length,
            reason=REASON_MEMBERSHIP_REQUIRED,
        )
        raise HTTPException(status_code=403, detail="Organization membership required") from None
    except (IndexingError, RetrievalUnavailableError, LLMError):
        _record_qa_event(
            context,
            event_type=EVENT_QA_FAILED,
            outcome=OUTCOME_FAILURE,
            question_length=question_length,
            reason=REASON_SERVICE_UNAVAILABLE,
        )
        raise HTTPException(status_code=503, detail="Answer service is temporarily unavailable") from None
    except SQLAlchemyError:
        context.db.rollback()
        _record_qa_event(
            context,
            event_type=EVENT_QA_FAILED,
            outcome=OUTCOME_FAILURE,
            question_length=question_length,
            reason=REASON_SERVICE_UNAVAILABLE,
        )
        raise HTTPException(status_code=503, detail="Answer service is temporarily unavailable") from None
    finally:
        if vector_search is not None:
            try:
                vector_search.close()
            except Exception:
                pass
    _record_qa_event(
        context,
        event_type=EVENT_QA_SUCCEEDED,
        outcome=OUTCOME_SUCCESS,
        question_length=question_length,
        citation_count=len(result.citations),
    )
    return {
        "answer": result.answer,
        "citations": [
            {"document_id": citation.document_id, "chunk_index": citation.chunk_index}
            for citation in result.citations
        ],
    }
