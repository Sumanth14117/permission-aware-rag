"""Authentication session (named UserSession to avoid clashing with
sqlalchemy.orm.Session)."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User

# SHA-256 digest as lowercase hex.
TOKEN_HASH_LENGTH = 64


class UserSession(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        # The column can only hold a SHA-256 hex digest: 64 lowercase hex
        # characters. A raw token (e.g. token_urlsafe output) would be rejected
        # because of its different shape, which guards against storing it by mistake.
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="token_hash_sha256_hex"),
    )

    # Internal identifier. It is never sent to clients; the CSRF token for an
    # authenticated session is bound to it.
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Absolute expiry, set by the service (now + SESSION_TTL_HOURS). Indexed so
    # a future cleanup job can find expired rows efficiently.
    expires_at: Mapped[datetime] = mapped_column(index=True)
    # NULL = not revoked. Rows are revoked, not deleted, so there is an audit trail.
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)

    user: Mapped["User"] = relationship("User", back_populates="sessions")

    def __repr__(self) -> str:
        # Never include token_hash.
        return f"<UserSession id={self.id}>"