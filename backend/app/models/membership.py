"""Membership: links one user to one organization with a role."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User

ROLE_OWNER = "owner"
ROLE_MEMBER = "member"


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        # MVP rule: a user belongs to exactly one organization. This also
        # creates the index used to find a user's membership.
        UniqueConstraint("user_id"),
        # Supports a composite permission foreign key that proves a reader is
        # a member of the same organization as the document.
        UniqueConstraint(
            "user_id", "organization_id", name="uq_memberships_user_organization"
        ),
        CheckConstraint("role IN ('owner', 'member')", name="role_valid"),
        # At most one owner per organization (PostgreSQL partial unique index).
        # Rows with role = 'member' are not part of this index at all.
        # "Exactly one owner" is a service-layer rule in this version: the
        # registration service creates the organization, user and owner
        # membership in a single transaction.
        Index(
            "uq_memberships_one_owner_per_organization",
            "organization_id",
            unique=True,
            postgresql_where=text("role = 'owner'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    # index=True: "list the members of an organization" is a common query.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    user: Mapped["User"] = relationship("User", back_populates="membership")
    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="memberships"
    )

    def __repr__(self) -> str:
        return f"<Membership id={self.id} role={self.role}>"
