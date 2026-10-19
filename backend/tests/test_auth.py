"""Database-independent tests for login and session lifecycle behavior."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

import app.authentication as authentication
from app.auth import LoginRequest
from app.authentication import (
    AuthenticationError,
    AuthenticationServiceError,
    authenticate_and_create_session,
    get_session_user,
    is_session_valid,
    revoke_session,
)
from app.db.session import get_db
from app.main import create_app
from app.models import User, UserSession
from app.security import hash_password, hash_session_token
from app.security import MAX_PASSWORD_BYTES


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Transaction:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.db.rolled_back = exc_type is not None
        self.db.committed = exc_type is None
        return False


class _Session:
    def __init__(self, *, user=None, persisted_session=None, execute_error=None):
        self.user = user
        self.persisted_session = persisted_session
        self.execute_error = execute_error
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.execute_count = 0

    def begin(self):
        return _Transaction(self)

    def execute(self, statement):
        self.execute_count += 1
        if self.execute_error is not None:
            raise self.execute_error
        entity = statement.column_descriptions[0]["entity"]
        if entity is User:
            return _Result(self.user)
        if entity is UserSession:
            return _Result(self.persisted_session)
        raise AssertionError(f"Unexpected query entity: {entity!r}")

    def add(self, instance):
        self.added.append(instance)

    def flush(self):
        pass


def _settings(*, secure=True):
    return SimpleNamespace(
        session_cookie_name="par_session",
        session_ttl=timedelta(hours=8),
        cookie_secure=secure,
        csrf_cookie_name="par_csrf",
        csrf_secret=SecretStr("unit-test-csrf-secret-0123456789abcdef"),
        csrf_ttl=timedelta(minutes=30),
        cors_origin_list=("http://localhost:5173",),
    )


def _csrf_headers(client):
    origin = "http://localhost:5173"
    response = client.get("/auth/csrf", headers={"Origin": origin})
    assert response.status_code == 200
    return {"Origin": origin, "X-CSRF-Token": response.json()["csrf_token"]}


def _user(*, active=True, password="correct password"):
    return User(
        email="person@example.com",
        password_hash=hash_password(password),
        is_active=active,
    )


def test_login_creates_hashed_persisted_session_with_configured_expiry():
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    user = _user()
    db = _Session(user=user)

    result = authenticate_and_create_session(
        db,
        email=" Person@Example.COM ",
        password="correct password",
        session_ttl=timedelta(hours=8),
        now=now,
    )

    assert result.user is user
    assert result.session in db.added
    assert result.session.user is user
    assert result.session.token_hash == hash_session_token(result.raw_token)
    assert result.raw_token != result.session.token_hash
    assert result.session.expires_at == now + timedelta(hours=8)
    assert result.session.expires_at.tzinfo is timezone.utc
    assert db.committed
    assert not db.rolled_back


def test_login_uses_dummy_argon2_hash_when_account_is_missing(monkeypatch):
    verified_hashes = []

    def verify(password, encoded_hash):
        verified_hashes.append(encoded_hash)
        return False

    monkeypatch.setattr(authentication, "verify_password", verify)
    db = _Session()

    with pytest.raises(AuthenticationError, match="invalid email or password"):
        authenticate_and_create_session(
            db,
            email="missing@example.com",
            password="some password",
            session_ttl=timedelta(hours=1),
        )

    assert len(verified_hashes) == 1
    assert verified_hashes[0].startswith("$argon2id$")
    assert not db.added
    assert db.rolled_back


@pytest.mark.parametrize(
    ("email", "user", "transaction_started"),
    [
        ("not-an-email", None, False),
        ("missing@example.com", None, True),
        ("person@example.com", _user(active=False), True),
        ("person@example.com", _user(password="different password"), True),
    ],
)
def test_invalid_email_missing_inactive_and_wrong_password_share_failure(
    email, user, transaction_started
):
    db = _Session(user=user)

    with pytest.raises(AuthenticationError) as error:
        authenticate_and_create_session(
            db,
            email=email,
            password="correct password",
            session_ttl=timedelta(hours=1),
        )

    assert str(error.value) == "invalid email or password"
    assert not db.added
    assert db.rolled_back is transaction_started


def test_database_errors_are_separate_from_invalid_credentials():
    db = _Session(execute_error=OperationalError("SELECT", {}, Exception("private detail")))

    with pytest.raises(AuthenticationServiceError) as error:
        authenticate_and_create_session(
            db,
            email="person@example.com",
            password="correct password",
            session_ttl=timedelta(hours=1),
        )

    assert str(error.value) == "authentication storage is unavailable"
    assert "private detail" not in str(error.value)
    assert db.rolled_back


def test_oversized_login_password_is_generic_and_skips_argon2_and_database(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("oversized input must be rejected before Argon2 verification")

    monkeypatch.setattr(authentication, "verify_password", unexpected)
    db = _Session(user=_user())

    with pytest.raises(AuthenticationError, match="invalid email or password"):
        authenticate_and_create_session(
            db,
            email="person@example.com",
            password="x" * (MAX_PASSWORD_BYTES + 1),
            session_ttl=timedelta(hours=1),
        )

    assert db.execute_count == 0
    assert not db.added
    assert not db.committed
    assert not db.rolled_back


def test_oversized_password_http_response_matches_invalid_credentials(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("oversized input must be rejected before Argon2 verification")

    monkeypatch.setattr(authentication, "verify_password", unexpected)
    db = _Session(user=_user())
    application = create_app(settings=_settings(secure=False))
    application.dependency_overrides[get_db] = lambda: db

    with TestClient(application) as client:
        csrf_headers = _csrf_headers(client)
        oversized = client.post(
            "/auth/login",
            json={"email": "person@example.com", "password": "x" * (MAX_PASSWORD_BYTES + 1)},
            headers=csrf_headers,
        )
        assert db.execute_count == 0

        monkeypatch.undo()
        invalid = client.post(
            "/auth/login",
            json={"email": "person@example.com", "password": "wrong password"},
            headers=csrf_headers,
        )

    assert oversized.status_code == 401
    assert oversized.json() == {"detail": "Invalid email or password"}
    assert (oversized.status_code, oversized.json()) == (invalid.status_code, invalid.json())
    assert "set-cookie" not in oversized.headers

def test_login_with_unpaired_surrogate_is_generic_and_skips_argon2(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("invalid Unicode must be rejected before Argon2 verification")

    monkeypatch.setattr(authentication, "verify_password", unexpected)
    db = _Session(user=_user())

    with pytest.raises(AuthenticationError, match="invalid email or password"):
        authenticate_and_create_session(
            db,
            email="person@example.com",
            password="bad\ud800password",
            session_ttl=timedelta(hours=1),
        )

    assert db.execute_count == 0


def test_session_validity_requires_unexpired_unrevoked_session_and_active_user():
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    user = _user()
    valid = UserSession(
        user=user,
        token_hash="a" * 64,
        expires_at=now + timedelta(minutes=1),
        revoked_at=None,
    )
    assert is_session_valid(valid, user, now=now)

    valid.expires_at = now
    assert not is_session_valid(valid, user, now=now)
    valid.expires_at = now + timedelta(minutes=1)
    valid.revoked_at = now
    assert not is_session_valid(valid, user, now=now)
    valid.revoked_at = None
    user.is_active = False
    assert not is_session_valid(valid, user, now=now)


def test_get_session_user_checks_token_and_session_validity():
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    user = _user()
    persisted = UserSession(
        user=user,
        token_hash=hash_session_token("raw-session-token"),
        expires_at=now + timedelta(hours=1),
        revoked_at=None,
    )
    db = _Session(persisted_session=persisted)

    assert get_session_user(db, "raw-session-token", now=now) is user
    assert db.committed

    persisted.revoked_at = now
    assert get_session_user(_Session(persisted_session=persisted), "raw-session-token", now=now) is None

    persisted.revoked_at = None
    persisted.expires_at = now
    assert get_session_user(_Session(persisted_session=persisted), "raw-session-token", now=now) is None

    persisted.expires_at = now + timedelta(hours=1)
    user.is_active = False
    assert get_session_user(_Session(persisted_session=persisted), "raw-session-token", now=now) is None


def test_malformed_session_token_fails_closed_without_database_lookup():
    db = _Session()

    assert get_session_user(db, "bad\ud800token") is None
    assert db.execute_count == 0


def test_logout_revokes_session_using_token_hash_only():
    token = "raw-session-token"
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    persisted = UserSession(
        user_id=None,
        token_hash=hash_session_token(token),
        expires_at=now - timedelta(seconds=1),
        revoked_at=None,
    )
    db = _Session(persisted_session=persisted)

    assert revoke_session(db, token, now=now)
    assert persisted.revoked_at == now
    assert db.committed
    assert token not in persisted.token_hash


def test_repeated_revocation_is_idempotent_and_preserves_timestamp():
    token = "raw-session-token"
    first_revoked_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    persisted = UserSession(
        token_hash=hash_session_token(token),
        expires_at=first_revoked_at + timedelta(hours=1),
        revoked_at=first_revoked_at,
    )
    db = _Session(persisted_session=persisted)

    assert not revoke_session(db, token, now=first_revoked_at + timedelta(minutes=5))
    assert persisted.revoked_at == first_revoked_at
    assert db.committed


@pytest.mark.parametrize("operation", ["lookup", "revoke"])
def test_session_storage_failures_are_redacted(operation):
    db = _Session(execute_error=OperationalError("SELECT", {}, Exception("private driver detail")))

    with pytest.raises(AuthenticationServiceError) as error:
        if operation == "lookup":
            get_session_user(db, "raw-session-token")
        else:
            revoke_session(db, "raw-session-token")

    assert str(error.value) == "authentication storage is unavailable"
    assert "private driver detail" not in str(error.value)
    assert db.rolled_back


def test_login_route_issues_only_the_configured_secure_cookie(monkeypatch):
    raw_token = "raw-session-token"

    def login_service(*args, **kwargs):
        return SimpleNamespace(raw_token=raw_token, user=SimpleNamespace(id="user-id"))

    monkeypatch.setattr("app.auth.authenticate_and_create_session", login_service)
    db = _Session()
    application = create_app(settings=_settings(secure=True))
    application.dependency_overrides[get_db] = lambda: db

    with TestClient(application, base_url="https://testserver") as client:
        csrf_headers = _csrf_headers(client)
        response = client.post(
            "/auth/login",
            json={"email": "person@example.com", "password": "correct password"},
            headers=csrf_headers,
        )

    assert response.status_code == 204
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("par_session=")
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie
    assert "max-age=28800" in cookie
    assert raw_token not in response.text


@pytest.mark.parametrize(
    ("service_error", "expected_status", "expected_detail"),
    [
        (AuthenticationError("invalid email or password"), 401, "Invalid email or password"),
        (
            AuthenticationServiceError("authentication storage is unavailable"),
            503,
            "Authentication is temporarily unavailable",
        ),
    ],
)
def test_login_route_separates_generic_auth_failures_from_storage_failures(
    monkeypatch, service_error, expected_status, expected_detail
):
    def fail_login(*args, **kwargs):
        raise service_error

    monkeypatch.setattr("app.auth.authenticate_and_create_session", fail_login)
    application = create_app(settings=_settings(secure=False))
    application.dependency_overrides[get_db] = lambda: _Session()

    with TestClient(application) as client:
        csrf_headers = _csrf_headers(client)
        response = client.post(
            "/auth/login",
            json={"email": "person@example.com", "password": "wrong password"},
            headers=csrf_headers,
        )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
    assert "set-cookie" not in response.headers


def test_logout_route_revokes_cookie_token_and_clears_with_matching_flags(monkeypatch):
    revoked = []

    def revoke_service(db, token):
        revoked.append(token)
        return True

    monkeypatch.setattr("app.auth.revoke_session", revoke_service)
    application = create_app(settings=_settings(secure=True))
    application.dependency_overrides[get_db] = lambda: _Session()

    with TestClient(application, base_url="https://testserver") as client:
        csrf_headers = _csrf_headers(client)
        client.cookies.set("par_session", "raw-session-token")
        response = client.post("/auth/logout", headers=csrf_headers)

    assert response.status_code == 204
    assert revoked == ["raw-session-token"]
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("par_session=")
    assert "httponly" in cookie
    assert "secure" in cookie
    assert "samesite=lax" in cookie
    assert "max-age=0" in cookie


def test_logout_clears_cookie_even_when_database_is_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise AuthenticationServiceError("authentication storage is unavailable")

    monkeypatch.setattr("app.auth.revoke_session", unavailable)
    application = create_app(settings=_settings(secure=False))
    application.dependency_overrides[get_db] = lambda: _Session()

    with TestClient(application) as client:
        csrf_headers = _csrf_headers(client)
        client.cookies.set("par_session", "raw-session-token")
        response = client.post("/auth/logout", headers=csrf_headers)

    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication is temporarily unavailable"}
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "secure" not in cookie
    assert "samesite=lax" in cookie


def test_login_request_rejects_extra_fields():
    with pytest.raises(ValueError):
        LoginRequest(email="person@example.com", password="password", organization_id="client-value")
