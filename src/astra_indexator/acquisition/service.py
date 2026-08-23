from __future__ import annotations

import codecs
import hashlib
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import httpx
from PIL import Image

from astra_indexator.storage import ObjectStorage, StorageRef

from .extended_formats import (
    ExtendedFormatValidationError,
    detect_csv,
    detect_html,
    validate_and_identify_zip,
    validate_rtf,
)
from .metrics import AcquisitionMetrics, NoopAcquisitionMetrics
from .workspace import WorkspaceCapacityError, WorkspaceManager


class AcquisitionError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AcquisitionPolicy:
    max_source_bytes: int = 100 * 1024 * 1024
    max_container_entries: int = 10_000
    max_total_uncompressed_bytes: int = 500 * 1024 * 1024
    max_single_entry_uncompressed_bytes: int = 100 * 1024 * 1024
    max_compression_ratio: float = 100.0
    max_nested_container_depth: int = 0
    max_image_width: int = 30_000
    max_image_height: int = 30_000
    max_image_pixels: int = 150_000_000
    max_tiff_pages: int = 1_000
    total_deadline_seconds: float = 300.0
    validation_profile: str = "default-v1"


@dataclass(frozen=True, slots=True)
class AcquiredSource:
    source_ref: StorageRef
    local_path: Path
    original_file_name: str | None
    detected_format: str
    detected_content_type: str
    size_bytes: int
    sha256: str
    etag: str | None
    version_id: str | None
    validation_profile: str
    warnings: tuple[str, ...]
    acquired_at: datetime


