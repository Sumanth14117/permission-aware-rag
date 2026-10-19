"""Database-independent tests for the minimal FastAPI application."""

import app.main as main_module
from fastapi.testclient import TestClient


def test_create_app_uses_existing_settings(monkeypatch):
    settings = object()
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)

    application = main_module.create_app()

    assert application.title == main_module.APP_TITLE
    assert application.state.settings is settings


def test_create_app_accepts_explicit_settings_without_loading_configuration(monkeypatch):
    def unexpected_settings_lookup():
        raise AssertionError("configuration lookup was not expected")

    monkeypatch.setattr(main_module, "get_settings", unexpected_settings_lookup)
    settings = object()

    application = main_module.create_app(settings=settings)

    assert application.state.settings is settings


def test_health_endpoint_does_not_create_or_use_database_engine(monkeypatch):
    def unexpected_database_access(*args, **kwargs):
        raise AssertionError("database access was not expected")

    monkeypatch.setattr("app.db.session.create_db_engine", unexpected_database_access)
    monkeypatch.setattr("app.db.session.get_engine", unexpected_database_access)
    monkeypatch.setattr("app.db.session.get_session_factory", unexpected_database_access)

    with TestClient(main_module.create_app(settings=object())) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
