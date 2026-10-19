"""Add append-only audit events.

Revision ID: 0004
Revises: 0003
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No foreign keys on purpose: audit history must outlive users,
    # organizations, and documents. Application code never updates or deletes
    # these rows.
    op.create_table(
        "audit_events",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("actor_role", sa.String(length=16), nullable=True),
        sa.Column("resource_type", sa.String(length=32), nullable=True),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("question_length", sa.Integer(), nullable=True),
        sa.Column("citation_count", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
        sa.CheckConstraint(
            "btrim(event_type) <> ''",
            name=op.f("ck_audit_events_event_type_not_blank"),
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure', 'denied')",
            name=op.f("ck_audit_events_outcome_valid"),
        ),
        sa.CheckConstraint(
            "actor_role IS NULL OR actor_role IN ('owner', 'member')",
            name=op.f("ck_audit_events_actor_role_valid"),
        ),
        sa.CheckConstraint(
            "reason IS NULL OR btrim(reason) <> ''",
            name=op.f("ck_audit_events_reason_not_blank"),
        ),
        sa.CheckConstraint(
            "question_length IS NULL OR question_length >= 0",
            name=op.f("ck_audit_events_question_length_nonnegative"),
        ),
        sa.CheckConstraint(
            "citation_count IS NULL OR citation_count >= 0",
            name=op.f("ck_audit_events_citation_count_nonnegative"),
        ),
    )
    op.create_index(
        "ix_audit_events_created_at", "audit_events", ["created_at"]
    )
    op.create_index(
        "ix_audit_events_event_type_created_at",
        "audit_events",
        ["event_type", "created_at"],
    )
    op.create_index(
        "ix_audit_events_organization_created_at",
        "audit_events",
        ["organization_id", "created_at"],
    )
    op.create_index(
        "ix_audit_events_actor_created_at",
        "audit_events",
        ["actor_user_id", "created_at"],
    )
    op.create_index(
        "ix_audit_events_resource",
        "audit_events",
        ["resource_type", "resource_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_resource", table_name="audit_events")
    op.drop_index(
        "ix_audit_events_actor_created_at", table_name="audit_events"
    )
    op.drop_index(
        "ix_audit_events_organization_created_at", table_name="audit_events"
    )
    op.drop_index(
        "ix_audit_events_event_type_created_at", table_name="audit_events"
    )
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_table("audit_events")
