"""Handler wiring tests for best-effort audit recording (no database)."""

import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

import app.auth as auth_module
import app.documents as documents_module
import app.qa as qa_module
from app.answering import Answer, Citation, LLMError
from app.authentication import AuthenticationError, AuthenticationServiceError
from app.csrf import generate_csrf_token
from app.db.session import get_db
from app.documents import (
    PermissionUpdate,
    TenantContext,
    delete_document,
    get_tenant_context,
    replace_document_readers,
)
from app.main import create_app
from app.qa import QuestionRequest
from app.retrieval import RetrievalAuthorizationError

USER_ID = uuid.UUID("d25803b6-e0af-4f4c-aee7-a1c35d3feb48")
ORG_ID = uuid.UUID("9d71d83f-8887-43cc-a7ca-89443d887e1f")


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append(kwargs)
        return True


def _settings(tmp_path: Path | None = None):
    return SimpleNamespace(
        session_cookie_name="session",
        session_ttl=timedelta(hours=8),
        cookie_secure=False,
        csrf_cookie_name="csrf",
        csrf_secret=SecretStr("test-csrf-secret-0123456789abcdef"),
        cors_origins="http://localhost:5173",
        cors_origin_list=("http://localhost:5173",),
        llm_provider="mock",
        embedding_model_name="unused-for-test",
        embedding_batch_size=2,
        qdrant_url="http://unused",
        qdrant_api_key=None,
        qdrant_collection_name="chunks",
        upload_root=tmp_path or Path("unused-upload-root"),
        max_upload_bytes=1024,
        chunk_size_chars=100,
        chunk_overlap_chars=10,
    )


def _csrf_headers():
    token = generate_csrf_token(
        "test-csrf-secret-0123456789abcdef", timedelta(minutes=5)
    )
    return {
        "origin": "http://localhost:5173",
        "x-csrf-token": token,
        "cookie": f"csrf={token}",
    }


def _context(*, role="member", db=None):
    return TenantContext(user_id=USER_ID, organization_id=ORG_ID, role=role, db=db)


# --------------------------------------------------------------- login events
def test_failed_login_records_safe_reason(monkeypatch):
    def fail(*args, **kwargs):
        raise AuthenticationError("invalid email or password")

    recorder = _Recorder()
    monkeypatch.setattr(auth_module, "authenticate_and_create_session", fail)
    monkeypatch.setattr(auth_module, "record_audit_event", recorder)
    application = create_app(settings=_settings())
    application.dependency_overrides[get_db] = lambda: object()

    with TestClient(application) as client:
        response = client.post(
            "/auth/login",
            json={"email": "person@example.com", "password": "wrong password"},
            headers=_csrf_headers(),
        )

    assert response.status_code == 401
    (call,) = recorder.calls
    assert call["event_type"] == "auth.login_failed"
    assert call["outcome"] == "failure"
    assert call["reason"] == "invalid_credentials"
    assert "person@example.com" not in repr(call)


def test_login_storage_failure_records_safe_reason(monkeypatch):
    def fail(*args, **kwargs):
        raise AuthenticationServiceError("authentication storage is unavailable")

    recorder = _Recorder()
    monkeypatch.setattr(auth_module, "authenticate_and_create_session", fail)
    monkeypatch.setattr(auth_module, "record_audit_event", recorder)
    application = create_app(settings=_settings())
    application.dependency_overrides[get_db] = lambda: object()

    with TestClient(application) as client:
        response = client.post(
            "/auth/login",
            json={"email": "person@example.com", "password": "password"},
            headers=_csrf_headers(),
        )

    assert response.status_code == 503
    (call,) = recorder.calls
    assert call["event_type"] == "auth.login_failed"
    assert call["reason"] == "authentication_unavailable"


def test_successful_login_records_actor_context(monkeypatch):
    recorded = {}

    def login_service(*args, **kwargs):
        return SimpleNamespace(user=SimpleNamespace(id=USER_ID), raw_token="raw-token")

    def record_success(db, *, user_id):
        recorded["user_id"] = user_id
        return True

    monkeypatch.setattr(auth_module, "authenticate_and_create_session", login_service)
    monkeypatch.setattr(auth_module, "record_login_success", record_success)
    application = create_app(settings=_settings())
    application.dependency_overrides[get_db] = lambda: object()

    with TestClient(application) as client:
        response = client.post(
            "/auth/login",
            json={"email": "person@example.com", "password": "correct password"},
            headers=_csrf_headers(),
        )

    assert response.status_code == 204
    assert recorded == {"user_id": USER_ID}


