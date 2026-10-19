"""Focused database-independent tests for M3 extraction and chunking."""

import sys
import uuid
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.ingestion import (
    DocumentExtractionError,
    EmptyDocumentError,
    ExtractionLimitError,
    MalformedDocumentError,
    UnsupportedDocumentError,
    chunk_document_text,
    extract_document_text,
)


def _document():
    return SimpleNamespace(id=uuid.uuid4(), organization_id=uuid.uuid4())


def test_utf8_txt_extraction_normalizes_newlines_and_removes_utf8_bom():
    result = extract_document_text(b"\xef\xbb\xbfalpha\r\nbeta\r", "text/plain; charset=utf-8")

    assert result == "alpha\nbeta\n"


def test_txt_extraction_rejects_invalid_utf8_and_empty_content():
    with pytest.raises(MalformedDocumentError, match="not valid UTF-8"):
        extract_document_text(b"\xff\xfe", "text/plain")
    with pytest.raises(EmptyDocumentError):
        extract_document_text(b" \r\n\t", "text/plain")
    with pytest.raises(EmptyDocumentError):
        extract_document_text(b"", "text/plain")


def test_extraction_rejects_unsupported_media_type_and_text_limit():
    with pytest.raises(UnsupportedDocumentError):
        extract_document_text(b"data", "application/octet-stream")
    with pytest.raises(ExtractionLimitError):
        extract_document_text(b"abcdef", "text/plain", max_characters=5)


def test_pdf_extraction_uses_reader_and_joins_pages(monkeypatch):
    class Page:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    class Reader:
        def __init__(self, stream, *, strict):
            assert stream.read() == b"fake-pdf"
            assert strict is True
            self.is_encrypted = False
            self.pages = [Page("first page"), Page("second page")]

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=Reader))

    assert extract_document_text(b"fake-pdf", "application/pdf") == "first page\nsecond page"


def test_pdf_extraction_rejects_encrypted_malformed_and_oversized_results(monkeypatch):
    class EncryptedReader:
        def __init__(self, *args, **kwargs):
            self.is_encrypted = True

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=EncryptedReader))
    with pytest.raises(MalformedDocumentError, match="Encrypted PDFs"):
        extract_document_text(b"pdf", "application/pdf")

    class BrokenReader:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("private parser detail")

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=BrokenReader))
    with pytest.raises(MalformedDocumentError, match="could not be parsed") as error:
        extract_document_text(b"pdf", "application/pdf")
    assert "private parser detail" not in str(error.value)

    class LargeReader:
        def __init__(self, *args, **kwargs):
            self.is_encrypted = False
            self.pages = [SimpleNamespace(extract_text=lambda: "too much")]

    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=LargeReader))
    with pytest.raises(ExtractionLimitError):
        extract_document_text(b"pdf", "application/pdf", max_characters=4)


def test_pdf_extraction_fails_safely_when_dependency_is_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)

    with pytest.raises(DocumentExtractionError, match="support is unavailable"):
        extract_document_text(b"pdf", "application/pdf")


def test_chunking_is_deterministic_with_exact_overlap_and_source_metadata():
    document = _document()
    chunks = chunk_document_text(
        document,
        "abcdefghij",
        chunk_size_chars=5,
        chunk_overlap_chars=2,
    )

    assert [chunk.text for chunk in chunks] == ["abcde", "defgh", "ghij"]
    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]
    assert all(chunk.document_id == document.id for chunk in chunks)
    assert all(chunk.organization_id == document.organization_id for chunk in chunks)


def test_chunking_handles_short_text_and_rejects_invalid_inputs():
    document = _document()
    assert [chunk.text for chunk in chunk_document_text(
        document, "short", chunk_size_chars=10, chunk_overlap_chars=2
    )] == ["short"]

    with pytest.raises(EmptyDocumentError):
        chunk_document_text(document, " \n ", chunk_size_chars=10, chunk_overlap_chars=1)
    with pytest.raises(ValueError, match="overlap"):
        chunk_document_text(document, "text", chunk_size_chars=4, chunk_overlap_chars=4)
    with pytest.raises(ValueError, match="identifiers"):
        chunk_document_text(
            SimpleNamespace(id=None, organization_id=document.organization_id),
            "text",
            chunk_size_chars=4,
            chunk_overlap_chars=1,
        )


def test_chunk_configuration_is_bounded_and_overlap_must_be_smaller_than_size():
    base = {
        "database_url": "postgresql+psycopg://user:pass@localhost/db",
        "cors_origins": "http://localhost:5173",
        "csrf_secret": "local-test-csrf-secret-0123456789",
        "cookie_secure": False,
        "_env_file": None,
    }
    assert Settings(**base).chunk_size_chars == 1000
    assert Settings(**base).chunk_overlap_chars == 150
    assert Settings(**base, chunk_size_chars=200, chunk_overlap_chars=20).chunk_size_chars == 200
    with pytest.raises(ValueError, match="smaller than"):
        Settings(**base, chunk_size_chars=100, chunk_overlap_chars=100)
