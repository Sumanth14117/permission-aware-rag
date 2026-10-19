"""Static checks for the unapplied audit-events migration (0004)."""

import ast
import importlib.util
from pathlib import Path

from app.models import AuditEvent


MIGRATION_FILE = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0004_audit_events.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("audit_migration", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call_names(path: Path, attribute: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == attribute and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant):
                    names.append(first.value)
    return names


def test_audit_migration_follows_document_chunks_revision():
    migration = _load_migration()

    assert migration.revision == "0004"
    assert migration.down_revision == "0003"
    assert migration.branch_labels is None
    assert migration.depends_on is None
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)


def test_audit_migration_creates_and_drops_only_audit_schema():
    assert set(_call_names(MIGRATION_FILE, "create_table")) == {"audit_events"}
    assert set(_call_names(MIGRATION_FILE, "drop_table")) == {"audit_events"}

    created_indexes = set(_call_names(MIGRATION_FILE, "create_index"))
    dropped_indexes = set(_call_names(MIGRATION_FILE, "drop_index"))
    assert created_indexes == {
        "ix_audit_events_created_at",
        "ix_audit_events_event_type_created_at",
        "ix_audit_events_organization_created_at",
        "ix_audit_events_actor_created_at",
        "ix_audit_events_resource",
    }
    assert dropped_indexes == created_indexes


def test_audit_migration_columns_match_the_model():
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    model_columns = set(AuditEvent.__table__.columns.keys())
    for column in model_columns:
        assert f'"{column}"' in source


def test_audit_migration_has_no_foreign_keys_or_secrets():
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    assert "ForeignKeyConstraint" not in source
    assert "ForeignKey(" not in source
    assert "password" not in source.lower()
    assert "postgresql+psycopg://" not in source

    forbidden = {
        "email",
        "original_filename",
        "content",
        "question",
        "answer",
        "text",
        "token_hash",
    }
    assert model_columns_are_disjoint(forbidden)


def model_columns_are_disjoint(forbidden: set[str]) -> bool:
    return set(AuditEvent.__table__.columns.keys()).isdisjoint(forbidden)
