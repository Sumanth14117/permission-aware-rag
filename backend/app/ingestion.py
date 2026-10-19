"""Document text extraction and deterministic, tenant-aware chunking."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import uuid

from app.models import Document

MAX_EXTRACTED_CHARACTERS = 5_000_000


class DocumentExtractionError(ValueError):
    """Base error with safe, content-free messages for extraction failures."""


class EmptyDocumentError(DocumentExtractionError):
    """Raised when a valid input contains no extractable text."""


class UnsupportedDocumentError(DocumentExtractionError):
    """Raised for a media type without an extraction implementation."""


class MalformedDocumentError(DocumentExtractionError):
    """Raised when document bytes cannot be decoded or parsed safely."""


class ExtractionLimitError(DocumentExtractionError):
    """Raised when extracted text exceeds the configured safety bound."""


@dataclass(frozen=True)
class DocumentChunk:
    """A deterministic text slice carrying only server-derived tenant IDs."""

    document_id: uuid.UUID
    organization_id: uuid.UUID
    chunk_index: int
    text: str


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _validate_extracted_text(text: str, max_characters: int) -> str:
    if max_characters < 1:
        raise ValueError("maximum extracted text length must be positive")
    normalized = _normalize_newlines(text)
    try:
        normalized.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise MalformedDocumentError("Document text is malformed") from None
    if len(normalized) > max_characters:
        raise ExtractionLimitError("Extracted document exceeds the text limit")
    if not normalized.strip():
        raise EmptyDocumentError("Document contains no extractable text")
    return normalized


def extract_document_text(
    content: bytes,
    content_type: str,
    *,
    max_characters: int = MAX_EXTRACTED_CHARACTERS,
) -> str:
    """Extract UTF-8 text from an uploaded TXT or PDF byte sequence.

    Parser errors are converted to generic exceptions and never include source
    content. PDF support is provided by the project's pypdf dependency.
    """
    if not isinstance(content, bytes):
        raise TypeError("document content must be bytes")
    if not content:
        raise EmptyDocumentError("Document is empty")
    if max_characters < 1:
        raise ValueError("maximum extracted text length must be positive")

    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "text/plain":
        try:
            text = content.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            raise MalformedDocumentError("Text document is not valid UTF-8") from None
        return _validate_extracted_text(text, max_characters)
    if media_type != "application/pdf":
        raise UnsupportedDocumentError("Document type is not supported")

    try:
        from pypdf import PdfReader
    except ImportError:
        raise DocumentExtractionError("PDF extraction support is unavailable") from None

    try:
        reader = PdfReader(BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise MalformedDocumentError("Encrypted PDFs are not supported")
        page_text: list[str] = []
        total_characters = 0
        for page in reader.pages:
            extracted = page.extract_text() or ""
            total_characters += len(extracted)
            if total_characters > max_characters:
                raise ExtractionLimitError("Extracted document exceeds the text limit")
            page_text.append(extracted)
        return _validate_extracted_text("\n".join(page_text), max_characters)
    except DocumentExtractionError:
        raise
    except Exception:
        # Parser exceptions can contain source fragments or implementation
        # details; callers receive a stable message only.
        raise MalformedDocumentError("PDF document could not be parsed") from None


def chunk_document_text(
    document: Document,
    text: str,
    *,
    chunk_size_chars: int,
    chunk_overlap_chars: int,
) -> list[DocumentChunk]:
    """Split text into fixed character windows and preserve source identity.

    IDs come from the persisted Document model, never request fields. Windows
    advance by size minus overlap, making boundaries reproducible.
    """
    if not isinstance(document.id, uuid.UUID) or not isinstance(
        document.organization_id, uuid.UUID
    ):
        raise ValueError("document must have persisted tenant identifiers")
    if not isinstance(text, str) or not text.strip():
        raise EmptyDocumentError("Document contains no extractable text")
    try:
        text.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise MalformedDocumentError("Document text is malformed") from None
    if chunk_size_chars < 1 or chunk_overlap_chars < 0:
        raise ValueError("chunk size and overlap are invalid")
    if chunk_overlap_chars >= chunk_size_chars:
        raise ValueError("chunk overlap must be smaller than chunk size")

    chunks: list[DocumentChunk] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size_chars, len(text))
        chunks.append(
            DocumentChunk(
                document_id=document.id,
                organization_id=document.organization_id,
                chunk_index=len(chunks),
                text=text[start:end],
            )
        )
        if end == len(text):
            break
        start = end - chunk_overlap_chars
    return chunks
