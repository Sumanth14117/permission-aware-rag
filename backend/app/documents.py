"""Tenant-scoped document upload, listing, permissions, and deletion."""

from __future__ import annotations

import codecs
import os
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit import (
    EVENT_DOCUMENT_DELETE_DENIED,
    EVENT_DOCUMENT_DELETE_FAILED,
    EVENT_DOCUMENT_DELETE_SUCCEEDED,
    EVENT_DOCUMENT_PERMISSIONS_DENIED,
    EVENT_DOCUMENT_UPLOAD_FAILED,
    EVENT_DOCUMENT_UPLOAD_SUCCEEDED,
    EVENT_MEMBERSHIP_DENIED,
    REASON_INGESTION_UNAVAILABLE,
    REASON_INVALID_UPLOAD,
    REASON_MEMBERSHIP_REQUIRED,
    REASON_NOT_FOUND_OR_FORBIDDEN,
    REASON_OWNER_REQUIRED,
    REASON_STORAGE_UNAVAILABLE,
    REASON_UPLOAD_TOO_LARGE,
    record_audit_event,
)
from app.authentication import AuthenticationServiceError, get_session_user
from app.db.session import get_db
from app.indexing import QdrantVectorIndex, SentenceTransformersEmbeddingProvider
from app.ingestion_service import (
    SQLAlchemyIngestionPersistence,
    ingest_document,
)
from app.models.document import (
    STATUS_FAILED,
    STATUS_PENDING,
    VISIBILITY_ORG,
    STATUS_PENDING,
    VISIBILITY_ORG,
    VISIBILITY_RESTRICTED,
)
from app.models import (
    Document,
    DocumentPermission,
    Membership,
    ROLE_OWNER,
)
from app.models.audit_event import OUTCOME_DENIED, OUTCOME_FAILURE, OUTCOME_SUCCESS

router = APIRouter(prefix="/documents", tags=["documents"])
_ALLOWED_CONTENT_TYPES = {"text/plain": ".txt", "application/pdf": ".pdf"}


@dataclass(frozen=True)
class TenantContext:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str
    db: Session


class PermissionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reader_user_ids: list[uuid.UUID] = Field(max_length=500)


def get_tenant_context(request: Request, db: Session = Depends(get_db)) -> TenantContext:
    """Resolve the user and their single membership from the session cookie."""
    settings = request.app.state.settings
    raw_token = request.cookies.get(settings.session_cookie_name)
    try:
        user = get_session_user(db, raw_token)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        membership = db.execute(
            select(Membership).where(Membership.user_id == user.id)
        ).scalar_one_or_none()
        if membership is None:
            db.rollback()
            record_audit_event(
                db,
                event_type=EVENT_MEMBERSHIP_DENIED,
                outcome=OUTCOME_DENIED,
                actor_user_id=user.id,
                reason=REASON_MEMBERSHIP_REQUIRED,
            )
            raise HTTPException(status_code=403, detail="Organization membership required")
        organization_id, role = membership.organization_id, membership.role
        # End the read transaction so the endpoint service can own its write
        # transaction with Session.begin(). This commits no application writes.
        db.commit()
    except AuthenticationServiceError:
        raise HTTPException(status_code=503, detail="Authentication is temporarily unavailable") from None
    except SQLAlchemyError:
        db.rollback()
        raise HTTPException(status_code=503, detail="Document service is temporarily unavailable") from None
    return TenantContext(user_id=user.id, organization_id=organization_id, role=role, db=db)


def can_read_document(
    context: TenantContext, document: Document, granted_user_ids: set[uuid.UUID]
) -> bool:
    if document.organization_id != context.organization_id:
        return False
    return (
        document.visibility == VISIBILITY_ORG
        or context.role == ROLE_OWNER
        or context.user_id in granted_user_ids
    )


def can_delete_document(context: TenantContext, document: Document) -> bool:
    """Deletion is independent of restricted-document read grants."""
    return document.organization_id == context.organization_id and (
        context.role == ROLE_OWNER or document.uploaded_by_user_id == context.user_id
    )


def validate_upload_metadata(filename: str, content_type: str, max_bytes: int) -> str:
    """Validate display name, declared media type, and filename extension."""
    if (
        not filename
        or len(filename) > 255
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or any(ord(char) < 32 or ord(char) == 127 for char in filename)
    ):
        raise ValueError("filename is invalid")
    media_type = content_type.split(";", 1)[0].strip().lower()
    extension = _ALLOWED_CONTENT_TYPES.get(media_type)
    if extension is None or Path(filename).suffix.lower() != extension:
        raise ValueError("file type is not supported")
    if max_bytes < 1:
        raise ValueError("upload size limit is invalid")
    return media_type


