"""Tenant-scoped uploaded document metadata."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.document_chunk import StoredDocumentChunk

VISIBILITY_ORG = "org"
VISIBILITY_RESTRICTED = "restricted"
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_READY = "ready"
STATUS_FAILED = "failed"


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        UniqueConstraint("id", "organization_id"),
        CheckConstraint("visibility IN ('org', 'restricted')", name="visibility_valid"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')", name="status_valid"
        ),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        Index("ix_documents_organization_id", "organization_id"),
        Index("ix_documents_uploaded_by_user_id", "uploaded_by_user_id"),
        Index("ix_documents_organization_visibility", "organization_id", "visibility"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    uploaded_by_user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(127), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False, server_default="org")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    chunks: Mapped[list["StoredDocumentChunk"]] = relationship(
        "StoredDocumentChunk", back_populates="document", passive_deletes=True
    )