# ---------------------------------------------------------- membership denial
class _MembershiplessSession:
    def execute(self, statement):
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    def rollback(self):
        pass


def test_missing_membership_is_audited_without_disclosure(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(documents_module, "record_audit_event", recorder)
    monkeypatch.setattr(
        documents_module, "get_session_user", lambda db, token: SimpleNamespace(id=USER_ID)
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(session_cookie_name="session"))),
        cookies={"session": "token"},
    )

    with pytest.raises(HTTPException) as error:
        get_tenant_context(request, _MembershiplessSession())

    assert error.value.status_code == 403
    (call,) = recorder.calls
    assert call["event_type"] == "auth.membership_denied"
    assert call["outcome"] == "denied"
    assert call["actor_user_id"] == USER_ID
    assert call["reason"] == "membership_required"


# ------------------------------------------------------------- permissions
def test_non_owner_permission_change_records_denial(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(documents_module, "record_audit_event", recorder)
    document_id = uuid.uuid4()

    with pytest.raises(HTTPException) as error:
        replace_document_readers(
            document_id, PermissionUpdate(reader_user_ids=[]), _context(role="member")
        )

    assert error.value.status_code == 403
    (call,) = recorder.calls
    assert call["event_type"] == "document.permissions_denied"
    assert call["outcome"] == "denied"
    assert call["resource_id"] == document_id
    assert call["reason"] == "owner_required"


# ------------------------------------------------------------------ deletion
class _Transaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _CrossTenantDeleteSession:
    def begin(self):
        return _Transaction()

    def execute(self, statement):
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    def flush(self):
        pass

    def rollback(self):
        pass


def test_cross_tenant_delete_is_audited_and_returns_non_disclosing_404(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(documents_module, "record_audit_event", recorder)
    document_id = uuid.uuid4()
    context = _context(db=_CrossTenantDeleteSession())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=_settings())))

    with pytest.raises(HTTPException) as error:
        delete_document(document_id, request, context)

    assert error.value.status_code == 404
    assert error.value.detail == "Document not found"
    (call,) = recorder.calls
    assert call["event_type"] == "document.delete_denied"
    assert call["outcome"] == "denied"
    assert call["organization_id"] == ORG_ID
    assert call["resource_id"] == document_id
    assert call["reason"] == "not_found_or_forbidden"


# ----------------------------------------------------------------------- QA
def _qa_request(tmp_path=None):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=_settings(tmp_path))))


class _FakeVectorIndex:
    def close(self):
        pass


def _install_fake_qa_dependencies(monkeypatch, recorder):
    monkeypatch.setattr(qa_module, "record_audit_event", recorder)
    monkeypatch.setattr(qa_module, "SentenceTransformersEmbeddingProvider", lambda *a, **kw: object())
    monkeypatch.setattr(qa_module.QdrantVectorIndex, "from_settings", lambda settings: _FakeVectorIndex())


def test_qa_success_records_shape_only(monkeypatch):
    recorder = _Recorder()
    _install_fake_qa_dependencies(monkeypatch, recorder)
    question = "What is in the document?"
    monkeypatch.setattr(
        qa_module,
        "answer_question",
        lambda *a, **kw: Answer("secret answer", (Citation(uuid.uuid4(), 0),)),
    )

    result = qa_module.ask_question(QuestionRequest(question=question), _qa_request(), _context())

    assert result["answer"] == "secret answer"
    (call,) = recorder.calls
    assert call["event_type"] == "qa.query_succeeded"
    assert call["outcome"] == "success"
    assert call["question_length"] == len(question)
    assert call["citation_count"] == 1
    assert question not in repr(call)
    assert "secret answer" not in repr(call)


def test_qa_denial_records_denied_event(monkeypatch):
    recorder = _Recorder()
    _install_fake_qa_dependencies(monkeypatch, recorder)
    question = "What is in the document?"

    def deny(*args, **kwargs):
        raise RetrievalAuthorizationError("no membership")

    monkeypatch.setattr(qa_module, "answer_question", deny)

    with pytest.raises(HTTPException) as error:
        qa_module.ask_question(QuestionRequest(question=question), _qa_request(), _context())

    assert error.value.status_code == 403
    (call,) = recorder.calls
    assert call["event_type"] == "qa.query_denied"
    assert call["outcome"] == "denied"
    assert call["question_length"] == len(question)
    assert call["reason"] == "membership_required"


