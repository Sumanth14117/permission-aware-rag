"""Database-independent document policy, metadata, and routing tests."""

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.sql.sqltypes import BigInteger, DateTime, String, Uuid
from types import SimpleNamespace
from fastapi import HTTPException
import app.documents as documents_module

from app.db.base import Base
from app.documents import (
    TenantContext,
    can_delete_document,
    can_read_document,
    delete_document,
    get_tenant_context,
    replace_document_readers,
    storage_path,
    validate_upload_metadata,
    PermissionUpdate,
)
from app.models import (
    Document,
    DocumentPermission,
    ROLE_MEMBER,
    ROLE_OWNER,
)
from app.models.document import VISIBILITY_ORG, VISIBILITY_RESTRICTED


def _context(*, user_id=None, organization_id=None, role=ROLE_MEMBER):
    return TenantContext(
        user_id=user_id or uuid.uuid4(),
        organization_id=organization_id or uuid.uuid4(),
        role=role,
        db=None,
    )


def _document(*, organization_id, uploader_id, visibility=VISIBILITY_ORG):
    return SimpleNamespace(
        organization_id=organization_id,
        uploaded_by_user_id=uploader_id,
        visibility=visibility,
    )


def test_document_uploader_can_delete_their_own_document():
    context = _context()
    document = _document(
        organization_id=context.organization_id,
        uploader_id=context.user_id,
        visibility=VISIBILITY_RESTRICTED,
    )

    assert can_delete_document(context, document)


def test_organization_owner_can_delete_another_members_document():
    context = _context(role=ROLE_OWNER)
    document = _document(
        organization_id=context.organization_id,
        uploader_id=uuid.uuid4(),
    )

    assert can_delete_document(context, document)


def test_read_grant_does_not_allow_member_to_delete_another_upload():
    context = _context()
    uploader_id = uuid.uuid4()
    document = _document(
        organization_id=context.organization_id,
        uploader_id=uploader_id,
        visibility=VISIBILITY_RESTRICTED,
    )

    assert can_read_document(context, document, {context.user_id})
    assert not can_delete_document(context, document)


def test_no_role_can_delete_a_document_from_another_organization():
    for role in (ROLE_MEMBER, ROLE_OWNER):
        context = _context(role=role)
        document = _document(
            organization_id=uuid.uuid4(),
            uploader_id=context.user_id,
        )

        assert not can_delete_document(context, document)


def test_document_read_policy_distinguishes_org_restricted_and_cross_tenant():
    context = _context()
    other_user = uuid.uuid4()
    organization_document = _document(
        organization_id=context.organization_id,
        uploader_id=other_user,
    )
    restricted_document = _document(
        organization_id=context.organization_id,
        uploader_id=other_user,
        visibility=VISIBILITY_RESTRICTED,
    )
    other_tenant_document = _document(
        organization_id=uuid.uuid4(),
        uploader_id=other_user,
    )

    assert can_read_document(context, organization_document, set())
    assert can_read_document(context, restricted_document, {context.user_id})
    assert not can_read_document(context, restricted_document, set())
    assert not can_read_document(context, other_tenant_document, {context.user_id})
    assert can_read_document(
        _context(organization_id=context.organization_id, role=ROLE_OWNER),
        restricted_document,
        set(),
    )


@pytest.mark.parametrize(
    ("filename", "content_type", "expected"),
    [
        ("notes.txt", "text/plain; charset=utf-8", "text/plain"),
        ("report.PDF", "application/pdf", "application/pdf"),
    ],
)
def test_upload_metadata_accepts_supported_types(filename, content_type, expected):
    assert validate_upload_metadata(filename, content_type, 1024) == expected


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("../secret.txt", "text/plain"),
        ("folder\\secret.txt", "text/plain"),
        ("bad\nname.txt", "text/plain"),
        ("notes.pdf", "text/plain"),
        ("notes.txt", "application/octet-stream"),
        ("", "text/plain"),
    ],
)
def test_upload_metadata_rejects_unsafe_names_and_unsupported_types(filename, content_type):
    with pytest.raises(ValueError):
        validate_upload_metadata(filename, content_type, 1024)


def test_upload_metadata_rejects_invalid_size_policy():
    with pytest.raises(ValueError):
        validate_upload_metadata("notes.txt", "text/plain", 0)


def test_storage_path_uses_only_server_uuid_components_and_stays_under_root():
    organization_id, document_id = uuid.uuid4(), uuid.uuid4()
    root = Path("isolated-upload-root").resolve()
    result = storage_path(root, organization_id, document_id)

    assert result == root / str(organization_id) / str(document_id)
    assert result.parent.parent == root


def test_document_tables_are_registered_on_shared_metadata():
    assert {"documents", "document_permissions"} <= set(Base.metadata.tables)
    assert Document.metadata is Base.metadata
    assert DocumentPermission.metadata is Base.metadata


