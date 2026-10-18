"""User account."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:  # only for type checkers; avoids circular imports at runtime
    from app.models.membership import Membership
    from app.models.user_session import UserSession

# RFC 5321 limits an address to 254 characters.
EMAIL_MAX_LENGTH = 254


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Case-insensitive uniqueness comes from storing only normalized emails
        # (the CHECK below) and making the column unique.
        UniqueConstraint("email"),
        # Defence in depth: the application normalizes emails, and the database
        # refuses anything that is not already lowercase and trimmed.
        CheckConstraint("email = lower(btrim(email))", name="email_normalized"),
        CheckConstraint("email <> ''", name="email_not_empty"),
        CheckConstraint("password_hash <> ''", name="password_hash_not_empty"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    email: Mapped[str] = mapped_column(String(EMAIL_MAX_LENGTH))
    # Argon2 encoded hash (about 100 characters). Never a plaintext password.
    password_hash: Mapped[str] = mapped_column(String(255))
    # Python-side default for ORM inserts; server_default covers raw SQL inserts.
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    # One organization per user in the MVP: at most one membership (enforced
    # by UNIQUE(user_id) on memberships; uselist=False mirrors it in Python).
    membership: Mapped[Optional["Membership"]] = relationship(
        "Membership", back_populates="user", uselist=False, passive_deletes="all"
    )
    sessions: Mapped[list["UserSession"]] = relationship(
        "UserSession", back_populates="user", passive_deletes="all"
    )

    def __repr__(self) -> str:
        # Deliberately excludes the email and password hash so logs and
        # tracebacks cannot leak them.
        return f"<User id={self.id}>"