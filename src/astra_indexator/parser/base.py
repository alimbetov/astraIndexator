from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID

from astra_indexator.acquisition import AcquiredSource

from .model import ParsedDocument, QualityStatus


class ParserError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ParseLimits:
    max_pages: int = 5_000
    max_elements: int = 200_000
    max_extracted_chars: int = 50_000_000
    max_embedded_images: int = 10_000
    max_tables: int = 10_000
    max_sheets: int = 1_000
    max_rows: int = 1_000_000
    max_columns: int = 16_384
    max_non_empty_cells: int = 5_000_000
    max_cell_chars: int = 1_000_000
    max_slides: int = 20_000
    max_shapes: int = 500_000
    max_dom_nodes: int = 500_000
    max_epub_spine_items: int = 20_000


@dataclass(frozen=True, slots=True)
class QualityProfile:
    min_good_chars_per_page: int = 80
    low_signal_chars_per_page: int = 20
    scanned_image_area_ratio: float = 0.70


@dataclass(frozen=True, slots=True)
class ParseContext:
    job_id: UUID
    attempt_id: UUID
    document_id: UUID
    document_version: int
    source_sha256: str
    parser_profile: str = "default-v1"
    limits: ParseLimits = ParseLimits()
    quality_profile: QualityProfile = QualityProfile()


class DocumentHandler(Protocol):
    format_name: str

    def parse(self, source: AcquiredSource, context: ParseContext) -> ParsedDocument: ...


class FileTypeHandlerRegistry:
    def __init__(self, handlers: list[DocumentHandler]):
        by_format: dict[str, DocumentHandler] = {}
        for handler in handlers:
            key = handler.format_name.upper()
            if key in by_format:
                raise ValueError(f"duplicate parser handler for detected format {key}")
            by_format[key] = handler
        self._handlers = by_format

    def resolve(self, detected_format: str) -> DocumentHandler:
        key = detected_format.upper()
        handler = self._handlers.get(key)
        if handler is None:
            raise ParserError("PARSER_UNSUPPORTED_FORMAT", f"no parser handler for detected format {key}")
        return handler


class DocumentParserService:
    def __init__(self, registry: FileTypeHandlerRegistry):
        self.registry = registry

    def parse(self, source: AcquiredSource, context: ParseContext) -> ParsedDocument:
        if source.sha256.lower() != context.source_sha256.lower():
            raise ParserError("PARSER_SOURCE_HASH_MISMATCH", "ParseContext source hash differs from acquired source")

        result = self.registry.resolve(source.detected_format).parse(source, context)
        quality = result.quality

        # An OCR candidate is evidence for OCR consideration, not an instruction
        # to classify the complete document as OCR_REQUIRED. Native documents may
        # contain embedded images that M5 can inspect independently.
        required_page_modes = {"SCANNED_IMAGE", "LOW_SIGNAL"}
        page_requires_ocr = any(mode in required_page_modes for mode in quality.page_modes)
        if quality.status == QualityStatus.OCR_REQUIRED and quality.native_text_chars > 0 and not page_requires_ocr:
            quality = replace(quality, status=QualityStatus.GOOD)

        # Partial-structure diagnostics must not be hidden behind a GOOD result.
        if quality.status == QualityStatus.GOOD and any(warning.endswith("_PARTIAL") for warning in quality.warnings):
            quality = replace(quality, status=QualityStatus.PARTIAL)

        if quality is not result.quality:
            result = replace(result, quality=quality)
        return result