def test_document_columns_constraints_foreign_keys_and_indexes():
    documents = Base.metadata.tables["documents"]
    assert isinstance(documents.c.id.type, Uuid)
    assert documents.c.id.type.as_uuid
    assert isinstance(documents.c.original_filename.type, String)
    assert documents.c.original_filename.type.length == 255
    assert isinstance(documents.c.content_type.type, String)
    assert documents.c.content_type.type.length == 127
    assert isinstance(documents.c.size_bytes.type, BigInteger)
    assert isinstance(documents.c.visibility.type, String)
    assert isinstance(documents.c.status.type, String)
    assert isinstance(documents.c.created_at.type, DateTime)
    assert documents.c.created_at.type.timezone
    assert documents.c.id.server_default is not None
    assert str(documents.c.visibility.server_default.arg) == "org"
    assert str(documents.c.status.server_default.arg) == "pending"

    unique = {
        (constraint.name, tuple(constraint.columns.keys()))
        for constraint in documents.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert unique == {("uq_documents_id", ("id", "organization_id"))}
    checks = {
        constraint.name
        for constraint in documents.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert checks == {
        "ck_documents_visibility_valid",
        "ck_documents_status_valid",
        "ck_documents_size_positive",
    }
    foreign_keys = {
        (
            constraint.name,
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in documents.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert foreign_keys == {
        (
            "fk_documents_organization_id_organizations",
            ("organization_id",),
            ("organizations.id",),
            "RESTRICT",
        ),
        (
            "fk_documents_uploaded_by_user_id_users",
            ("uploaded_by_user_id",),
            ("users.id",),
            "RESTRICT",
        ),
    }
    assert {
        (index.name, tuple(column.name for column in index.columns))
        for index in documents.indexes
    } == {
        ("ix_documents_organization_id", ("organization_id",)),
        ("ix_documents_uploaded_by_user_id", ("uploaded_by_user_id",)),
        ("ix_documents_organization_visibility", ("organization_id", "visibility")),
    }


def test_permission_table_enforces_same_organization_membership():
    permissions = Base.metadata.tables["document_permissions"]
    assert [column.name for column in permissions.primary_key.columns] == [
        "document_id",
        "user_id",
    ]
    foreign_keys = {
        (
            constraint.name,
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in permissions.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert foreign_keys == {
        (
            "fk_document_permissions_document_id_documents",
            ("document_id", "organization_id"),
            ("documents.id", "documents.organization_id"),
            "CASCADE",
        ),
        (
            "fk_document_permissions_user_id_memberships",
            ("user_id", "organization_id"),
            ("memberships.user_id", "memberships.organization_id"),
            "CASCADE",
        ),
    }
    membership_constraints = Base.metadata.tables["memberships"].constraints
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_memberships_user_organization"
        and tuple(constraint.columns.keys()) == ("user_id", "organization_id")
        for constraint in membership_constraints
    )


def test_only_organization_owner_can_manage_restricted_readers():
    context = _context(role=ROLE_MEMBER)

    with pytest.raises(HTTPException) as error:
        replace_document_readers(uuid.uuid4(), PermissionUpdate(reader_user_ids=[]), context)

    assert error.value.status_code == 403
    assert error.value.detail == "Document permissions require an organization owner"


def test_tenant_context_uses_authenticated_session_and_database_membership(monkeypatch):
    user_id, organization_id = uuid.uuid4(), uuid.uuid4()
    user = SimpleNamespace(id=user_id)
    membership = SimpleNamespace(organization_id=organization_id, role=ROLE_OWNER)
    requested_token = "session-cookie-value"

    class MembershipResult:
        def scalar_one_or_none(self):
            return membership

    class SessionDB:
        committed = False

        def execute(self, statement):
            compiled = statement.compile()
            assert user_id in compiled.params.values()
            return MembershipResult()

        def commit(self):
            self.committed = True

    db = SessionDB()
    monkeypatch.setattr(
        documents_module,
        "get_session_user",
        lambda session_db, token: user if session_db is db and token == requested_token else None,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(settings=SimpleNamespace(session_cookie_name="session"))
        ),
        cookies={"session": requested_token},
    )

    context = get_tenant_context(request, db)

    assert context.user_id == user_id
    assert context.organization_id == organization_id
    assert context.role == ROLE_OWNER
    assert context.db is db
    assert db.committed


class _EmptyResult:
    def scalar_one_or_none(self):
        return None


class _ReadTransaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _TenantScopedLookup:
    def __init__(self, expected_document_id, expected_organization_id):
        self.expected_document_id = expected_document_id
        self.expected_organization_id = expected_organization_id
        self.statement = None

    def begin(self):
        return _ReadTransaction()

    def execute(self, statement):
        self.statement = statement
        compiled = statement.compile()
        values = set(compiled.params.values())
        assert self.expected_document_id in values
        assert self.expected_organization_id in values
        sql = str(compiled)
        assert "documents.organization_id" in sql
        return _EmptyResult()


def test_cross_tenant_delete_returns_non_disclosing_404_without_file_access():
    settings = SimpleNamespace(upload_root=Path("unused-upload-root"))
    organization_id, document_id = uuid.uuid4(), uuid.uuid4()
    db = _TenantScopedLookup(document_id, organization_id)
    context = _context(organization_id=organization_id)
    context = TenantContext(context.user_id, organization_id, ROLE_MEMBER, db)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=settings))
    )
    with pytest.raises(HTTPException) as error:
        delete_document(document_id, request, context)

    assert error.value.status_code == 404
    assert error.value.detail == "Document not found"
    assert db.statement is not None
