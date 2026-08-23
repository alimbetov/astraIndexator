from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from typing import Protocol


class ContainerPolicy(Protocol):
    max_container_entries: int
    max_total_uncompressed_bytes: int
    max_single_entry_uncompressed_bytes: int
    max_compression_ratio: float
    max_nested_container_depth: int


class ExtendedFormatValidationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


ZIP_FORMATS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "DOCX",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ("[Content_Types].xml", "word/document.xml"),
    ),
    (
        "XLSX",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ("[Content_Types].xml", "xl/workbook.xml"),
    ),
    (
        "PPTX",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ("[Content_Types].xml", "ppt/presentation.xml"),
    ),
    (
        "EPUB",
        "application/epub+zip",
        ("META-INF/container.xml", "mimetype"),
    ),
    (
        "ODT",
        "application/vnd.oasis.opendocument.text",
        ("content.xml", "mimetype"),
    ),
)


def validate_and_identify_zip(path: Path, policy: ContainerPolicy) -> tuple[str, str]:
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > policy.max_container_entries:
                raise ExtendedFormatValidationError("CONTAINER_RESOURCE_LIMIT", "too many container entries")
            total = 0
            names: set[str] = set()
            for info in infos:
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or any(part == ".." for part in name.split("/")):
                    raise ExtendedFormatValidationError("CONTAINER_PATH_TRAVERSAL", "unsafe container entry path")
                if name in names:
                    raise ExtendedFormatValidationError("CONTAINER_DUPLICATE_ENTRY", "duplicate container entry name")
                names.add(name)
                if info.flag_bits & 0x1:
                    raise ExtendedFormatValidationError("CONTAINER_ENCRYPTED", "encrypted container entries are unsupported")
                if info.file_size > policy.max_single_entry_uncompressed_bytes:
                    raise ExtendedFormatValidationError("CONTAINER_RESOURCE_LIMIT", "container entry too large")
                total += info.file_size
                if total > policy.max_total_uncompressed_bytes:
                    raise ExtendedFormatValidationError("CONTAINER_RESOURCE_LIMIT", "container uncompressed total too large")
                if info.file_size / max(info.compress_size, 1) > policy.max_compression_ratio:
                    raise ExtendedFormatValidationError("CONTAINER_RESOURCE_LIMIT", "container compression ratio exceeds limit")
                lower = name.lower()
                if lower.endswith((".zip", ".docx", ".xlsx", ".pptx", ".odt", ".epub")) and policy.max_nested_container_depth == 0:
                    raise ExtendedFormatValidationError("CONTAINER_NESTING_LIMIT", "nested container processing is disabled")

            for fmt, mime, required in ZIP_FORMATS:
                if all(item in names for item in required):
                    if fmt == "EPUB":
                        value = archive.read("mimetype")[:64].decode("ascii", errors="ignore").strip()
                        if value != "application/epub+zip":
                            continue
                    if fmt == "ODT":
                        value = archive.read("mimetype")[:128].decode("ascii", errors="ignore").strip()
                        if value != "application/vnd.oasis.opendocument.text":
                            continue
                    return fmt, mime
            raise ExtendedFormatValidationError("UNSUPPORTED_ZIP_CONTAINER", "ZIP package is not an admitted document format")
    except ExtendedFormatValidationError:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise ExtendedFormatValidationError("MALFORMED_CONTAINER", "container validation failed") from exc


def validate_rtf(path: Path, *, max_group_depth: int = 256) -> None:
    depth = 0
    escaped = False
    with path.open("rb") as stream:
        prefix = stream.read(16)
        if not prefix.lstrip().startswith(b"{\\rtf"):
            raise ExtendedFormatValidationError("MALFORMED_RTF", "RTF signature is missing")
        stream.seek(0)
        while chunk := stream.read(1024 * 1024):
            if b"\x00" in chunk:
                raise ExtendedFormatValidationError("MALFORMED_RTF", "RTF contains NUL bytes")
            for byte in chunk:
                char = chr(byte)
                if escaped:
                    escaped = False
                    continue
                if char == "\\":
                    escaped = True
                elif char == "{":
                    depth += 1
                    if depth > max_group_depth:
                        raise ExtendedFormatValidationError("RTF_RESOURCE_LIMIT", "RTF group nesting exceeds limit")
                elif char == "}":
                    depth -= 1
                    if depth < 0:
                        raise ExtendedFormatValidationError("MALFORMED_RTF", "unbalanced RTF groups")
    if depth != 0:
        raise ExtendedFormatValidationError("MALFORMED_RTF", "unbalanced RTF groups")


def detect_html(sample: str, extension: str) -> bool:
    lowered = sample.lstrip().lower()
    strong = lowered.startswith("<!doctype html") or bool(re.search(r"<html(?:\s|>)", lowered[:4096]))
    fragment = bool(re.search(r"<(article|section|main|h[1-6]|p|table)(?:\s|>)", lowered[:16384]))
    return strong or (extension in {".html", ".htm"} and fragment)


def detect_csv(sample: str, extension: str) -> tuple[bool, str | None, str | None]:
    if not sample.strip():
        return False, None, None
    try:
        dialect = csv.Sniffer().sniff(sample[:65536], delimiters=",;\t")
    except csv.Error:
        return False, None, None
    reader = csv.reader(io.StringIO(sample[:65536]), dialect)
    rows: list[list[str]] = []
    try:
        for row in reader:
            if any(cell.strip() for cell in row):
                rows.append(row)
            if len(rows) >= 12:
                break
    except csv.Error:
        return False, None, None
    if len(rows) < 2:
        return False, None, None
    widths = [len(row) for row in rows]
    stable = min(widths) >= 2 and max(widths) - min(widths) <= 1
    hinted = extension in {".csv", ".tsv"}
    if not stable or (not hinted and len(rows) < 3):
        return False, None, None
    return True, dialect.delimiter, dialect.quotechar