def storage_path(upload_root: str | Path, organization_id: uuid.UUID, document_id: uuid.UUID) -> Path:
    """Derive a storage path only from server-side UUIDs and enforce containment."""
    root = Path(upload_root).resolve()
    organization_directory = root / str(organization_id)
    resolved_organization_directory = organization_directory.resolve()
    try:
        resolved_organization_directory.relative_to(root)
    except ValueError:
        raise ValueError("storage path is invalid") from None
    # Do not let a pre-existing symlink redirect one tenant into another
    # directory, even when its target remains somewhere under the upload root.
    if resolved_organization_directory != organization_directory:
        raise ValueError("storage path is invalid")
    path = resolved_organization_directory / str(document_id)
    try:
        path.relative_to(organization_directory)
    except ValueError:
        raise ValueError("storage path is invalid") from None
    return path


async def _stream_upload(request: Request, path: Path, media_type: str, max_bytes: int) -> int:
    """Stream to an exclusive-created file; never buffer the full upload."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    size = 0
    prefix = bytearray()
    decoder = codecs.getincrementaldecoder("utf-8")("strict") if media_type == "text/plain" else None
    try:
        with os.fdopen(fd, "wb") as output:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise OverflowError("upload exceeds size limit")
                if len(prefix) < 5:
                    prefix.extend(chunk[: 5 - len(prefix)])
                if decoder is not None:
                    if b"\x00" in chunk:
                        raise UnicodeError("text file contains a null byte")
                    decoder.decode(chunk, final=False)
                output.write(chunk)
            if size == 0:
                raise ValueError("file is empty")
            if media_type == "application/pdf" and not prefix.startswith(b"%PDF-"):
                raise ValueError("file content does not match its type")
            if decoder is not None:
                decoder.decode(b"", final=True)
            output.flush()
            os.fsync(output.fileno())
        return size
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _document_response(document: Document) -> dict[str, object]:
    return {
        "id": document.id,
        "original_filename": document.original_filename,
        "content_type": document.content_type,
        "size_bytes": document.size_bytes,
        "visibility": document.visibility,
        "status": document.status,
        "created_at": document.created_at,
    }


def _record_document_event(
    context: TenantContext,
    *,
    event_type: str,
    outcome: str,
    document_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> None:
    """Record a document event with tenant context and no file metadata.

    `document_id` is omitted for failures that occur before the document row is
    persisted (validation, size, stream, and persistence failures); those events
    describe the request, not a stored resource.
    """
    record_audit_event(
        context.db,
        event_type=event_type,
        outcome=outcome,
        actor_user_id=context.user_id,
        organization_id=context.organization_id,
        actor_role=context.role,
        resource_type="document",
        resource_id=document_id,
        reason=reason,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    filename: str = Query(...),
    visibility: Literal["org", "restricted"] = VISIBILITY_ORG,
    context: TenantContext = Depends(get_tenant_context),
) -> dict[str, object]:
    """Upload UTF-8 .txt or PDF (max 10 MiB by default) as a streamed raw body.

    The filename is a display value only. Storage uses a generated document ID
    beneath the authenticated membership's organization directory.
    """
    settings = request.app.state.settings
    document_id = uuid.uuid4()
    try:
        media_type = validate_upload_metadata(filename, request.headers.get("content-type", ""), settings.max_upload_bytes)
    except ValueError:
        _record_document_event(
            context,
            event_type=EVENT_DOCUMENT_UPLOAD_FAILED,
            outcome=OUTCOME_FAILURE,
            reason=REASON_INVALID_UPLOAD,
        )
        raise HTTPException(status_code=415, detail="Unsupported document upload") from None

    content_length = request.headers.get("content-length")
    if content_length is not None:
        if not content_length.isdecimal() or len(content_length) > 20:
            _record_document_event(
                context,
                event_type=EVENT_DOCUMENT_UPLOAD_FAILED,
                outcome=OUTCOME_FAILURE,
                reason=REASON_INVALID_UPLOAD,
            )
            raise HTTPException(status_code=400, detail="Invalid document upload")
        if int(content_length) > settings.max_upload_bytes:
            _record_document_event(
                context,
                event_type=EVENT_DOCUMENT_UPLOAD_FAILED,
                outcome=OUTCOME_FAILURE,
                reason=REASON_UPLOAD_TOO_LARGE,
            )
            raise HTTPException(status_code=413, detail="Document exceeds the upload limit")

    path = storage_path(settings.upload_root, context.organization_id, document_id)
    try:
        size = await _stream_upload(request, path, media_type, settings.max_upload_bytes)
    except OverflowError:
        _record_document_event(
            context,
            event_type=EVENT_DOCUMENT_UPLOAD_FAILED,
            outcome=OUTCOME_FAILURE,
            reason=REASON_UPLOAD_TOO_LARGE,
        )
        raise HTTPException(status_code=413, detail="Document exceeds the upload limit") from None
    except (OSError, UnicodeError, ValueError):
        _record_document_event(
            context,
            event_type=EVENT_DOCUMENT_UPLOAD_FAILED,
            outcome=OUTCOME_FAILURE,
            reason=REASON_INVALID_UPLOAD,
        )
        raise HTTPException(status_code=400, detail="Document upload could not be accepted") from None

    document = Document(
        id=document_id,
        organization_id=context.organization_id,
        uploaded_by_user_id=context.user_id,
        original_filename=filename,
        content_type=media_type,
        size_bytes=size,
        visibility=visibility,
        status=STATUS_PENDING,
    )
    try:
        with context.db.begin():
            context.db.add(document)
            context.db.flush()
    except SQLAlchemyError:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Preserve the safe service error; a leftover file is not
            # discoverable through the document API without committed metadata.
            pass
        _record_document_event(
            context,
            event_type=EVENT_DOCUMENT_UPLOAD_FAILED,
            outcome=OUTCOME_FAILURE,
            reason=REASON_STORAGE_UNAVAILABLE,
        )
        raise HTTPException(status_code=503, detail="Document service is temporarily unavailable") from None

    vector_index = None
    try:
        embedding_provider = SentenceTransformersEmbeddingProvider(
            settings.embedding_model_name,
            batch_size=settings.embedding_batch_size,
        )
        vector_index = QdrantVectorIndex.from_settings(settings)
        ingest_document(
            document.id,
            path.read_bytes(),
            persistence=SQLAlchemyIngestionPersistence(context.db),
            embedding_provider=embedding_provider,
            vector_index=vector_index,
            chunk_size_chars=settings.chunk_size_chars,
            chunk_overlap_chars=settings.chunk_overlap_chars,
        )
    except Exception:
        # Ingestion owns its state transition after claiming the document. If
        # adapter initialization or file reading failed before that claim,
        # persist a non-searchable failure state here.
        try:
            with context.db.begin():
                failed_document = context.db.execute(
                    select(Document)
                    .where(
                        Document.id == document.id,
                        Document.organization_id == context.organization_id,
                    )
                    .with_for_update()
                ).scalar_one_or_none()
                if failed_document is not None and failed_document.status == STATUS_PENDING:
                    failed_document.status = STATUS_FAILED
                    context.db.flush()
        except Exception:
            context.db.rollback()
        _record_document_event(
            context,
            event_type=EVENT_DOCUMENT_UPLOAD_FAILED,
            outcome=OUTCOME_FAILURE,
            document_id=document_id,
            reason=REASON_INGESTION_UNAVAILABLE,
        )
        raise HTTPException(
            status_code=503, detail="Document ingestion is temporarily unavailable"
        ) from None
    finally:
        if vector_index is not None:
            try:
                vector_index.close()
            except Exception:
                # Client cleanup must not turn a committed READY document into
                # an ambiguous failed upload response.
                pass
    _record_document_event(
        context,
        event_type=EVENT_DOCUMENT_UPLOAD_SUCCEEDED,
        outcome=OUTCOME_SUCCESS,
        document_id=document.id,
    )
    return _document_response(document)


@router.get("")
def list_documents(context: TenantContext = Depends(get_tenant_context)) -> list[dict[str, object]]:
    try:
        documents = context.db.execute(
            select(Document).where(Document.organization_id == context.organization_id)
        ).scalars().all()
        if context.role == ROLE_OWNER:
            granted_ids: set[uuid.UUID] = set()
        else:
            granted_ids = set(
                context.db.execute(
                    select(DocumentPermission.document_id).where(
                        DocumentPermission.organization_id == context.organization_id,
                        DocumentPermission.user_id == context.user_id,
                    )
                ).scalars().all()
            )
        return [_document_response(item) for item in documents if can_read_document(context, item, granted_ids)]
    except SQLAlchemyError:
        context.db.rollback()
        raise HTTPException(status_code=503, detail="Document service is temporarily unavailable") from None


@router.put("/{document_id}/permissions", status_code=status.HTTP_204_NO_CONTENT)
def replace_document_readers(
    document_id: uuid.UUID,
    payload: PermissionUpdate,
    context: TenantContext = Depends(get_tenant_context),
) -> Response:
    if context.role != ROLE_OWNER:
        _record_document_event(
            context,
            event_type=EVENT_DOCUMENT_PERMISSIONS_DENIED,
            outcome=OUTCOME_DENIED,
            document_id=document_id,
            reason=REASON_OWNER_REQUIRED,
        )
        raise HTTPException(status_code=403, detail="Document permissions require an organization owner")
    if len(set(payload.reader_user_ids)) != len(payload.reader_user_ids):
        raise HTTPException(status_code=422, detail="Reader list contains duplicates")
    try:
        with context.db.begin():
            document = context.db.execute(
                select(Document).where(
                    Document.id == document_id,
                    Document.organization_id == context.organization_id,
                )
            ).scalar_one_or_none()
            if document is None:
                _record_document_event(
                    context,
                    event_type=EVENT_DOCUMENT_PERMISSIONS_DENIED,
                    outcome=OUTCOME_DENIED,
                    document_id=document_id,
                    reason=REASON_NOT_FOUND_OR_FORBIDDEN,
                )
                raise HTTPException(status_code=404, detail="Document not found")
            if document.visibility != VISIBILITY_RESTRICTED:
                raise HTTPException(status_code=409, detail="Document is not restricted")
            if payload.reader_user_ids:
                member_ids = set(
                    context.db.execute(
                        select(Membership.user_id).where(
                            Membership.organization_id == context.organization_id,
                            Membership.user_id.in_(payload.reader_user_ids),
                        )
                    ).scalars().all()
                )
                if member_ids != set(payload.reader_user_ids):
                    raise HTTPException(status_code=422, detail="Reader must belong to this organization")
            context.db.query(DocumentPermission).filter_by(
                document_id=document_id, organization_id=context.organization_id
            ).delete(synchronize_session=False)
            context.db.add_all(
                DocumentPermission(
                    document_id=document_id,
                    organization_id=context.organization_id,
                    user_id=user_id,
                )
                for user_id in payload.reader_user_ids
            )
            context.db.flush()
    except HTTPException:
        raise
    except SQLAlchemyError:
        context.db.rollback()
        raise HTTPException(status_code=503, detail="Document service is temporarily unavailable") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: uuid.UUID,
    request: Request,
    context: TenantContext = Depends(get_tenant_context),
) -> Response:
    settings = request.app.state.settings
    quarantined: Path | None = None
    original_path: Path | None = None
    try:
        with context.db.begin():
            document = context.db.execute(
                select(Document).where(
                    Document.id == document_id,
                    Document.organization_id == context.organization_id,
                )
            ).scalar_one_or_none()
            if document is None or not can_delete_document(context, document):
                _record_document_event(
                    context,
                    event_type=EVENT_DOCUMENT_DELETE_DENIED,
                    outcome=OUTCOME_DENIED,
                    document_id=document_id,
                    reason=REASON_NOT_FOUND_OR_FORBIDDEN,
                )
                raise HTTPException(status_code=404, detail="Document not found")
            original_path = storage_path(
                settings.upload_root, context.organization_id, document.id
            )
            root = Path(settings.upload_root).resolve()
            try:
                original_path.parent.relative_to(root)
            except ValueError:
                raise HTTPException(status_code=503, detail="Document service is temporarily unavailable") from None
            if original_path.is_symlink() or not original_path.is_file():
                raise HTTPException(status_code=503, detail="Document service is temporarily unavailable")
            quarantined = original_path.with_name(f".{document.id}.{uuid.uuid4().hex}.deleting")
            os.replace(original_path, quarantined)
            context.db.delete(document)
            context.db.flush()
    except HTTPException as exc:
        if quarantined is not None and quarantined.exists() and original_path is not None:
            os.replace(quarantined, original_path)
        # The 404 denial is recorded where it is raised. Only the in-transaction
        # 503 (invalid storage path or missing file) needs a failure event here.
        if exc.status_code != 404:
            _record_document_event(
                context,
                event_type=EVENT_DOCUMENT_DELETE_FAILED,
                outcome=OUTCOME_FAILURE,
                document_id=document_id,
                reason=REASON_STORAGE_UNAVAILABLE,
            )
        raise
    except (OSError, SQLAlchemyError):
        if quarantined is not None and quarantined.exists() and original_path is not None:
            try:
                os.replace(quarantined, original_path)
            except OSError:
                pass
        context.db.rollback()
        _record_document_event(
            context,
            event_type=EVENT_DOCUMENT_DELETE_FAILED,
            outcome=OUTCOME_FAILURE,
            document_id=document_id,
            reason=REASON_STORAGE_UNAVAILABLE,
        )
        raise HTTPException(status_code=503, detail="Document service is temporarily unavailable") from None
    if quarantined is not None:
        try:
            quarantined.unlink()
        except OSError:
            # Metadata is gone and the remaining file is in a non-public
            # quarantine name; report failure rather than claiming success.
            _record_document_event(
                context,
                event_type=EVENT_DOCUMENT_DELETE_FAILED,
                outcome=OUTCOME_FAILURE,
                document_id=document_id,
                reason=REASON_STORAGE_UNAVAILABLE,
            )
            raise HTTPException(status_code=503, detail="Document service is temporarily unavailable") from None
    _record_document_event(
        context,
        event_type=EVENT_DOCUMENT_DELETE_SUCCEEDED,
        outcome=OUTCOME_SUCCESS,
        document_id=document_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
