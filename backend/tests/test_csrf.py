"""Database-independent CSRF token and HTTP protection tests."""

from datetime import timedelta
from types import SimpleNamespace
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.csrf import (
    generate_csrf_token,
    is_allowed_origin,
    validate_csrf_token,
    validate_double_submit,
)
from app.db.session import get_db
from app.main import create_app

_SECRET = "unit-test-csrf-secret-0123456789abcdef"
_ORIGIN = "http://localhost:5173"


def _settings(*, secure=False, origins=None):
    return SimpleNamespace(
        session_cookie_name="par_session",
        session_ttl=timedelta(hours=8),
        cookie_secure=secure,
        csrf_cookie_name="par_csrf",
        csrf_secret=SecretStr(_SECRET),
        csrf_ttl=timedelta(minutes=30),
        cors_origin_list=origins or (_ORIGIN,),
    )


def test_token_generation_and_valid_verification():
    token = generate_csrf_token(_SECRET, timedelta(seconds=10), now=1000)

    assert validate_csrf_token(token, _SECRET, now=1000)
    assert not validate_csrf_token(token, _SECRET + "wrong", now=1000)


@pytest.mark.parametrize(
    "token_mutation",
    [
        lambda parts: [parts[0], parts[1], parts[2], "A" * len(parts[3])],
        lambda parts: [parts[0], parts[1], "A" * len(parts[2]), parts[3]],
        lambda parts: ["v2", parts[1], parts[2], parts[3]],
    ],
)
def test_tampered_or_invalidly_signed_tokens_are_rejected(token_mutation):
    token = generate_csrf_token(_SECRET, timedelta(minutes=1), now=1000)
    changed = ".".join(token_mutation(token.split(".")))

    assert not validate_csrf_token(changed, _SECRET, now=1000)


@pytest.mark.parametrize("token", [None, "", "malformed", "v1.0.bad.bad", "v1.1.\ud800."])
def test_missing_and_malformed_tokens_fail_closed(token):
    assert not validate_csrf_token(token, _SECRET, now=1000)


def test_expired_token_is_rejected():
    token = generate_csrf_token(_SECRET, timedelta(seconds=10), now=1000)

    assert not validate_csrf_token(token, _SECRET, now=1010)


def test_double_submit_requires_matching_cookie_and_header():
    token = generate_csrf_token(_SECRET, timedelta(minutes=1), now=1000)

    assert validate_double_submit(token, token, _SECRET, now=1000)
    assert not validate_double_submit(token, token + "x", _SECRET, now=1000)
    assert not validate_double_submit(token, None, _SECRET, now=1000)


def test_origin_validation_requires_exact_allowlist_membership():
    allowed = (_ORIGIN, "https://example.test")

    assert is_allowed_origin(_ORIGIN, allowed)
    assert not is_allowed_origin(None, allowed)
    assert not is_allowed_origin("http://localhost:51730", allowed)
    assert not is_allowed_origin("http://localhost:5173.attacker.test", allowed)
    assert not is_allowed_origin(_ORIGIN + "/", allowed)


def test_csrf_lifetime_setting_has_default_and_bounds():
    common = {
        "database_url": "postgresql+psycopg://user:pass@localhost/db",
        "cors_origins": _ORIGIN,
        "csrf_secret": _SECRET,
        "cookie_secure": False,
        "_env_file": None,
    }

    assert Settings(**common).csrf_ttl_minutes == 30
    assert Settings(**common, csrf_ttl_minutes=1).csrf_ttl == timedelta(minutes=1)
    assert Settings(**common, csrf_ttl_minutes=1440).csrf_ttl == timedelta(days=1)
    with pytest.raises(ValidationError):
        Settings(**common, csrf_ttl_minutes=0)
    with pytest.raises(ValidationError):
        Settings(**common, csrf_ttl_minutes=1441)


def test_bootstrap_sets_readable_cookie_and_returns_only_csrf_token():
    application = create_app(settings=_settings(secure=True))
    with TestClient(application) as client:
        response = client.get("/auth/csrf", headers={"Origin": _ORIGIN})

    assert response.status_code == 200
    token = response.json()["csrf_token"]
    assert validate_csrf_token(token, _SECRET)
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("par_csrf=")
    assert "path=/" in cookie
    assert "secure" in cookie
    assert "httponly" not in cookie
    assert "samesite=lax" in cookie
    assert "max-age=1800" in cookie
    assert response.headers["cache-control"] == "no-store"
    assert "par_session" not in response.headers["set-cookie"]
    assert _SECRET not in response.text


