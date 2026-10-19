"""Opt-in PostgreSQL integration tests for authentication and session lifecycle.

Every test uses only the existing verified_test_engine fixture. The module
creates uniquely named account data and deliberately does not clean it up.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.authentication import get_session_user
from app.db.session import create_session_factory, get_db
from app.main import create_app
from app.models import UserSession
from app.registration import register_user
from app.security import generate_session_token, hash_session_token

_ALLOWED_ORIGIN = "http://localhost:5173"
_SESSION_COOKIE = "par_session"


@dataclass(frozen=True)
class _RegisteredAccount:
    email: str
    password: str
    user_id: uuid.UUID


@pytest.fixture(scope="module")
def registered_account(verified_test_engine) -> _RegisteredAccount:
    """Create one unique account through the supported registration service.

    The account fixture leaves one user, one organization, and one owner membership.
    The successful-login test leaves its session revoked; the expiry test leaves
    one expired session. No cleanup is performed.
    """
    factory = create_session_factory(verified_test_engine)
    suffix = uuid.uuid4().hex
    email = f"auth-integration-{suffix}@example.com"
    password = f"Integration-only-{uuid.uuid4().hex}-A9!"

    with factory() as session:
        result = register_user(
            session,
            email=email,
            password=password,
            organization_name=f"Auth integration {suffix}",
        )
        user_id = result.user.id

    return _RegisteredAccount(email=email, password=password, user_id=user_id)


def _db_dependency(factory: sessionmaker[Session], calls: list[bool] | None = None):
    """Provide request sessions from the explicitly supplied verified engine."""

    def dependency():
        if calls is not None:
            calls.append(True)
        session = factory()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return dependency


def _client_for(factory: sessionmaker[Session], calls: list[bool] | None = None) -> TestClient:
    application = create_app()
    application.dependency_overrides[get_db] = _db_dependency(factory, calls)
    return TestClient(application)


def _csrf_headers(client: TestClient) -> dict[str, str]:
    response = client.get("/auth/csrf", headers={"Origin": _ALLOWED_ORIGIN})
    assert response.status_code == 200
    token = response.json()["csrf_token"]
    return {"Origin": _ALLOWED_ORIGIN, "X-CSRF-Token": token}


def _session_count(factory: sessionmaker[Session], user_id: uuid.UUID) -> int:
    with factory() as session:
        return session.scalar(
            select(func.count()).select_from(UserSession).where(UserSession.user_id == user_id)
        )


@pytest.mark.db
def test_verified_database_login_persists_only_token_hash_and_logout_revokes(
    verified_test_engine, registered_account: _RegisteredAccount
):
    """Leave the registered account and one revoked session row in the test DB."""
    factory = create_session_factory(verified_test_engine)
    before_request = datetime.now(timezone.utc)
    with _client_for(factory) as client:
        csrf_headers = _csrf_headers(client)
        login_response = client.post(
            "/auth/login",
            json={"email": registered_account.email, "password": registered_account.password},
            headers=csrf_headers,
        )
        assert login_response.status_code == 204

        raw_token = client.cookies.get(_SESSION_COOKIE)
        assert raw_token
        token_hash = hash_session_token(raw_token)
        with factory() as session:
            persisted = session.execute(
                select(UserSession).where(UserSession.token_hash == token_hash)
            ).scalar_one()
            assert persisted.user_id == registered_account.user_id
            assert persisted.token_hash == token_hash
            assert persisted.token_hash != raw_token
            assert raw_token not in persisted.token_hash
            assert persisted.expires_at.tzinfo is not None
            after_request = datetime.now(timezone.utc)
            ttl = timedelta(hours=24)
            assert before_request + ttl <= persisted.expires_at <= after_request + ttl
            assert persisted.revoked_at is None
            persisted_id = persisted.id

        logout_response = client.post("/auth/logout", headers=csrf_headers)
        assert logout_response.status_code == 204
        assert client.cookies.get(_SESSION_COOKIE) is None

    with factory() as session:
        revoked = session.get(UserSession, persisted_id)
        assert revoked is not None
        assert revoked.token_hash == token_hash
        assert revoked.revoked_at is not None


@pytest.mark.db
def test_verified_database_invalid_password_is_generic_and_creates_no_session(
    verified_test_engine, registered_account: _RegisteredAccount
):
    """Invalid credentials add no session row; the account fixture remains."""
    factory = create_session_factory(verified_test_engine)
    before = _session_count(factory, registered_account.user_id)

    with _client_for(factory) as client:
        response = client.post(
            "/auth/login",
            json={"email": registered_account.email, "password": "definitely incorrect"},
            headers=_csrf_headers(client),
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}
    assert _session_count(factory, registered_account.user_id) == before


@pytest.mark.db
def test_invalid_csrf_is_rejected_before_authentication_dependency_runs(
    verified_test_engine, registered_account: _RegisteredAccount
):
    """Rejected CSRF performs no DB dependency work or session write."""
    factory = create_session_factory(verified_test_engine)
    dependency_calls: list[bool] = []
    before = _session_count(factory, registered_account.user_id)

    with _client_for(factory, dependency_calls) as client:
        response = client.post(
            "/auth/login",
            json={"email": registered_account.email, "password": registered_account.password},
            headers={"Origin": _ALLOWED_ORIGIN, "X-CSRF-Token": "invalid-token"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert "set-cookie" not in response.headers
    assert dependency_calls == []
    assert _session_count(factory, registered_account.user_id) == before


@pytest.mark.db
def test_verified_database_session_validation_rejects_expired_session(
    verified_test_engine, registered_account: _RegisteredAccount
):
    """Persist one expired session row and verify service-level rejection."""
    factory = create_session_factory(verified_test_engine)
    now = datetime.now(timezone.utc)
    raw_token = generate_session_token()
    expired = UserSession(
        user_id=registered_account.user_id,
        token_hash=hash_session_token(raw_token),
        expires_at=now - timedelta(seconds=1),
    )
    with factory() as session:
        session.add(expired)
        session.commit()
        expired_id = expired.id

    with factory() as session:
        assert get_session_user(session, raw_token, now=now) is None

    # This deliberately inserted expired row remains in the test database.
    with factory() as session:
        assert session.get(UserSession, expired_id) is not None


@pytest.mark.db
def test_simulated_session_storage_failure_returns_safe_503_and_rolls_back(
    verified_test_engine, registered_account: _RegisteredAccount
):
    """Simulate a post-INSERT SQLAlchemy error; this is not a real DB outage.

    The injected exception occurs after SQL execution but before transaction
    commit. The service must roll back the attempted session row.
    """
    factory = create_session_factory(verified_test_engine)
    before = _session_count(factory, registered_account.user_id)
    injected: list[bool] = []

    def fail_after_session_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO SESSIONS"):
            injected.append(True)
            raise OperationalError(
                "INSERT sessions",
                {},
                RuntimeError("controlled test-only persistence failure"),
            )

    event.listen(verified_test_engine, "after_cursor_execute", fail_after_session_insert)
    try:
        with _client_for(factory) as client:
            response = client.post(
                "/auth/login",
                json={"email": registered_account.email, "password": registered_account.password},
                headers=_csrf_headers(client),
            )
    finally:
        event.remove(verified_test_engine, "after_cursor_execute", fail_after_session_insert)

    assert injected == [True]
    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication is temporarily unavailable"}
    assert "controlled test-only persistence failure" not in response.text
    assert "set-cookie" not in response.headers
    assert _session_count(factory, registered_account.user_id) == before

