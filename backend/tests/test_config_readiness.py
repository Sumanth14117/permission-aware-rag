from pathlib import Path

from app.config import Settings
from app.main import create_app


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_example_environment_uses_application_settings_and_starts_routes():
    settings = Settings(_env_file=REPO_ROOT / ".env.example")

    assert settings.upload_root == Path("data/uploads")
    assert settings.embedding_model_name == "sentence-transformers/all-MiniLM-L6-v2"
    assert settings.qdrant_url == "http://localhost:6333"
    assert settings.qdrant_collection_name == "document_chunks"
    assert settings.qdrant_api_key is None
    assert settings.llm_provider == "mock"

    paths = create_app(settings=settings).openapi()["paths"]
    assert {"/auth/login", "/documents", "/qa", "/health"} <= set(paths)
