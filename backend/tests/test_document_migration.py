"""Static checks for the unapplied document-management migration."""

import ast
import importlib.util
from pathlib import Path


MIGRATION_FILE = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "0002_document_management.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("document_migration", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_document_migration_follows_initial_schema():
    migration = _load_migration()

    assert migration.revision == "0002"
    assert migration.down_revision == "0001"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)


def test_document_migration_adds_and_removes_only_document_schema():
    tree = ast.parse(MIGRATION_FILE.read_text(encoding="utf-8"))
    created_tables = []
    dropped_tables = []
    created_indexes = []
    dropped_indexes = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr in {"create_table", "drop_table", "create_index", "drop_index"}:
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant):
                target = {
                    "create_table": created_tables,
                    "drop_table": dropped_tables,
                    "create_index": created_indexes,
                    "drop_index": dropped_indexes,
                }[node.func.attr]
                target.append(first.value)

    assert set(created_tables) == {"documents", "document_permissions"}
    assert set(dropped_tables) == {"documents", "document_permissions"}
    assert set(created_indexes) == {
        "ix_documents_organization_id",
        "ix_documents_uploaded_by_user_id",
        "ix_documents_organization_visibility",
        "ix_document_permissions_organization_user",
    }
    assert set(dropped_indexes) == set(created_indexes)
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    assert '"uq_memberships_user_organization"' in source
    assert 'ondelete="RESTRICT"' in source
    assert 'ondelete="CASCADE"' in source
    assert "password=" not in source.lower()
    assert "postgresql+psycopg://" not in source
