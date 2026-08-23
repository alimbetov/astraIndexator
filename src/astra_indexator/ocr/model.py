from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from astra_indexator.parser import DocumentElement, ParsedDocument, SourceGeometry


class OcrMode(StrEnum):
    DISABLED = "OCR_DISABLED"
    IF_NEEDED = "OCR_IF_NEEDED"
    FORCE = "OCR_FORCE"


class OcrDecision(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED = "REQUIRED"
    REQUIRED_BUT_DISABLED = "REQUIRED_BUT_DISABLED"
    REJECTED_RESOURCE_LIMIT = "REJECTED_RESOURCE_LIMIT"
    UNSUPPORTED = "UNSUPPORTED"


class ReconciliationAction(StrEnum):
    KEEP_NATIVE = "KEEP_NATIVE"
    KEEP_OCR = "KEEP_OCR"
    KEEP_BOTH_DISTINCT_REGIONS = "KEEP_BOTH_DISTINCT_REGIONS"
    MERGE_COMPLEMENTARY = "MERGE_COMPLEMENTARY"
    DROP_DUPLICATE_OCR = "DROP_DUPLICATE_OCR"
    FLAG_CONFLICT = "FLAG_CONFLICT"


@dataclass(frozen=True, slots=True)
class OcrProfile:
    profile_id: str = "ocr_cpu_ru_kk_en_v1"
    languages: tuple[str, ...] = ("ru", "kk", "en")
    device: str = "cpu"
    min_confidence: float = 0.35
    hard_confidence_floor: float = 0.10
    max_pages_per_job: int = 500
    max_pixels_per_page: int = 24_000_000
    max_total_pixels_per_job: int = 250_000_000
    timeout_per_candidate_seconds: float = 60.0
    timeout_per_job_seconds: float = 900.0
    render_dpi: int = 200
    preprocessing_version: str = "ocr-preprocess-v1"
    reconciliation_version: str = "ocr-reconcile-v1"


@dataclass(frozen=True, slots=True)
class OcrModelIdentity:
    model_id: str
    engine: str
    engine_version: str
    artifact_revision: str
    bundle_sha256: str
    languages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedOcrInput:
    candidate_id: str
    image_path: Path
    width: int
    height: int
    page_number: int | None
    source_element_id: str | None
    source_geometry: SourceGeometry | None = None
    cleanup: bool = False

    @property
    def pixels(self) -> int:
        return self.width * self.height


@dataclass(frozen=True, slots=True)
class OcrRequest:
    candidate_id: str
    image_path: Path
    page_number: int | None
    source_element_id: str | None
    profile: OcrProfile


@dataclass(frozen=True, slots=True)
class OcrObservation:
    text: str
    confidence: float
    block_order: int
    candidate_id: str
    source_element_id: str | None
    page_number: int | None
    geometry: SourceGeometry | None
    model: OcrModelIdentity
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OcrCandidateResult:
    candidate_id: str
    decision: OcrDecision
    reason_codes: tuple[str, ...]
    observations: tuple[OcrObservation, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OcrPipelineResult:
    document: ParsedDocument
    candidate_results: tuple[OcrCandidateResult, ...]
    accepted_ocr_elements: tuple[DocumentElement, ...]
    processing_fingerprint: str
    pages_processed: int
    total_pixels: int
    warnings: tuple[str, ...] = ()
