"""Organization (tenant)."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.membership import Membership

ORGANIZATION_NAME_MAX_LENGTH = 200


class Organization(Base):
    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("btrim(name) <> ''", name="name_not_blank"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    # Not unique: two unrelated organizations may legitimately share a name.
    name: Mapped[str] = mapped_column(String(ORGANIZATION_NAME_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # Ownership is not stored here. It is a membership with role 'owner', which
    # avoids a circular users <-> organizations foreign key and a second place
    # that could disagree with the membership table.
    memberships: Mapped[list["Membership"]] = relationship(
        "Membership", back_populates="organization", passive_deletes="all"
    )

    def __repr__(self) -> str:
        return f"<Organization id={self.id}>"