def _assert_rejected_bootstrap(headers):
    application = create_app(settings=_settings())
    with TestClient(application) as client:
        response = client.get("/auth/csrf", headers=headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert "csrf_token" not in response.json()
    assert "set-cookie" not in response.headers
    assert _SECRET not in response.text


def test_bootstrap_without_origin_accepts_same_origin_fetch_metadata_and_host():
    application = create_app(settings=_settings())
    with TestClient(application) as client:
        response = client.get(
            "/auth/csrf",
            headers={"Sec-Fetch-Site": "same-origin", "Host": "LOCALHOST:5173"},
        )

    assert response.status_code == 200
    token = response.json()["csrf_token"]
    assert validate_csrf_token(token, _SECRET)
    assert response.headers["set-cookie"].lower().startswith("par_csrf=")


@pytest.mark.parametrize(
    ("scheme", "configured_origin", "host"),
    [
        ("http", "http://localhost", "localhost"),
        ("https", "https://localhost", "localhost"),
        ("http", "http://localhost:8080", "localhost:8080"),
        ("https", "https://localhost:8443", "localhost:8443"),
        ("http", "http://[::1]", "[::1]"),
    ],
)
def test_origin_absent_fallback_accepts_matching_scheme_and_authority(
    scheme, configured_origin, host
):
    application = create_app(settings=_settings(origins=(configured_origin,)))
    with TestClient(application, base_url=f"{scheme}://{host}") as client:
        response = client.get(
            "/auth/csrf",
            headers={"Sec-Fetch-Site": "same-origin", "Host": host},
        )

    assert response.status_code == 200
    assert validate_csrf_token(response.json()["csrf_token"], _SECRET)
    assert response.headers["set-cookie"].lower().startswith("par_csrf=")


@pytest.mark.parametrize(
    ("request_scheme", "configured_origin", "host"),
    [
        ("http", "https://localhost", "localhost"),
        ("https", "http://localhost", "localhost"),
        ("http", "http://localhost:8080", "localhost:8081"),
    ],
)
def test_origin_absent_fallback_rejects_scheme_or_port_mismatch(
    request_scheme, configured_origin, host
):
    application = create_app(settings=_settings(origins=(configured_origin,)))
    with TestClient(application, base_url=f"{request_scheme}://localhost") as client:
        response = client.get(
            "/auth/csrf",
            headers={"Sec-Fetch-Site": "same-origin", "Host": host},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert "csrf_token" not in response.json()
    assert "set-cookie" not in response.headers


def test_configured_port_zero_does_not_match_default_port_host():
    application = create_app(settings=_settings(origins=("http://localhost:0",)))
    with TestClient(application, base_url="http://localhost") as client:
        response = client.get(
            "/auth/csrf",
            headers={"Sec-Fetch-Site": "same-origin", "Host": "localhost"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert "csrf_token" not in response.json()
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("scheme", [None, "", "ftp", "HTTPS"])
def test_origin_absent_fallback_rejects_missing_or_unsupported_asgi_scheme(scheme):
    application = create_app(settings=_settings(origins=("https://localhost",)))

    async def with_scheme_override(scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/auth/csrf":
            scope = dict(scope)
            scope["scheme"] = scheme
        await application(scope, receive, send)

    with TestClient(with_scheme_override, base_url="https://localhost") as client:
        response = client.get(
            "/auth/csrf",
            headers={"Sec-Fetch-Site": "same-origin", "Host": "localhost"},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert "csrf_token" not in response.json()
    assert "set-cookie" not in response.headers


def test_origin_present_allowlist_path_does_not_depend_on_asgi_scheme():
    application = create_app(settings=_settings(origins=("https://allowed.example",)))
    with TestClient(application, base_url="http://localhost") as client:
        response = client.get(
            "/auth/csrf", headers={"Origin": "https://allowed.example"}
        )

    assert response.status_code == 200
    assert validate_csrf_token(response.json()["csrf_token"], _SECRET)
    assert response.headers["set-cookie"].lower().startswith("par_csrf=")


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Sec-Fetch-Site": "same-site", "Host": "localhost:5173"},
        {"Sec-Fetch-Site": "cross-site", "Host": "localhost:5173"},
        {"Sec-Fetch-Site": "none", "Host": "localhost:5173"},
        {"Sec-Fetch-Site": "same-origin", "Host": "attacker.test"},
        {"Sec-Fetch-Site": "same-origin", "Host": "localhost:5174"},
        {"Sec-Fetch-Site": "same-origin", "Host": ""},
        {"Sec-Fetch-Site": "same-origin", "Host": "localhost:5173/path"},
        {"Sec-Fetch-Site": "same-origin", "Host": "localhost:"},
        {"Sec-Fetch-Site": "", "Host": "localhost:5173"},
    ],
)
def test_bootstrap_without_origin_rejects_invalid_fetch_metadata_or_host(headers):
    _assert_rejected_bootstrap(headers)


def test_bootstrap_without_origin_rejects_duplicate_host():
    _assert_rejected_bootstrap(
        [
            ("Sec-Fetch-Site", "same-origin"),
            ("Host", "localhost:5173"),
            ("host", "attacker.test"),
        ]
    )


def test_bootstrap_without_origin_rejects_missing_host_at_asgi_boundary():
    application = create_app(settings=_settings())

    async def without_host(scope, receive, send):
        if scope["type"] == "http" and scope.get("path") == "/auth/csrf":
            scope = dict(scope)
            scope["headers"] = [
                (name, value) for name, value in scope["headers"] if name.lower() != b"host"
            ]
        await application(scope, receive, send)

    with TestClient(without_host) as client:
        response = client.get(
            "/auth/csrf", headers={"Sec-Fetch-Site": "same-origin"}
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert "csrf_token" not in response.json()
    assert "set-cookie" not in response.headers


def test_bootstrap_without_origin_rejects_missing_or_non_same_origin_fetch_site():
    _assert_rejected_bootstrap({"Host": "localhost:5173"})
    _assert_rejected_bootstrap(
        [("Sec-Fetch-Site", "cross-site"), ("Host", "localhost:5173")]
    )


def test_bootstrap_without_origin_rejects_duplicate_or_malformed_fetch_site():
    _assert_rejected_bootstrap(
        [
            ("Sec-Fetch-Site", "same-origin"),
            ("sec-fetch-site", "same-site"),
            ("Host", "localhost:5173"),
        ]
    )
    _assert_rejected_bootstrap(
        [("Sec-Fetch-Site", "same-origin, same-site"), ("Host", "localhost:5173")]
    )


@pytest.mark.parametrize(
    "origin",
    ["https://unlisted.example", "http://localhost:5173.attacker.test", "not an origin"],
)
def test_bootstrap_rejects_disallowed_origin_without_fetch_metadata_fallback(origin):
    _assert_rejected_bootstrap(
        [
            ("Origin", origin),
            ("Sec-Fetch-Site", "same-origin"),
            ("Host", "localhost:5173"),
        ]
    )


def test_bootstrap_rejects_duplicate_origin_headers_without_issuing_token_or_cookie():
    application = create_app(settings=_settings())

    with TestClient(application) as client:
        response = client.get(
            "/auth/csrf",
            headers=[
                ("Origin", _ORIGIN),
                ("origin", "https://unlisted.example"),
            ],
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert "csrf_token" not in response.json()
    assert "set-cookie" not in response.headers
    assert _SECRET not in response.text


@pytest.mark.parametrize("path", ["/auth/login", "/auth/logout"])
def test_auth_routes_reject_missing_csrf_before_route_logic(path, monkeypatch):
    calls = []

    def unexpected(*args, **kwargs):
        calls.append(True)
        raise AssertionError("protected route logic must not execute")

    monkeypatch.setattr("app.auth.authenticate_and_create_session", unexpected)
    monkeypatch.setattr("app.auth.revoke_session", unexpected)
    application = create_app(settings=_settings())
    application.dependency_overrides[get_db] = unexpected

    with TestClient(application) as client:
        response = client.post(
            path,
            headers={"Origin": _ORIGIN},
            json={"email": "person@example.com", "password": "password"}
            if path.endswith("login")
            else None,
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert calls == []
    assert _SECRET not in response.text


@pytest.mark.parametrize("origin", [None, "https://unlisted.example"])
def test_unsafe_request_rejects_missing_or_unlisted_origin_before_route(origin):
    calls = []
    application = create_app(settings=_settings())

    def probe():
        calls.append(True)
        return {"ok": True}

    application.add_api_route("/probe", probe, methods=["POST"])
    headers = {} if origin is None else {"Origin": origin}
    with TestClient(application) as client:
        response = client.post("/probe", headers=headers)

    assert response.status_code == 403
    assert calls == []


def test_unsafe_request_rejects_cookie_header_mismatch_before_route():
    calls = []
    application = create_app(settings=_settings())

    def probe():
        calls.append(True)
        return {"ok": True}

    application.add_api_route("/probe", probe, methods=["POST"])
    with TestClient(application) as client:
        bootstrap = client.get("/auth/csrf", headers={"Origin": _ORIGIN})
        valid_token = bootstrap.json()["csrf_token"]
        client.cookies.set("par_csrf", valid_token + "tampered")
        response = client.post(
            "/probe",
            headers={"Origin": _ORIGIN, "X-CSRF-Token": valid_token},
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert calls == []


def test_duplicate_csrf_header_fields_are_rejected_before_route():
    calls = []
    application = create_app(settings=_settings())
    application.add_api_route(
        "/probe", lambda: calls.append(True) or {"ok": True}, methods=["POST"]
    )

    with TestClient(application) as client:
        bootstrap = client.get("/auth/csrf", headers={"Origin": _ORIGIN})
        token = bootstrap.json()["csrf_token"]
        response = client.post(
            "/probe",
            headers=[
                ("Origin", _ORIGIN),
                ("X-CSRF-Token", token),
                ("x-csrf-token", token),
            ],
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert calls == []


@pytest.mark.parametrize(
    "cookie_headers",
    [
        lambda token: [("Cookie", f"par_csrf={token}; par_csrf={token}")],
        lambda token: [("Cookie", f"par_csrf={token}"), ("cookie", f"par_csrf={token}")],
    ],
)
def test_duplicate_csrf_cookie_instances_are_rejected_before_route(cookie_headers):
    calls = []
    application = create_app(settings=_settings())
    application.add_api_route(
        "/probe", lambda: calls.append(True) or {"ok": True}, methods=["POST"]
    )

    with TestClient(application) as client:
        bootstrap = client.get("/auth/csrf", headers={"Origin": _ORIGIN})
        token = bootstrap.json()["csrf_token"]
        client.cookies.clear()
        headers = [("Origin", _ORIGIN), ("X-CSRF-Token", token), *cookie_headers(token)]
        response = client.post("/probe", headers=headers)

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert calls == []


def test_duplicate_unrelated_cookies_are_allowed():
    calls = []
    application = create_app(settings=_settings())
    application.add_api_route(
        "/probe", lambda: calls.append(True) or {"ok": True}, methods=["POST"]
    )

    with TestClient(application) as client:
        bootstrap = client.get("/auth/csrf", headers={"Origin": _ORIGIN})
        token = bootstrap.json()["csrf_token"]
        client.cookies.clear()
        response = client.post(
            "/probe",
            headers=[
                ("Origin", _ORIGIN),
                ("X-CSRF-Token", token),
                ("Cookie", "theme=light; theme=dark"),
                ("cookie", f"par_csrf={token}"),
            ],
        )

    assert response.status_code == 200
    assert calls == [True]


def test_expired_matching_cookie_and_header_are_rejected_by_http_middleware():
    calls = []
    application = create_app(settings=_settings())
    application.add_api_route(
        "/probe", lambda: calls.append(True) or {"ok": True}, methods=["POST"]
    )
    expired = generate_csrf_token(
        _SECRET, timedelta(seconds=30), now=time.time() - 60
    )

    with TestClient(application) as client:
        client.cookies.clear()
        response = client.post(
            "/probe",
            headers=[
                ("Origin", _ORIGIN),
                ("X-CSRF-Token", expired),
                ("Cookie", f"par_csrf={expired}"),
            ],
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert expired not in response.text
    assert calls == []


@pytest.mark.parametrize("path", ["/auth/login", "/auth/logout"])
def test_auth_routes_reject_invalid_csrf_before_route_logic(path, monkeypatch):
    calls = []

    def unexpected(*args, **kwargs):
        calls.append(True)
        raise AssertionError("protected route logic must not execute")

    monkeypatch.setattr("app.auth.authenticate_and_create_session", unexpected)
    monkeypatch.setattr("app.auth.revoke_session", unexpected)
    application = create_app(settings=_settings())
    application.dependency_overrides[get_db] = unexpected

    with TestClient(application) as client:
        response = client.post(
            path,
            headers={"Origin": _ORIGIN, "Cookie": "par_csrf=malformed", "X-CSRF-Token": "malformed"},
            json={"email": "person@example.com", "password": "password"}
            if path.endswith("login")
            else None,
        )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
    assert calls == []


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_all_unsafe_methods_are_protected_by_default(method):
    calls = []
    application = create_app(settings=_settings())

    def probe():
        calls.append(True)
        return {"ok": True}

    application.add_api_route(
        "/probe", probe, methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
    )

    with TestClient(application) as client:
        rejected = client.request(method, "/probe", headers={"Origin": _ORIGIN})
        assert rejected.status_code == 403
        assert calls == []

        bootstrap = client.get("/auth/csrf", headers={"Origin": _ORIGIN})
        token = bootstrap.json()["csrf_token"]
        accepted = client.request(
            method,
            "/probe",
            headers={"Origin": _ORIGIN, "X-CSRF-Token": token},
        )

    assert accepted.status_code == 200
    assert calls == [True]


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_do_not_require_csrf(method):
    calls = []
    application = create_app(settings=_settings())

    def probe():
        calls.append(True)
        return {"ok": True}

    application.add_api_route(
        "/probe", probe, methods=["GET", "HEAD", "OPTIONS", "POST"]
    )

    with TestClient(application) as client:
        response = client.request(method, "/probe")

    assert response.status_code == 200
    assert calls == [True]
