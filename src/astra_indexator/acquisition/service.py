from __future__ import annotations

import codecs
import hashlib
import os
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from PIL import Image

from astra_indexator.storage import ObjectStorage, StorageRef


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
    max_image_width: int = 30_000
    max_image_height: int = 30_000
    max_image_pixels: int = 150_000_000
    max_tiff_pages: int = 1_000
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
    def __init__(self, storage: ObjectStorage, workspace_root: Path, policy: AcquisitionPolicy | None = None):
        self.storage = storage
        self.workspace_root = workspace_root
        self.policy = policy or AcquisitionPolicy()

    def acquire(
        self,
        *,
        source_uri: str,
        job_id: UUID,
        attempt_id: UUID,
        original_file_name: str | None = None,
        expected_sha256: str | None = None,
    ) -> AcquiredSource:
        ref = StorageRef.parse(source_uri)
        head = self.storage.head(ref)
        if not head.exists:
            raise AcquisitionError("SOURCE_NOT_FOUND", f"source object does not exist: {ref.as_uri()}")
        if head.size_bytes is not None and head.size_bytes > self.policy.max_source_bytes:
            raise AcquisitionError("SOURCE_TOO_LARGE", "source exceeds configured maximum")

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
            with part.open("xb") as target:
                for chunk in self.storage.iter_bytes(ref):
                    if not chunk:
                        continue
                    count += len(chunk)
                    if count > self.policy.max_source_bytes:
                        raise AcquisitionError("SOURCE_TOO_LARGE", "streamed source exceeds configured maximum")
                    target.write(chunk)
                    digest.update(chunk)
                target.flush()
                os.fsync(target.fileno())

            if count == 0:
                raise AcquisitionError("EMPTY_SOURCE", "zero-byte source is not supported")
            if head.size_bytes is not None and count != head.size_bytes:
                raise AcquisitionError("SOURCE_SIZE_MISMATCH", "HEAD size differs from acquired byte count")

            sha = digest.hexdigest()
            normalized_expected = expected_sha256.removeprefix("sha256:") if expected_sha256 else None
            if normalized_expected and normalized_expected.lower() != sha.lower():
                raise AcquisitionError("SOURCE_CONTENT_MISMATCH", "acquired SHA-256 differs from durable source hash")

            fmt, mime, warnings = self._validate(part, original_file_name)
            os.replace(part, validated)
            return AcquiredSource(
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
        except Exception:
            part.unlink(missing_ok=True)
            raise

    def cleanup_workspace(self, job_id: UUID, attempt_id: UUID) -> None:
        shutil.rmtree(self.workspace_root / str(job_id) / str(attempt_id), ignore_errors=True)

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
            self._validate_docx(path)
            fmt, mime = "DOCX", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            self._validate_utf8_text(path)
            if ext in {".md", ".markdown"}:
                fmt, mime = "MARKDOWN", "text/markdown"
            else:
                fmt, mime = "TXT", "text/plain"

        accepted_exts = {
            "PDF": {".pdf"},
            "PNG": {".png"},
            "JPEG": {".jpg", ".jpeg"},
            "TIFF": {".tif", ".tiff"},
            "DOCX": {".docx"},
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
                        remaining = 1024 * 1024 - probed
                        probe = chunk[:remaining]
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
                if (
                    width > self.policy.max_image_width
                    or height > self.policy.max_image_height
                    or width * height > self.policy.max_image_pixels
                ):
                    raise AcquisitionError("IMAGE_RESOURCE_LIMIT", "image dimensions exceed configured limits")
                frames = getattr(image, "n_frames", 1)
                if expected == "TIFF" and frames > self.policy.max_tiff_pages:
                    raise AcquisitionError("IMAGE_RESOURCE_LIMIT", "TIFF page count exceeds configured limit")
                image.verify()
        except AcquisitionError:
            raise
        except Exception as exc:
            raise AcquisitionError("MALFORMED_IMAGE", "image validation failed") from exc

    def _validate_docx(self, path: Path) -> None:
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                if len(infos) > self.policy.max_container_entries:
                    raise AcquisitionError("CONTAINER_RESOURCE_LIMIT", "too many OOXML entries")
                total = 0
                names = set()
                for info in infos:
                    name = info.filename.replace("\\", "/")
                    if name.startswith("/") or any(part == ".." for part in name.split("/")):
                        raise AcquisitionError("CONTAINER_PATH_TRAVERSAL", "unsafe OOXML entry path")
                    if name in names:
                        raise AcquisitionError("CONTAINER_DUPLICATE_ENTRY", "duplicate OOXML entry name")
                    names.add(name)
                    if info.flag_bits & 0x1:
                        raise AcquisitionError("CONTAINER_ENCRYPTED", "encrypted OOXML entries are unsupported")
                    if info.file_size > self.policy.max_single_entry_uncompressed_bytes:
                        raise AcquisitionError("CONTAINER_RESOURCE_LIMIT", "OOXML entry too large")
                    total += info.file_size
                    if total > self.policy.max_total_uncompressed_bytes:
                        raise AcquisitionError("CONTAINER_RESOURCE_LIMIT", "OOXML uncompressed total too large")
                    ratio = info.file_size / max(info.compress_size, 1)
                    if ratio > self.policy.max_compression_ratio:
                        raise AcquisitionError("CONTAINER_RESOURCE_LIMIT", "OOXML compression ratio exceeds limit")
                if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                    raise AcquisitionError("UNSUPPORTED_ZIP_CONTAINER", "ZIP is not a DOCX package")
        except AcquisitionError:
            raise
        except (zipfile.BadZipFile, OSError) as exc:
            raise AcquisitionError("MALFORMED_CONTAINER", "OOXML container validation failed") from exc
