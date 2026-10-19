"""Best-effort audit-event recording.

Audit writes use a *separate* SQLAlchemy session bound to the request session's
engine. They never commit or roll back the request session, so an audit problem
cannot change the main operation's result.

This is best-effort logging: if the audit database write fails, the failure is
logged as a safe warning and the main HTTP response is unchanged. No event is
guaranteed in that case.

Callers must pass safe, bounded values. Never pass emails, passwords or hashes,
session/CSRF tokens, filenames, document contents, questions, answers, or
retrieved text.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Membership
from app.models.audit_event import OUTCOME_SUCCESS

logger = logging.getLogger(__name__)

# domain.action_outcome
EVENT_LOGIN_SUCCEEDED = "auth.login_succeeded"
EVENT_LOGIN_FAILED = "auth.login_failed"
EVENT_MEMBERSHIP_DENIED = "auth.membership_denied"

EVENT_DOCUMENT_UPLOAD_SUCCEEDED = "document.upload_succeeded"
EVENT_DOCUMENT_UPLOAD_FAILED = "document.upload_failed"
EVENT_DOCUMENT_DELETE_SUCCEEDED = "document.delete_succeeded"
EVENT_DOCUMENT_DELETE_DENIED = "document.delete_denied"
EVENT_DOCUMENT_DELETE_FAILED = "document.delete_failed"
EVENT_DOCUMENT_PERMISSIONS_DENIED = "document.permissions_denied"

EVENT_QA_SUCCEEDED = "qa.query_succeeded"
EVENT_QA_DENIED = "qa.query_denied"
EVENT_QA_FAILED = "qa.query_failed"

REASON_INVALID_CREDENTIALS = "invalid_credentials"
REASON_AUTHENTICATION_UNAVAILABLE = "authentication_unavailable"
REASON_MEMBERSHIP_REQUIRED = "membership_required"
REASON_OWNER_REQUIRED = "owner_required"
REASON_NOT_FOUND_OR_FORBIDDEN = "not_found_or_forbidden"
REASON_INVALID_UPLOAD = "invalid_upload"
REASON_UPLOAD_TOO_LARGE = "upload_too_large"
REASON_STORAGE_UNAVAILABLE = "storage_unavailable"
REASON_INGESTION_UNAVAILABLE = "ingestion_unavailable"
REASON_SERVICE_UNAVAILABLE = "service_unavailable"


def _warn(message: str, *args: object) -> None:
    """Log only safe metadata: no IDs, content, or driver details."""
    logger.warning(message, *args)


def record_audit_event(
    db: Session | None,
    *,
    event_type: str,
    outcome: str,
    actor_user_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    reason: str | None = None,
    question_length: int | None = None,
    citation_count: int | None = None,
) -> bool:
    """Insert one audit row in an independent transaction.

    Returns True when the row was written. Any failure is swallowed after a
    safe warning so the caller's response is unaffected.
    """
    if db is None:
        _warn(
            "audit event was not recorded (event_type=%s, outcome=%s, error=no_session)",
            event_type,
            outcome,
        )
        return False

    try:
        # A fresh session means audit failures cannot poison the request
        # session's transaction state.
        with Session(bind=db.get_bind()) as audit_session:
            with audit_session.begin():
                audit_session.add(
                    AuditEvent(
                        event_type=event_type,
                        outcome=outcome,
                        actor_user_id=actor_user_id,
                        organization_id=organization_id,
                        actor_role=actor_role,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        reason=reason,
                        question_length=question_length,
                        citation_count=citation_count,
                    )
                )
        return True
    except Exception as exc:  # noqa: BLE001 - audit must never break the request
        _warn(
            "audit event was not recorded (event_type=%s, outcome=%s, error=%s)",
            event_type,
            outcome,
            type(exc).__name__,
        )
        return False


def record_login_success(db: Session | None, *, user_id: uuid.UUID) -> bool:
    """Record a successful login with the actor's membership context.

    The membership lookup also runs in the audit session. Login itself has
    already committed, and a missing or unreadable membership must not turn a
    valid login into a failure.
    """
    organization_id: uuid.UUID | None = None
    actor_role: str | None = None
    if db is not None:
        try:
            with Session(bind=db.get_bind()) as audit_session:
                membership = audit_session.execute(
                    select(Membership).where(Membership.user_id == user_id)
                ).scalar_one_or_none()
                if membership is not None:
                    organization_id = membership.organization_id
                    actor_role = membership.role
        except Exception as exc:  # noqa: BLE001 - context is best effort
            _warn(
                "audit login context was unavailable (event_type=%s, error=%s)",
                EVENT_LOGIN_SUCCEEDED,
                type(exc).__name__,
            )

    return record_audit_event(
        db,
        event_type=EVENT_LOGIN_SUCCEEDED,
        outcome=OUTCOME_SUCCESS,
        actor_user_id=user_id,
        organization_id=organization_id,
        actor_role=actor_role,
    )
