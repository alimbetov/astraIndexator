from __future__ import annotations

import io
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from astra_indexator.acquisition import AcquisitionError, AcquisitionPolicy, SafeAcquisitionService
from astra_indexator.storage import ObjectHead, StorageRef


class FakeStorage:
    def __init__(self, payload: bytes, *, advertised_size: int | None = None):
        self.payload = payload
        self.advertised_size = len(payload) if advertised_size is None else advertised_size

    def head(self, ref: StorageRef) -> ObjectHead:
        return ObjectHead(exists=True, size_bytes=self.advertised_size, etag='"etag-1"')

    def iter_bytes(self, ref: StorageRef, *, chunk_size: int = 1024 * 1024):
        for offset in range(0, len(self.payload), 3):
            yield self.payload[offset : offset + 3]


def _service(tmp_path: Path, payload: bytes, **policy_kwargs) -> SafeAcquisitionService:
    return SafeAcquisitionService(
        FakeStorage(payload), tmp_path, AcquisitionPolicy(**policy_kwargs)
    )


def test_pdf_is_streamed_hashed_and_promoted(tmp_path: Path) -> None:
    payload = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
    result = _service(tmp_path, payload).acquire(
        source_uri="seaweed://documents/original/doc.pdf",
        job_id=uuid4(),
        attempt_id=uuid4(),
        original_file_name="contract.pdf",
    )
    assert result.detected_format == "PDF"
    assert result.size_bytes == len(payload)
    assert result.local_path.name == "source.validated"
    assert result.local_path.read_bytes() == payload
    assert len(result.sha256) == 64


def test_stream_overrun_is_rejected_and_partial_file_not_promoted(tmp_path: Path) -> None:
    payload = b"hello world"
    service = _service(tmp_path, payload, max_source_bytes=5)
    job_id, attempt_id = uuid4(), uuid4()
    with pytest.raises(AcquisitionError) as exc:
        service.acquire(
            source_uri="seaweed://documents/original/a.txt",
            job_id=job_id,
            attempt_id=attempt_id,
            original_file_name="a.txt",
        )
    assert exc.value.code == "SOURCE_TOO_LARGE"
    assert not (tmp_path / str(job_id) / str(attempt_id) / "source.validated").exists()


def test_durable_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(AcquisitionError) as exc:
        _service(tmp_path, b"hello").acquire(
            source_uri="seaweed://documents/a.txt",
            job_id=uuid4(),
            attempt_id=uuid4(),
            original_file_name="a.txt",
            expected_sha256="0" * 64,
        )
    assert exc.value.code == "SOURCE_CONTENT_MISMATCH"


def test_executable_masquerading_as_pdf_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(AcquisitionError) as exc:
        _service(tmp_path, b"MZ" + b"x" * 100).acquire(
            source_uri="seaweed://documents/fake.pdf",
            job_id=uuid4(),
            attempt_id=uuid4(),
            original_file_name="fake.pdf",
        )
    assert exc.value.code == "UNSUPPORTED_EXECUTABLE"


def test_markdown_is_admitted_only_after_utf8_validation(tmp_path: Path) -> None:
    payload = "# Тест\nҚазақша мәтін\n".encode()
    result = _service(tmp_path, payload).acquire(
        source_uri="seaweed://documents/readme.md",
        job_id=uuid4(),
        attempt_id=uuid4(),
        original_file_name="README.md",
    )
    assert result.detected_format == "MARKDOWN"


def test_invalid_utf8_text_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(AcquisitionError) as exc:
        _service(tmp_path, b"abc\xffdef").acquire(
            source_uri="seaweed://documents/bad.txt",
            job_id=uuid4(),
            attempt_id=uuid4(),
            original_file_name="bad.txt",
        )
    assert exc.value.code == "TEXT_ENCODING_UNSUPPORTED"


def _docx(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_minimal_docx_container_is_admitted(tmp_path: Path) -> None:
    payload = _docx({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<document/>"})
    result = _service(tmp_path, payload).acquire(
        source_uri="seaweed://documents/a.docx",
        job_id=uuid4(),
        attempt_id=uuid4(),
        original_file_name="a.docx",
    )
    assert result.detected_format == "DOCX"


def test_docx_path_traversal_is_rejected(tmp_path: Path) -> None:
    payload = _docx(
        {"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<document/>", "../evil": b"x"}
    )
    with pytest.raises(AcquisitionError) as exc:
        _service(tmp_path, payload).acquire(
            source_uri="seaweed://documents/a.docx",
            job_id=uuid4(),
            attempt_id=uuid4(),
            original_file_name="a.docx",
        )
    assert exc.value.code == "CONTAINER_PATH_TRAVERSAL"


def test_storage_ref_rejects_parent_traversal() -> None:
    with pytest.raises(ValueError):
        StorageRef.parse("seaweed://documents/a/../secret.pdf")
