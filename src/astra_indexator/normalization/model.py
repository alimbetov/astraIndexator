from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from astra_indexator.parser import ElementType, SourceGeometry


@dataclass(frozen=True, slots=True)
class NormalizationProfile:
    profile_id: str = "multilingual-general-v1"
    version: str = "text-normalizer-v1"
    unicode_form: str = "NFC"
    collapse_horizontal_whitespace: bool = True
    normalize_nbsp: bool = True
    remove_soft_hyphen: bool = True
    page_furniture_suppression: bool = True
    furniture_min_pages: int = 3
    furniture_min_fraction: float = 0.60
    furniture_edge_fraction: float = 0.12

    def validate(self) -> None:
        if self.unicode_form != "NFC":
            raise ValueError("NORMALIZATION_UNSUPPORTED_UNICODE_POLICY")
        if self.furniture_min_pages < 2:
            raise ValueError("NORMALIZATION_PROFILE_INVALID:furniture_min_pages")
        if not 0.0 < self.furniture_min_fraction <= 1.0:
            raise ValueError("NORMALIZATION_PROFILE_INVALID:furniture_min_fraction")
        if not 0.0 < self.furniture_edge_fraction < 0.5:
            raise ValueError("NORMALIZATION_PROFILE_INVALID:furniture_edge_fraction")


@dataclass(frozen=True, slots=True)
class NormalizationStats:
    elements_input: int = 0
    elements_output: int = 0
    elements_suppressed: int = 0
    chars_before: int = 0
    chars_after: int = 0
    whitespace_runs_collapsed: int = 0
    line_wrap_joins: int = 0
    dehyphenations_applied: int = 0
    furniture_suppressed: int = 0
    control_chars_removed: int = 0


@dataclass(frozen=True, slots=True)
class NormalizedElement:
    source_element_id: str
    type: ElementType
    order_index: int
    original_text: str | None
    normalized_text: str | None
    parent_element_id: str | None = None
    level: int | None = None
    geometry: SourceGeometry | None = None
    section_path: tuple[str, ...] = ()
    source_locator: dict[str, Any] = field(default_factory=dict)
    style_hints: dict[str, Any] = field(default_factory=dict)
    language_hint: str | None = None
    role: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    normalized_structured_data: dict[str, Any] = field(default_factory=dict)
    suppressed_from_index: bool = False
    suppression_reason: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NormalizedDocument:
    schema_version: str
    document_id: UUID
    document_version: int
    source_sha256: str
    detected_format: str
    normalizer_profile: str
    normalizer_version: str
    processing_fingerprint: str
    elements: tuple[NormalizedElement, ...]
    stats: NormalizationStats
    warnings: tuple[str, ...] = ()
