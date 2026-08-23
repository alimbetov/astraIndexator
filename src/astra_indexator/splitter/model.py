from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID


class FragmentType(StrEnum):
    SECTION = "SECTION"
    PARAGRAPH = "PARAGRAPH"
    LIST = "LIST"
    TABLE = "TABLE"
    CODE = "CODE"
    OCR = "OCR"
    OTHER = "OTHER"


@dataclass(frozen=True, slots=True)
class SplitterProfile:
    profile_id: str = "multilingual-general-v1"
    version: str = "logical-v1"
    sentence_boundary_backend: str = "unicode"
    min_chars: int = 800
    target_chars: int = 5000
    soft_max_chars: int = 8000
    hard_max_chars: int = 12000
    target_words: int = 700
    soft_max_words: int = 1200
    hard_max_words: int = 1800
    target_sentences: int = 25
    hard_max_sentences: int = 80
    raw_text_overlap: bool = False
    repeat_heading_context: bool = True
    repeat_parent_headings: bool = True
    repeat_table_header: bool = True
    repeat_list_intro: bool = True

    def validate(self) -> None:
        if self.sentence_boundary_backend not in {"unicode", "icu"}:
            raise ValueError("SPLITTER_PROFILE_INVALID:sentence_boundary_backend")
        if not (0 < self.min_chars <= self.target_chars <= self.soft_max_chars <= self.hard_max_chars):
            raise ValueError("SPLITTER_PROFILE_INVALID:char_limits")
        if not (0 < self.target_words <= self.soft_max_words <= self.hard_max_words):
            raise ValueError("SPLITTER_PROFILE_INVALID:word_limits")
        if not (0 < self.target_sentences <= self.hard_max_sentences):
            raise ValueError("SPLITTER_PROFILE_INVALID:sentence_limits")
        if self.raw_text_overlap:
            raise ValueError("SPLITTER_PROFILE_INVALID:raw_text_overlap_not_supported_v1")


@dataclass(frozen=True, slots=True)
class FragmentStatistics:
    char_count: int
    word_count: int
    sentence_count: int


@dataclass(frozen=True, slots=True)
class SplitDecision:
    reason: str
    forced: bool
    profile: str
    splitter_version: str
    continuation_index: int = 0


@dataclass(frozen=True, slots=True)
class FragmentSource:
    element_ids: tuple[str, ...]
    element_from: str | None
    element_to: str | None
    page_from: int | None = None
    page_to: int | None = None
    table_row_from: int | None = None
    table_row_to: int | None = None


@dataclass(frozen=True, slots=True)
class LogicalFragment:
    fragment_id: str
    document_id: UUID
    document_version: int
    sequence: int
    fragment_type: FragmentType
    normalized_text: str
    context_prefix: str
    hierarchy: tuple[str, ...]
    source: FragmentSource
    statistics: FragmentStatistics
    split: SplitDecision
    primary_language: str = "und"
    languages: tuple[str, ...] = ()
    mixed_language: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
