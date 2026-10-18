"""Database-independent contract tests for the core SQLAlchemy mappings."""

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql.sqltypes import Uuid

from app.db.base import Base
from app.models import Membership, Organization, User, UserSession


EXPECTED_TABLES = {"organizations", "users", "memberships", "sessions"}


def _constraints(table, constraint_type):
    return {
        constraint.name: constraint
        for constraint in table.constraints
        if isinstance(constraint, constraint_type)
    }


def _check_expression(constraint):
    return " ".join(str(constraint.sqltext).split())


def _assert_no_defaults(column):
    assert column.default is None
    assert column.server_default is None


def test_all_core_models_register_on_the_shared_base():
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert Organization.metadata is Base.metadata
    assert User.metadata is Base.metadata
    assert Membership.metadata is Base.metadata
    assert UserSession.metadata is Base.metadata


def test_column_names_and_types_match_initial_schema():
    expected_columns = {
        "organizations": {"id", "name", "created_at"},
        "users": {"id", "email", "password_hash", "is_active", "created_at"},
        "memberships": {"id", "user_id", "organization_id", "role", "created_at"},
        "sessions": {
            "id", "user_id", "token_hash", "created_at", "expires_at", "revoked_at"
        },
    }
    for table_name, names in expected_columns.items():
        table = Base.metadata.tables[table_name]
        assert set(table.columns.keys()) == names
        assert isinstance(table.c.id.type, Uuid)
        assert table.c.id.type.as_uuid

    organizations = Base.metadata.tables["organizations"]
    assert isinstance(organizations.c.name.type, String)
    assert organizations.c.name.type.length == 200

    users = Base.metadata.tables["users"]
    assert isinstance(users.c.email.type, String)
    assert users.c.email.type.length == 254
    assert isinstance(users.c.password_hash.type, String)
    assert users.c.password_hash.type.length == 255
    assert isinstance(users.c.is_active.type, Boolean)

    memberships = Base.metadata.tables["memberships"]
    assert isinstance(memberships.c.role.type, String)
    assert memberships.c.role.type.length == 16

    sessions = Base.metadata.tables["sessions"]
    assert isinstance(sessions.c.token_hash.type, String)
    assert sessions.c.token_hash.type.length == 64


def test_primary_keys_and_nullability_match_initial_schema():
    expected_nullable = {
        "organizations": set(),
        "users": set(),
        "memberships": set(),
        "sessions": {"revoked_at"},
    }
    for table_name, table in Base.metadata.tables.items():
        primary_keys = _constraints(table, PrimaryKeyConstraint)
        assert len(primary_keys) == 1
        primary_key = next(iter(primary_keys.values()))
        assert primary_key.name == f"pk_{table.name}"
        assert list(primary_key.columns.keys()) == ["id"]
        assert {
            column.name for column in table.columns if column.nullable
        } == expected_nullable[table_name]


def test_uuid_and_timestamp_defaults_match_initial_schema():
    for table in Base.metadata.tables.values():
        identifier = table.c.id
        assert identifier.default is not None
        assert callable(identifier.default.arg)
        generated_id = identifier.default.arg(None)
        assert isinstance(generated_id, uuid.UUID)
        assert generated_id.int != 0
        assert str(identifier.server_default.arg) == "gen_random_uuid()"

        created_at = table.c.created_at
        assert isinstance(created_at.type, DateTime)
        assert created_at.type.timezone is True
        assert created_at.default is None
        assert str(created_at.server_default.arg) == "now()"

    users = Base.metadata.tables["users"]
    assert users.c.is_active.default.arg is True
    assert str(users.c.is_active.server_default.arg) == "true"

    sessions = Base.metadata.tables["sessions"]
    assert isinstance(sessions.c.expires_at.type, DateTime)
    assert sessions.c.expires_at.type.timezone is True
    assert sessions.c.expires_at.default is None
    assert sessions.c.expires_at.server_default is None

    revoked_at = sessions.c.revoked_at
    assert isinstance(revoked_at.type, DateTime)
    assert revoked_at.type.timezone is True
    _assert_no_defaults(revoked_at)


def test_columns_without_migration_defaults_have_no_python_or_server_defaults():
    users = Base.metadata.tables["users"]
    memberships = Base.metadata.tables["memberships"]
    sessions = Base.metadata.tables["sessions"]

    for column in (
        users.c.email,
        users.c.password_hash,
        memberships.c.user_id,
        memberships.c.organization_id,
        memberships.c.role,
        sessions.c.user_id,
        sessions.c.token_hash,
        sessions.c.expires_at,
    ):
        _assert_no_defaults(column)


