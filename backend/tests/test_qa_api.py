from types import SimpleNamespace
from datetime import timedelta
from uuid import UUID

from fastapi.testclient import TestClient

import app.qa as qa_module
from app.answering import ABSTENTION_ANSWER, Answer, Citation
from app.documents import TenantContext, get_tenant_context
from app.csrf import generate_csrf_token
from app.main import create_app


def _settings():
    return SimpleNamespace(
        llm_provider="mock",
        embedding_model_name="unused-for-test",
        embedding_batch_size=2,
        qdrant_url="http://unused",
        qdrant_api_key=None,
        qdrant_collection_name="chunks",
        session_cookie_name="session",
        csrf_cookie_name="csrf",
        csrf_secret=SimpleNamespace(get_secret_value=lambda: "test-csrf-secret"),
        cors_origins="http://localhost:5173",
        cors_origin_list=("http://localhost:5173",),
    )


def _headers():
    token = generate_csrf_token("test-csrf-secret", timedelta(minutes=5))
    return {
        "origin": "http://localhost:5173",
        "x-csrf-token": token,
        "cookie": f"csrf={token}",
    }


def test_qa_endpoint_uses_session_context_and_returns_citations(monkeypatch):
    context = TenantContext(
        user_id=UUID("d25803b6-e0af-4f4c-aee7-a1c35d3feb48"),
        organization_id=UUID("9d71d83f-8887-43cc-a7ca-89443d887e1f"),
        role="member",
        db=None,
    )
    received = {}

    class FakeVectorIndex:
        def close(self):
            pass

    monkeypatch.setattr(qa_module, "SentenceTransformersEmbeddingProvider", lambda *a, **kw: object())
    monkeypatch.setattr(qa_module.QdrantVectorIndex, "from_settings", lambda settings: FakeVectorIndex())

    def fake_answer_question(authenticated_user, question, **kwargs):
        received["user"] = authenticated_user
        received["question"] = question
        return Answer("Answer", (Citation("f1654aad-2084-46dd-a75d-c5d3d106ce0c", 2),))

    monkeypatch.setattr(qa_module, "answer_question", fake_answer_question)
    application = create_app(settings=_settings())
    application.dependency_overrides[get_tenant_context] = lambda: context
    with TestClient(application) as client:
        response = client.post(
            "/qa",
            json={"question": "What is in the document?"},
            headers=_headers(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "answer": "Answer",
        "citations": [{"document_id": "f1654aad-2084-46dd-a75d-c5d3d106ce0c", "chunk_index": 2}],
    }
    assert received == {"user": context, "question": "What is in the document?"}


def test_qa_endpoint_returns_each_new_question_response(monkeypatch):
    context = TenantContext(
        user_id=UUID("d25803b6-e0af-4f4c-aee7-a1c35d3feb48"),
        organization_id=UUID("9d71d83f-8887-43cc-a7ca-89443d887e1f"),
        role="member",
        db=None,
    )

    class FakeVectorIndex:
        def close(self):
            pass

    monkeypatch.setattr(qa_module, "SentenceTransformersEmbeddingProvider", lambda *a, **kw: object())
    monkeypatch.setattr(qa_module.QdrantVectorIndex, "from_settings", lambda settings: FakeVectorIndex())

    received = []

    def fake_answer_question(authenticated_user, question, **kwargs):
        received.append(question)
        if "price" in question:
            return Answer(ABSTENTION_ANSWER, ())
        return Answer(
            "Irrigation schedule answer",
            (Citation("f1654aad-2084-46dd-a75d-c5d3d106ce0c", 0),),
        )

    monkeypatch.setattr(qa_module, "answer_question", fake_answer_question)
    application = create_app(settings=_settings())
    application.dependency_overrides[get_tenant_context] = lambda: context
    with TestClient(application) as client:
        first = client.post(
            "/qa",
            json={"question": "When does irrigation activate?"},
            headers=_headers(),
        )
        second = client.post(
            "/qa",
            json={"question": "What is the exact price in rupees?"},
            headers=_headers(),
        )

    assert received == [
        "When does irrigation activate?",
        "What is the exact price in rupees?",
    ]
    assert first.json() == {
        "answer": "Irrigation schedule answer",
        "citations": [
            {"document_id": "f1654aad-2084-46dd-a75d-c5d3d106ce0c", "chunk_index": 0}
        ],
    }
    assert second.json() == {"answer": ABSTENTION_ANSWER, "citations": []}


def test_qa_request_rejects_client_supplied_authorization_fields():
    application = create_app(settings=_settings())
    application.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id=UUID("d25803b6-e0af-4f4c-aee7-a1c35d3feb48"),
        organization_id=UUID("9d71d83f-8887-43cc-a7ca-89443d887e1f"),
        role="member",
        db=None,
    )
    with TestClient(application) as client:
        response = client.post(
            "/qa",
            json={"question": "question", "user_id": "attacker", "organization_id": "attacker"},
            headers=_headers(),
        )
    assert response.status_code == 422
