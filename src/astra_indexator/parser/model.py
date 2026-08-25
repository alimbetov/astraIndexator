from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID


class ElementType(StrEnum):
    HEADING = "HEADING"
    PARAGRAPH = "PARAGRAPH"
    LIST = "LIST"
    LIST_ITEM = "LIST_ITEM"
    TABLE = "TABLE"
    IMAGE = "IMAGE"
    CAPTION = "CAPTION"
    CODE_BLOCK = "CODE_BLOCK"
    PAGE_BREAK = "PAGE_BREAK"
    OTHER = "OTHER"


class QualityStatus(StrEnum):
    GOOD = "GOOD"
    LOW_SIGNAL = "LOW_SIGNAL"
    OCR_REQUIRED = "OCR_REQUIRED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class SourceGeometry:
    page_number: int | None = None
    x0: float | None = None
    y0: float | None = None
    x1: float | None = None
    y1: float | None = None
    page_width: float | None = None
    page_height: float | None = None
    coordinate_space: str | None = None


@dataclass(frozen=True, slots=True)
class OcrCandidate:
    candidate_id: str
    scope: str
    page_number: int | None
    reason: str
    element_id: str | None = None
    geometry: SourceGeometry | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DocumentElement:
    element_id: str
    type: ElementType
    order_index: int
    text: str | None = None
    parent_element_id: str | None = None
    level: int | None = None
    geometry: SourceGeometry | None = None
    section_path: tuple[str, ...] = ()
    source_locator: dict[str, Any] = field(default_factory=dict)
    style_hints: dict[str, Any] = field(default_factory=dict)
    language_hint: str | None = None
    role: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParserIdentity:
    name: str
    version: str
    profile: str
    reading_order_profile: str = "reading-order-v1"


@dataclass(frozen=True, slots=True)
class ParseQuality:
    status: QualityStatus
    native_text_chars: int
    ocr_candidate_count: int
    warnings: tuple[str, ...] = ()
    page_modes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    schema_version: str
    document_id: UUID
    document_version: int
    source_sha256: str
    detected_format: str
    parser: ParserIdentity
    elements: tuple[DocumentElement, ...]
    ocr_candidates: tuple[OcrCandidate, ...]
    quality: ParseQuality


def deterministic_element_id(
    *,
    document_id: UUID,
    document_version: int,
    source_sha256: str,
    detected_format: str,
    locator: str,
    element_type: ElementType,
) -> str:
    payload = "\x1f".join(
        [
            str(document_id),
            str(document_version),
            source_sha256,
            detected_format,
            locator,
            element_type.value,
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