def test_unique_and_check_constraints_match_initial_schema():
    users = Base.metadata.tables["users"]
    assert {
        name: list(constraint.columns.keys())
        for name, constraint in _constraints(users, UniqueConstraint).items()
    } == {"uq_users_email": ["email"]}

    memberships = Base.metadata.tables["memberships"]
    assert {
        name: list(constraint.columns.keys())
        for name, constraint in _constraints(memberships, UniqueConstraint).items()
    } == {"uq_memberships_user_id": ["user_id"]}

    sessions = Base.metadata.tables["sessions"]
    assert {
        name: list(constraint.columns.keys())
        for name, constraint in _constraints(sessions, UniqueConstraint).items()
    } == {"uq_sessions_token_hash": ["token_hash"]}

    expected_checks = {
        "organizations": {
            "ck_organizations_name_not_blank": "btrim(name) <> ''",
        },
        "users": {
            "ck_users_email_normalized": "email = lower(btrim(email))",
            "ck_users_email_not_empty": "email <> ''",
            "ck_users_password_hash_not_empty": "password_hash <> ''",
        },
        "memberships": {
            "ck_memberships_role_valid": "role IN ('owner', 'member')",
        },
        "sessions": {
            "ck_sessions_token_hash_sha256_hex": "token_hash ~ '^[0-9a-f]{64}$'",
        },
    }
    for table_name, expected in expected_checks.items():
        checks = _constraints(Base.metadata.tables[table_name], CheckConstraint)
        assert {
            name: _check_expression(constraint) for name, constraint in checks.items()
        } == expected


def test_foreign_keys_match_initial_schema_and_restrict_deletes():
    expected = {
        "memberships": {
            (
                "fk_memberships_user_id_users",
                "user_id",
                "users",
                "id",
                "RESTRICT",
            ),
            (
                "fk_memberships_organization_id_organizations",
                "organization_id",
                "organizations",
                "id",
                "RESTRICT",
            ),
        },
        "sessions": {
            ("fk_sessions_user_id_users", "user_id", "users", "id", "RESTRICT")
        },
        "organizations": set(),
        "users": set(),
    }
    for table_name, expected_fks in expected.items():
        table = Base.metadata.tables[table_name]
        actual = set()
        for constraint in table.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            assert len(constraint.elements) == 1
            element = constraint.elements[0]
            actual.add(
                (
                    constraint.name,
                    element.parent.name,
                    element.column.table.name,
                    element.column.name,
                    element.ondelete,
                )
            )
        assert actual == expected_fks


def test_indexes_match_initial_schema_including_partial_owner_index():
    expected_indexes = {
        "organizations": set(),
        "users": set(),
        "memberships": {
            ("ix_memberships_organization_id", False, ("organization_id",)),
            (
                "uq_memberships_one_owner_per_organization",
                True,
                ("organization_id",),
            ),
        },
        "sessions": {
            ("ix_sessions_expires_at", False, ("expires_at",)),
            ("ix_sessions_user_id", False, ("user_id",)),
        },
    }

    for table_name, expected in expected_indexes.items():
        actual = {
            (
                index.name,
                index.unique,
                tuple(column.name for column in index.columns),
            )
            for index in Base.metadata.tables[table_name].indexes
        }
        assert actual == expected

    owner_index = next(
        index
        for index in Base.metadata.tables["memberships"].indexes
        if index.name == "uq_memberships_one_owner_per_organization"
    )
    assert isinstance(owner_index, Index)
    assert str(owner_index.dialect_options["postgresql"]["where"]) == "role = 'owner'"


def test_relationship_targets_back_populates_and_cardinality():
    relationships = {
        Organization: {
            "memberships": (Membership, "organization", True),
        },
        User: {
            "membership": (Membership, "user", False),
            "sessions": (UserSession, "user", True),
        },
        Membership: {
            "user": (User, "membership", False),
            "organization": (Organization, "memberships", False),
        },
        UserSession: {
            "user": (User, "sessions", False),
        },
    }

    for model, expected_relationships in relationships.items():
        mapper_relationships = model.__mapper__.relationships
        assert set(mapper_relationships.keys()) == set(expected_relationships)
        for name, (target_model, back_populates, uselist) in expected_relationships.items():
            relationship = mapper_relationships[name]
            assert relationship.mapper.class_ is target_model
            assert relationship.back_populates == back_populates
            assert relationship.uselist is uselist
