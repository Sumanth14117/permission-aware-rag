"""User grants for restricted documents."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKeyConstraint, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentPermission(Base):
    __tablename__ = "document_permissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["memberships.user_id", "memberships.organization_id"],
            ondelete="CASCADE",
        ),
        Index("ix_document_permissions_organization_user", "organization_id", "user_id"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
