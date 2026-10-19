"""Add tenant-scoped documents and restricted-reader grants.

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_memberships_user_organization",
        "memberships",
        ["user_id", "organization_id"],
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=127), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("visibility", sa.String(length=16), server_default=sa.text("'org'"), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("id", "organization_id", name=op.f("uq_documents_id")),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name=op.f("fk_documents_organization_id_organizations"), ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"], ["users.id"],
            name=op.f("fk_documents_uploaded_by_user_id_users"), ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "visibility IN ('org', 'restricted')", name=op.f("ck_documents_visibility_valid")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name=op.f("ck_documents_status_valid"),
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_documents_size_positive")),
    )
    op.create_index("ix_documents_organization_id", "documents", ["organization_id"])
    op.create_index("ix_documents_uploaded_by_user_id", "documents", ["uploaded_by_user_id"])
    op.create_index(
        "ix_documents_organization_visibility", "documents", ["organization_id", "visibility"]
    )

    op.create_table(
        "document_permissions",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("document_id", "user_id", name=op.f("pk_document_permissions")),
        sa.ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            name=op.f("fk_document_permissions_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            name=op.f("fk_document_permissions_user_id_memberships"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_document_permissions_organization_user",
        "document_permissions",
        ["organization_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_permissions_organization_user", table_name="document_permissions")
    op.drop_table("document_permissions")
    op.drop_index("ix_documents_organization_visibility", table_name="documents")
    op.drop_index("ix_documents_uploaded_by_user_id", table_name="documents")
    op.drop_index("ix_documents_organization_id", table_name="documents")
    op.drop_table("documents")
    op.drop_constraint(
        "uq_memberships_user_organization", "memberships", type_="unique"
    )
