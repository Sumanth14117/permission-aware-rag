"""Unit tests for the initial baseline schema migration revision (0001).

Zero database connections and zero network calls: verifies migration structure,
dependency ordering, constraint definitions, reversibility, and lack of secrets
purely through in-memory inspection.
"""

import ast
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import Table

from app.db.base import Base
import app.models  # noqa: F401  — register models on Base.metadata

VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_FILE = VERSIONS_DIR / "0001_initial_core_schema.py"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("initial_migration", MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_file_exists():
    assert MIGRATION_FILE.is_file(), f"Expected migration file at {MIGRATION_FILE}"


def test_migration_revision_identifiers():
    module = _load_migration_module()
    assert getattr(module, "revision", None) == "0001"
    assert getattr(module, "down_revision", "not_none") is None
    assert getattr(module, "branch_labels", "not_none") is None
    assert getattr(module, "depends_on", "not_none") is None


def test_migration_has_upgrade_and_downgrade_callables():
    module = _load_migration_module()
    assert hasattr(module, "upgrade") and callable(module.upgrade)
    assert hasattr(module, "downgrade") and callable(module.downgrade)


def test_no_credentials_or_urls_in_migration_code():
    content = MIGRATION_FILE.read_text(encoding="utf-8")
    assert "password=" not in content.lower()
    assert "postgresql://" not in content
    assert "postgresql+psycopg://" not in content
    assert "secret" not in content.lower().replace("password_hash", "")


def test_tables_match_sqlalchemy_metadata():
    tree = ast.parse(MIGRATION_FILE.read_text(encoding="utf-8"))
    created_tables: list[str] = []
    dropped_tables: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "create_table" and node.args:
                first_arg = node.args[0]
                if isinstance(first_arg, ast.Constant):
                    created_tables.append(first_arg.value)
            elif node.func.attr == "drop_table" and node.args:
                first_arg = node.args[0]
                if isinstance(first_arg, ast.Constant):
                    dropped_tables.append(first_arg.value)

    expected_tables = {"organizations", "users", "memberships", "sessions"}
    assert set(created_tables) == expected_tables
    assert set(dropped_tables) == expected_tables
    assert set(Base.metadata.tables.keys()) == expected_tables


def test_dependency_ordering_in_upgrade_and_downgrade():
    tree = ast.parse(MIGRATION_FILE.read_text(encoding="utf-8"))
    created_tables: list[str] = []
    dropped_tables: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            if node.name == "upgrade":
                for child in ast.walk(node):
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                        if child.func.attr == "create_table" and child.args:
                            if isinstance(child.args[0], ast.Constant):
                                created_tables.append(child.args[0].value)
            elif node.name == "downgrade":
                for child in ast.walk(node):
                    if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                        if child.func.attr == "drop_table" and child.args:
                            if isinstance(child.args[0], ast.Constant):
                                dropped_tables.append(child.args[0].value)

    # In upgrade: referenced tables must be created before referencing tables
    assert created_tables.index("organizations") < created_tables.index("memberships")
    assert created_tables.index("users") < created_tables.index("memberships")
    assert created_tables.index("users") < created_tables.index("sessions")

    # In downgrade: referencing tables must be dropped before referenced tables
    assert dropped_tables.index("sessions") < dropped_tables.index("users")
    assert dropped_tables.index("memberships") < dropped_tables.index("users")
    assert dropped_tables.index("memberships") < dropped_tables.index("organizations")


def test_critical_constraints_present_in_migration_source():
    content = MIGRATION_FILE.read_text(encoding="utf-8")

    # Check constraints
    assert "email = lower(btrim(email))" in content
    assert "token_hash ~ '^[0-9a-f]{64}$'" in content
    assert "btrim(name) <> ''" in content
    assert "role IN ('owner', 'member')" in content

    # Partial unique index for single owner per org
    assert "uq_memberships_one_owner_per_organization" in content
    assert "role = 'owner'" in content

    # RESTRICT foreign key deletion rules
    assert 'ondelete="RESTRICT"' in content
