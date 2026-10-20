"""Database-independent tests for the signup HTTP route."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

import app.auth as auth_module
from app.auth import RegisterRequest
from app.authentication import AuthenticationServiceError
from app.db.session import get_db
from app.main import create_app
from app.registration import DuplicateEmailError, RegistrationError

USER_ID = "user-id"


def _settings(*, secure=False):
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


def _client(monkeypatch, *, register=None, login=None, recorded=None):
    if register is not None:
        monkeypatch.setattr(auth_module, "register_user", register)
    if login is not None:
        monkeypatch.setattr(auth_module, "authenticate_and_create_session", login)
    monkeypatch.setattr(
        auth_module,
        "record_login_success",
        lambda db, *, user_id: (recorded.append(user_id) if recorded is not None else True),
    )
    application = create_app(settings=_settings())
    application.dependency_overrides[get_db] = lambda: object()
    return TestClient(application)


def _valid_payload(**overrides):
    payload = {
        "email": " Owner@Example.com ",
        "organization_name": " Example Org ",
        "password": "correct horse battery",
    }
    payload.update(overrides)
    return payload


def _unexpected(*args, **kwargs):
    raise AssertionError("registration must not run for invalid input")


def test_signup_creates_account_and_signs_in(monkeypatch):
    calls = {}
    recorded = []

    def fake_register(db, *, email, password, organization_name):
        calls["register"] = {
            "email": email,
            "password": password,
            "organization_name": organization_name,
        }
        return SimpleNamespace(user=SimpleNamespace(id=USER_ID))

    def fake_login(db, *, email, password, session_ttl):
        calls["login"] = {"email": email, "password": password, "session_ttl": session_ttl}
        return SimpleNamespace(user=SimpleNamespace(id=USER_ID), raw_token="raw-session-token")

    with _client(
        monkeypatch, register=fake_register, login=fake_login, recorded=recorded
    ) as client:
        headers = _csrf_headers(client)
        response = client.post("/auth/register", json=_valid_payload(), headers=headers)

    assert response.status_code == 201
    assert response.json() == {"signed_in": True}
    assert calls["register"] == {
        "email": "owner@example.com",
        "password": "correct horse battery",
        "organization_name": "Example Org",
    }
    assert calls["login"]["email"] == "owner@example.com"
    assert calls["login"]["email"] == calls["register"]["email"]
    assert calls["login"]["session_ttl"] == timedelta(hours=8)
    assert recorded == [USER_ID]

    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("par_session=")
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "raw-session-token" not in response.text
    assert "correct horse battery" not in response.text


def test_signup_rejects_short_password_before_registration(monkeypatch):
    with _client(monkeypatch, register=_unexpected) as client:
        headers = _csrf_headers(client)
        response = client.post(
            "/auth/register", json=_valid_payload(password="short"), headers=headers
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Password must be at least 8 characters"}
    assert "short" not in response.text


def test_signup_rejects_blank_password_before_registration(monkeypatch):
    with _client(monkeypatch, register=_unexpected) as client:
        headers = _csrf_headers(client)
        response = client.post(
            "/auth/register", json=_valid_payload(password="        "), headers=headers
        )

    assert response.status_code == 400


def test_signup_rejects_invalid_email_before_registration(monkeypatch):
    with _client(monkeypatch, register=_unexpected) as client:
        headers = _csrf_headers(client)
        response = client.post(
            "/auth/register", json=_valid_payload(email="not-an-email"), headers=headers
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Enter a valid email address"}
    assert "not-an-email" not in response.text


def test_signup_rejects_blank_organization_before_registration(monkeypatch):
    with _client(monkeypatch, register=_unexpected) as client:
        headers = _csrf_headers(client)
        response = client.post(
            "/auth/register", json=_valid_payload(organization_name="   "), headers=headers
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "Enter a valid organization name"}


def test_signup_duplicate_email_is_a_safe_conflict(monkeypatch):
    def duplicate(*args, **kwargs):
        raise DuplicateEmailError("email address is already registered")

    with _client(monkeypatch, register=duplicate) as client:
        headers = _csrf_headers(client)
        response = client.post("/auth/register", json=_valid_payload(), headers=headers)

    assert response.status_code == 409
    assert response.json() == {"detail": "An account with this email already exists"}
    assert "owner@example.com" not in response.text
    assert "correct horse battery" not in response.text
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "failure",
    [
        RegistrationError("registration could not be completed"),
        OperationalError("INSERT", {}, Exception("private driver detail")),
    ],
)
def test_signup_storage_failures_are_redacted(monkeypatch, failure):
    def fail(*args, **kwargs):
        raise failure

    with _client(monkeypatch, register=fail) as client:
        headers = _csrf_headers(client)
        response = client.post("/auth/register", json=_valid_payload(), headers=headers)

    assert response.status_code == 503
    assert response.json() == {"detail": "Registration is temporarily unavailable"}
    assert "private driver detail" not in response.text
    assert "correct horse battery" not in response.text
    assert "set-cookie" not in response.headers


def test_signup_account_survives_session_creation_failure(monkeypatch):
    def fake_register(*args, **kwargs):
        return SimpleNamespace(user=SimpleNamespace(id=USER_ID))

    def unavailable(*args, **kwargs):
        raise AuthenticationServiceError("authentication storage is unavailable")

    with _client(monkeypatch, register=fake_register, login=unavailable) as client:
        headers = _csrf_headers(client)
        response = client.post("/auth/register", json=_valid_payload(), headers=headers)

    assert response.status_code == 201
    assert response.json() == {"signed_in": False}
    assert "set-cookie" not in response.headers
    assert "authentication storage" not in response.text


def test_signup_requires_csrf(monkeypatch):
    with _client(monkeypatch, register=_unexpected) as client:
        response = client.post("/auth/register", json=_valid_payload())

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}


def test_signup_request_rejects_extra_authorization_fields():
    with pytest.raises(ValueError):
        RegisterRequest(
            email="owner@example.com",
            organization_name="Example",
            password="correct horse battery",
            organization_id="client-supplied",
            role="owner",
        )


def test_signup_validation_errors_do_not_echo_the_password(monkeypatch):
    with _client(monkeypatch, register=_unexpected) as client:
        headers = _csrf_headers(client)
        response = client.post(
            "/auth/register",
            json={"password": "super-secret-password"},
            headers=headers,
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert "super-secret-password" not in response.text


def test_login_validation_errors_do_not_echo_the_password(monkeypatch):
    with _client(monkeypatch) as client:
        headers = _csrf_headers(client)
        response = client.post(
            "/auth/login",
            json={"password": "super-secret-password"},
            headers=headers,
        )

    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}
    assert "super-secret-password" not in response.text
