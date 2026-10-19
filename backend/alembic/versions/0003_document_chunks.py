"""Persist extracted chunks and their deterministic vector point IDs.

Revision ID: 0003
Revises: 0002
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_chunks",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("vector_point_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "document_id", "organization_id", "chunk_index", name=op.f("pk_document_chunks")
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            name="fk_document_chunks_document_tenant",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "chunk_index >= 0", name=op.f("ck_document_chunks_chunk_index_nonnegative")
        ),
        sa.CheckConstraint("length(text) > 0", name=op.f("ck_document_chunks_text_not_empty")),
        sa.UniqueConstraint(
            "vector_point_id", name=op.f("uq_document_chunks_vector_point_id")
        ),
    )
    op.create_index(
        "ix_document_chunks_organization_document",
        "document_chunks",
        ["organization_id", "document_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_document_chunks_organization_document", table_name="document_chunks"
    )
    op.drop_table("document_chunks")