class SafeAcquisitionService:
    def __init__(
        self,
        storage: ObjectStorage,
        workspace_root: Path,
        policy: AcquisitionPolicy | None = None,
        *,
        workspace_manager: WorkspaceManager | None = None,
        metrics: AcquisitionMetrics | None = None,
    ):
        self.storage = storage
        self.workspace_root = workspace_root
        self.policy = policy or AcquisitionPolicy()
        self.workspace_manager = workspace_manager
        self.metrics = metrics or NoopAcquisitionMetrics()

    def acquire(
        self,
        *,
        source_uri: str,
        job_id: UUID,
        attempt_id: UUID,
        original_file_name: str | None = None,
        expected_sha256: str | None = None,
    ) -> AcquiredSource:
        started = time.monotonic()
        ref = StorageRef.parse(source_uri)
        try:
            head_started = time.monotonic()
            try:
                head = self.storage.head(ref)
                self.metrics.storage_request(operation="head", result="success", duration_seconds=time.monotonic() - head_started)
            except httpx.ConnectTimeout as exc:
                self.metrics.storage_request(operation="head", result="connect_timeout", duration_seconds=time.monotonic() - head_started)
                raise AcquisitionError("STORAGE_CONNECT_TIMEOUT", "source storage connect timeout") from exc
            except httpx.ReadTimeout as exc:
                self.metrics.storage_request(operation="head", result="read_timeout", duration_seconds=time.monotonic() - head_started)
                raise AcquisitionError("STORAGE_READ_TIMEOUT", "source storage read timeout") from exc

            if not head.exists:
                raise AcquisitionError("SOURCE_NOT_FOUND", f"source object does not exist: {ref.as_uri()}")
            if head.size_bytes is not None and head.size_bytes > self.policy.max_source_bytes:
                raise AcquisitionError("SOURCE_TOO_LARGE", "source exceeds configured maximum")
            if self.workspace_manager is not None:
                try:
                    self.workspace_manager.preflight(expected_bytes=head.size_bytes)
                except WorkspaceCapacityError as exc:
                    raise AcquisitionError(exc.code, str(exc)) from exc

            workspace = self.workspace_root / str(job_id) / str(attempt_id)
            incoming = workspace / "incoming"
            incoming.mkdir(parents=True, exist_ok=True)
            part = incoming / "source.part"
            validated = workspace / "source.validated"
            part.unlink(missing_ok=True)
            validated.unlink(missing_ok=True)

            digest = hashlib.sha256()
            count = 0
            try:
                stream_started = time.monotonic()
                with part.open("xb") as target:
                    try:
                        for chunk in self.storage.iter_bytes(ref):
                            self._check_deadline(started)
                            if not chunk:
                                continue
                            count += len(chunk)
                            if count > self.policy.max_source_bytes:
                                raise AcquisitionError("SOURCE_TOO_LARGE", "streamed source exceeds configured maximum")
                            target.write(chunk)
                            digest.update(chunk)
                            if self.workspace_manager is not None:
                                self.workspace_manager.enforce_attempt_usage(job_id, attempt_id)
                    except httpx.ConnectTimeout as exc:
                        raise AcquisitionError("STORAGE_CONNECT_TIMEOUT", "source storage connect timeout") from exc
                    except httpx.ReadTimeout as exc:
                        raise AcquisitionError("STORAGE_READ_TIMEOUT", "source storage read timeout") from exc
                    target.flush()
                    os.fsync(target.fileno())
                self.metrics.storage_request(operation="get", result="success", duration_seconds=time.monotonic() - stream_started)

                if count == 0:
                    raise AcquisitionError("EMPTY_SOURCE", "zero-byte source is not supported")
                if head.size_bytes is not None and count != head.size_bytes:
                    raise AcquisitionError("SOURCE_SIZE_MISMATCH", "HEAD size differs from acquired byte count")

                sha = digest.hexdigest()
                normalized_expected = expected_sha256.removeprefix("sha256:") if expected_sha256 else None
                if normalized_expected and normalized_expected.lower() != sha.lower():
                    raise AcquisitionError("SOURCE_CONTENT_MISMATCH", "acquired SHA-256 differs from durable source hash")

                self._check_deadline(started)
                fmt, mime, warnings = self._validate(part, original_file_name)
                os.replace(part, validated)
                result = AcquiredSource(
                    source_ref=ref,
                    local_path=validated,
                    original_file_name=Path(original_file_name).name if original_file_name else None,
                    detected_format=fmt,
                    detected_content_type=mime,
                    size_bytes=count,
                    sha256=sha,
                    etag=head.etag,
                    version_id=head.version_id,
                    validation_profile=self.policy.validation_profile,
                    warnings=tuple(warnings),
                    acquired_at=datetime.now(timezone.utc),
                )
                self.metrics.acquisition_completed(
                    detected_format=fmt,
                    size_bytes=count,
                    duration_seconds=time.monotonic() - started,
                )
                return result
            except Exception:
                part.unlink(missing_ok=True)
                raise
        except AcquisitionError as exc:
            self.metrics.acquisition_failed(error_code=exc.code, duration_seconds=time.monotonic() - started)
            raise

    def cleanup_workspace(self, job_id: UUID, attempt_id: UUID) -> None:
        if self.workspace_manager is not None:
            self.workspace_manager.cleanup_attempt(job_id, attempt_id)
        else:
            shutil.rmtree(self.workspace_root / str(job_id) / str(attempt_id), ignore_errors=True)

    def _check_deadline(self, started: float) -> None:
        if time.monotonic() - started > self.policy.total_deadline_seconds:
            raise AcquisitionError("ACQUISITION_DEADLINE_EXCEEDED", "total acquisition deadline exceeded")

    def _validate(self, path: Path, original_name: str | None) -> tuple[str, str, list[str]]:
        with path.open("rb") as stream:
            prefix = stream.read(64)
            try:
                stream.seek(-2048, os.SEEK_END)
            except OSError:
                stream.seek(0)
            suffix = stream.read(2048)
        ext = Path(original_name).suffix.lower() if original_name else ""
        warnings: list[str] = []

        if prefix.startswith(b"MZ") or prefix.startswith(b"\x7fELF"):
            raise AcquisitionError("UNSUPPORTED_EXECUTABLE", "executable content is not a supported document")
        if prefix.startswith(b"%PDF-"):
            if b"%%EOF" not in suffix:
                raise AcquisitionError("MALFORMED_PDF", "PDF EOF marker not found in bounded trailer probe")
            fmt, mime = "PDF", "application/pdf"
        elif prefix.startswith(b"\x89PNG\r\n\x1a\n"):
            self._validate_image(path, "PNG")
            fmt, mime = "PNG", "image/png"
        elif prefix.startswith(b"\xff\xd8\xff"):
            self._validate_image(path, "JPEG")
            fmt, mime = "JPEG", "image/jpeg"
        elif prefix.startswith((b"II*\x00", b"MM\x00*")):
            self._validate_image(path, "TIFF")
            fmt, mime = "TIFF", "image/tiff"
        elif prefix.startswith(b"PK\x03\x04"):
            try:
                fmt, mime = validate_and_identify_zip(path, self.policy)
            except ExtendedFormatValidationError as exc:
                raise AcquisitionError(exc.code, str(exc)) from exc
        elif prefix.lstrip().startswith(b"{\\rtf"):
            try:
                validate_rtf(path)
            except ExtendedFormatValidationError as exc:
                raise AcquisitionError(exc.code, str(exc)) from exc
            fmt, mime = "RTF", "application/rtf"
        else:
            self._validate_utf8_text(path)
            with path.open("r", encoding="utf-8") as stream:
                sample = stream.read(65_536)
            if detect_html(sample, ext):
                fmt, mime = "HTML", "text/html"
            else:
                is_csv, delimiter, quotechar = detect_csv(sample, ext)
                if is_csv:
                    fmt, mime = "CSV", "text/csv"
                    if delimiter:
                        warnings.append(f"CSV_DELIMITER:{repr(delimiter)}")
                    if quotechar:
                        warnings.append(f"CSV_QUOTE:{repr(quotechar)}")
                elif ext in {".md", ".markdown"}:
                    fmt, mime = "MARKDOWN", "text/markdown"
                else:
                    fmt, mime = "TXT", "text/plain"

        accepted_exts = {
            "PDF": {".pdf"},
            "PNG": {".png"},
            "JPEG": {".jpg", ".jpeg"},
            "TIFF": {".tif", ".tiff"},
            "DOCX": {".docx"},
            "XLSX": {".xlsx"},
            "PPTX": {".pptx"},
            "CSV": {".csv", ".tsv"},
            "HTML": {".html", ".htm"},
            "RTF": {".rtf"},
            "ODT": {".odt"},
            "EPUB": {".epub"},
        }.get(fmt)
        if accepted_exts and ext and ext not in accepted_exts:
            warnings.append("FILE_TYPE_MISMATCH")
        return fmt, mime, warnings

    @staticmethod
    def _validate_utf8_text(path: Path) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        probed = 0
        try:
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    if probed < 1024 * 1024:
                        probe = chunk[: 1024 * 1024 - probed]
                        if b"\x00" in probe:
                            raise AcquisitionError("UNSUPPORTED_FORMAT", "binary format is not supported")
                        probed += len(probe)
                    decoder.decode(chunk, final=False)
                decoder.decode(b"", final=True)
        except UnicodeDecodeError as exc:
            raise AcquisitionError("TEXT_ENCODING_UNSUPPORTED", "text source is not valid UTF-8") from exc

    def _validate_image(self, path: Path, expected: str) -> None:
        try:
            with Image.open(path) as image:
                if image.format != expected:
                    raise AcquisitionError("IMAGE_FORMAT_MISMATCH", "image decoder disagrees with signature")
                width, height = image.size
                if width > self.policy.max_image_width or height > self.policy.max_image_height or width * height > self.policy.max_image_pixels:
                    raise AcquisitionError("IMAGE_RESOURCE_LIMIT", "image dimensions exceed configured limits")
                if expected == "TIFF" and getattr(image, "n_frames", 1) > self.policy.max_tiff_pages:
                    raise AcquisitionError("IMAGE_RESOURCE_LIMIT", "TIFF page count exceeds configured limit")
                image.verify()
        except AcquisitionError:
            raise
        except Exception as exc:
            raise AcquisitionError("MALFORMED_IMAGE", "image validation failed") from exc
