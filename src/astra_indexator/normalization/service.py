from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from dataclasses import replace
from typing import Iterable

from astra_indexator.parser import DocumentElement, ElementType, ParsedDocument, SourceGeometry

from .model import NormalizationProfile, NormalizationStats, NormalizedDocument, NormalizedElement


_HORIZONTAL_WS = re.compile(r"[\t\v\f ]+")
_C0 = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class TextNormalizationService:
    def __init__(self, profile: NormalizationProfile | None = None):
        self.profile = profile or NormalizationProfile()
        self.profile.validate()

    def normalize(self, document: ParsedDocument) -> NormalizedDocument:
        normalized_pre: list[NormalizedElement] = []
        counters = Counter()
        warnings: list[str] = []

        for element in document.elements:
            normalized, local = self._normalize_element(element)
            normalized_pre.append(normalized)
            counters.update(local)
            warnings.extend(normalized.warnings)

        furniture_ids = self._classify_page_furniture(normalized_pre) if self.profile.page_furniture_suppression else set()
        normalized_final: list[NormalizedElement] = []
        for element in normalized_pre:
            if element.source_element_id in furniture_ids:
                normalized_final.append(replace(
                    element,
                    suppressed_from_index=True,
                    suppression_reason="REPEATED_PAGE_FURNITURE",
                ))
                counters["elements_suppressed"] += 1
                counters["furniture_suppressed"] += 1
            else:
                normalized_final.append(element)

        fingerprint_payload = "\x1f".join([
            document.source_sha256,
            document.parser.name,
            document.parser.version,
            document.parser.profile,
            self.profile.profile_id,
            self.profile.version,
        ])
        fingerprint = hashlib.sha256(fingerprint_payload.encode("utf-8")).hexdigest()
        stats = NormalizationStats(
            elements_input=len(document.elements),
            elements_output=len(normalized_final),
            elements_suppressed=counters["elements_suppressed"],
            chars_before=counters["chars_before"],
            chars_after=counters["chars_after"],
            whitespace_runs_collapsed=counters["whitespace_runs_collapsed"],
            line_wrap_joins=counters["line_wrap_joins"],
            dehyphenations_applied=counters["dehyphenations_applied"],
            furniture_suppressed=counters["furniture_suppressed"],
            control_chars_removed=counters["control_chars_removed"],
        )
        return NormalizedDocument(
            schema_version="astra-indexator-normalized-document-v1",
            document_id=document.document_id,
            document_version=document.document_version,
            source_sha256=document.source_sha256,
            detected_format=document.detected_format,
            normalizer_profile=self.profile.profile_id,
            normalizer_version=self.profile.version,
            processing_fingerprint=fingerprint,
            elements=tuple(normalized_final),
            stats=stats,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    def _normalize_element(self, element: DocumentElement) -> tuple[NormalizedElement, Counter]:
        counters = Counter()
        original = element.text
        normalized_text = original
        structured: dict = {}
        element_warnings: list[str] = []

        if original is not None:
            counters["chars_before"] += len(original)
            normalized_text, text_counts = self._normalize_text(
                original,
                preserve_layout=element.type == ElementType.CODE_BLOCK,
                allow_dehyphenation=bool(element.metadata.get("lineWrapHyphenEvidence")),
            )
            counters.update(text_counts)
            counters["chars_after"] += len(normalized_text)

        if element.type == ElementType.TABLE:
            rows = element.metadata.get("rows")
            if isinstance(rows, list):
                normalized_rows: list[list[str]] = []
                for row in rows:
                    if not isinstance(row, list):
                        element_warnings.append("TABLE_ROW_STRUCTURE_INVALID")
                        continue
                    normalized_row: list[str] = []
                    for cell in row:
                        value = "" if cell is None else str(cell)
                        normalized_cell, local = self._normalize_text(value, preserve_layout=False, allow_dehyphenation=False)
                        counters.update(local)
                        normalized_row.append(normalized_cell)
                    normalized_rows.append(normalized_row)
                structured = {
                    "rows": normalized_rows,
                    "rowCount": len(normalized_rows),
                    "columnCount": max((len(row) for row in normalized_rows), default=0),
                }

        normalized = NormalizedElement(
            source_element_id=element.element_id,
            type=element.type,
            order_index=element.order_index,
            original_text=original,
            normalized_text=normalized_text,
            parent_element_id=element.parent_element_id,
            level=element.level,
            geometry=element.geometry,
            section_path=element.section_path,
            source_locator=dict(element.source_locator),
            style_hints=dict(element.style_hints),
            language_hint=element.language_hint,
            role=element.role,
            metadata=dict(element.metadata),
            normalized_structured_data=structured,
            warnings=tuple(element_warnings),
        )
        return normalized, counters

    def _normalize_text(self, text: str, *, preserve_layout: bool, allow_dehyphenation: bool) -> tuple[str, Counter]:
        counters = Counter()
        value = text.replace("\r\n", "\n").replace("\r", "\n")
        value = unicodedata.normalize(self.profile.unicode_form, value)
        if self.profile.normalize_nbsp:
            value = value.replace("\u00a0", " ")
        if self.profile.remove_soft_hyphen:
            value = value.replace("\u00ad", "")
        before_controls = len(value)
        value = _C0.sub("", value)
        counters["control_chars_removed"] += before_controls - len(value)

        if preserve_layout:
            return value, counters

        if allow_dehyphenation:
            # Ordinary hyphens are removed only when upstream parser/OCR supplied explicit
            # line-wrap evidence. This prevents guessing on lexical forms like бизнес-процесс.
            updated, count = re.subn(r"(?<=\w)-\n(?=\w)", "", value)
            value = updated
            counters["dehyphenations_applied"] += count

        lines = value.split("\n")
        normalized_lines: list[str] = []
        for line in lines:
            if self.profile.collapse_horizontal_whitespace:
                collapsed, count = _HORIZONTAL_WS.subn(" ", line)
                counters["whitespace_runs_collapsed"] += count
                line = collapsed
            normalized_lines.append(line.strip())

        nonempty = [line for line in normalized_lines if line]
        if len(nonempty) > 1:
            counters["line_wrap_joins"] += len(nonempty) - 1
        value = " ".join(nonempty).strip()
        return value, counters

    def _classify_page_furniture(self, elements: Iterable[NormalizedElement]) -> set[str]:
        page_elements: list[NormalizedElement] = []
        pages: set[int] = set()
        for element in elements:
            geometry = element.geometry
            if (
                not element.normalized_text
                or element.type == ElementType.HEADING
                or geometry is None
                or geometry.page_number is None
                or geometry.y0 is None
                or geometry.y1 is None
                or geometry.page_height in (None, 0)
            ):
                continue
            pages.add(geometry.page_number)
            page_elements.append(element)
        if len(pages) < self.profile.furniture_min_pages:
            return set()

        occurrences: dict[str, list[NormalizedElement]] = {}
        for element in page_elements:
            key = re.sub(r"\s+", " ", element.normalized_text.casefold()).strip()
            if not key:
                continue
            g = element.geometry
            assert g is not None and g.page_height is not None and g.y0 is not None and g.y1 is not None
            center = ((g.y0 + g.y1) / 2.0) / float(g.page_height)
            near_edge = center <= self.profile.furniture_edge_fraction or center >= (1.0 - self.profile.furniture_edge_fraction)
            if near_edge:
                occurrences.setdefault(key, []).append(element)

        required = max(self.profile.furniture_min_pages, math.ceil(len(pages) * self.profile.furniture_min_fraction))
        suppressed: set[str] = set()
        for group in occurrences.values():
            distinct_pages = {e.geometry.page_number for e in group if e.geometry is not None}
            if len(distinct_pages) >= required:
                suppressed.update(e.source_element_id for e in group)
        return suppressed