def test_qa_failure_records_failed_event(monkeypatch):
    recorder = _Recorder()
    _install_fake_qa_dependencies(monkeypatch, recorder)
    question = "What is in the document?"

    def fail(*args, **kwargs):
        raise LLMError("provider detail")

    monkeypatch.setattr(qa_module, "answer_question", fail)

    with pytest.raises(HTTPException) as error:
        qa_module.ask_question(QuestionRequest(question=question), _qa_request(), _context())

    assert error.value.status_code == 503
    (call,) = recorder.calls
    assert call["event_type"] == "qa.query_failed"
    assert call["outcome"] == "failure"
    assert call["question_length"] == len(question)
    assert call["reason"] == "service_unavailable"


# --------------------------------------------------------------------- upload
class _UploadSession:
    def begin(self):
        return _Transaction()

    def add(self, document):
        self.document = document

    def flush(self):
        pass

    def execute(self, statement):
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    def rollback(self):
        pass


def _install_fake_upload_dependencies(monkeypatch, recorder, written: dict):
    monkeypatch.setattr(documents_module, "record_audit_event", recorder)

    def fake_stream(request, path, media_type, max_bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"hello audit")
        written["path"] = path
        return len(b"hello audit")

    async def _async_stream(request, path, media_type, max_bytes):
        return fake_stream(request, path, media_type, max_bytes)

    monkeypatch.setattr(documents_module, "_stream_upload", _async_stream)
    monkeypatch.setattr(documents_module, "SentenceTransformersEmbeddingProvider", lambda *a, **kw: object())
    monkeypatch.setattr(documents_module.QdrantVectorIndex, "from_settings", lambda settings: _FakeVectorIndex())
    monkeypatch.setattr(documents_module, "ingest_document", lambda *a, **kw: None)


def test_upload_success_records_resource_without_filename(monkeypatch, tmp_path):
    recorder = _Recorder()
    written = {}
    _install_fake_upload_dependencies(monkeypatch, recorder, written)
    application = create_app(settings=_settings(tmp_path))
    application.dependency_overrides[documents_module.get_tenant_context] = lambda: _context(db=_UploadSession())

    with TestClient(application) as client:
        response = client.post(
            "/documents?filename=secret-name.txt",
            content=b"hello audit",
            headers={"content-type": "text/plain", **_csrf_headers()},
        )

    assert response.status_code == 201
    (call,) = recorder.calls
    assert call["event_type"] == "document.upload_succeeded"
    assert call["outcome"] == "success"
    assert call["actor_user_id"] == USER_ID
    assert call["organization_id"] == ORG_ID
    assert call["resource_type"] == "document"
    assert call["resource_id"] == uuid.UUID(response.json()["id"])
    assert "secret-name" not in repr(call)


def test_upload_validation_failure_records_failed_event(monkeypatch, tmp_path):
    recorder = _Recorder()
    written = {}
    _install_fake_upload_dependencies(monkeypatch, recorder, written)
    application = create_app(settings=_settings(tmp_path))
    application.dependency_overrides[documents_module.get_tenant_context] = lambda: _context(db=_UploadSession())

    with TestClient(application) as client:
        response = client.post(
            "/documents?filename=notes.exe",
            content=b"hello audit",
            headers={"content-type": "application/pdf", **_csrf_headers()},
        )

    assert response.status_code == 415
    (call,) = recorder.calls
    assert call["event_type"] == "document.upload_failed"
    assert call["outcome"] == "failure"
    assert call["reason"] == "invalid_upload"
    assert call["resource_id"] is None
    assert "notes.exe" not in repr(call)


def test_upload_post_persistence_failure_keeps_resource_id(monkeypatch, tmp_path):
    recorder = _Recorder()
    written = {}
    session = _UploadSession()
    _install_fake_upload_dependencies(monkeypatch, recorder, written)

    def fail_ingest(*args, **kwargs):
        raise RuntimeError("ingest failed")

    monkeypatch.setattr(documents_module, "ingest_document", fail_ingest)
    application = create_app(settings=_settings(tmp_path))
    application.dependency_overrides[documents_module.get_tenant_context] = lambda: _context(db=session)

    with TestClient(application) as client:
        response = client.post(
            "/documents?filename=notes.txt",
            content=b"hello audit",
            headers={"content-type": "text/plain", **_csrf_headers()},
        )

    assert response.status_code == 503
    assert session.document is not None
    (call,) = recorder.calls
    assert call["event_type"] == "document.upload_failed"
    assert call["outcome"] == "failure"
    assert call["reason"] == "ingestion_unavailable"
    assert call["resource_id"] == session.document.id
