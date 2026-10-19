"""Append-only security and data-access audit events.

The table deliberately has no foreign keys. Audit rows must survive deletion of
the organization, user, or document they describe, and a failed audit insert
must never block or roll back the operation being audited.

Application code only inserts rows. There is no update/delete API, and callers
must pass safe identifiers and reason codes rather than free-form user input.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

OUTCOME_SUCCESS = "success"
OUTCOME_FAILURE = "failure"
OUTCOME_DENIED = "denied"

OUTCOMES = (OUTCOME_SUCCESS, OUTCOME_FAILURE, OUTCOME_DENIED)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("btrim(event_type) <> ''", name="event_type_not_blank"),
        CheckConstraint(
            "outcome IN ('success', 'failure', 'denied')", name="outcome_valid"
        ),
        CheckConstraint(
            "actor_role IS NULL OR actor_role IN ('owner', 'member')",
            name="actor_role_valid",
        ),
        CheckConstraint(
            "reason IS NULL OR btrim(reason) <> ''", name="reason_not_blank"
        ),
        CheckConstraint(
            "question_length IS NULL OR question_length >= 0",
            name="question_length_nonnegative",
        ),
        CheckConstraint(
            "citation_count IS NULL OR citation_count >= 0",
            name="citation_count_nonnegative",
        ),
        Index("ix_audit_events_created_at", "created_at"),
        Index(
            "ix_audit_events_event_type_created_at", "event_type", "created_at"
        ),
        Index(
            "ix_audit_events_organization_created_at",
            "organization_id",
            "created_at",
        ),
        Index(
            "ix_audit_events_actor_created_at", "actor_user_id", "created_at"
        ),
        Index(
            "ix_audit_events_resource", "resource_type", "resource_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    # No foreign keys by design: this is historical evidence, not a live
    # reference to mutable rows.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resource_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # A bounded, application-defined code (for example "not_found_or_forbidden"),
    # never a free-form message that could contain user input.
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # QA events store shape only: never the question, answer, or retrieved text.
    question_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    citation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AuditEvent id={self.id} event_type={self.event_type} "
            f"outcome={self.outcome}>"
        )
