from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Iterable

from astra_indexator.normalization import NormalizedDocument, NormalizedElement
from astra_indexator.parser import ElementType

from .model import (
    FragmentSource,
    FragmentStatistics,
    FragmentType,
    LogicalFragment,
    SplitDecision,
    SplitterProfile,
)
from .sentence import split_sentences


_WORD = re.compile(r"\S+", re.UNICODE)
_KK = set("ӘәҒғҚқҢңӨөҰұҮүҺһІі")
_CYR = re.compile(r"[А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]")
_LAT = re.compile(r"[A-Za-z]")
_SMALL_FRAGMENT_EXCEPTION_ROLES = {
    "FAQ", "WARNING", "LEGAL_DEFINITION", "DEFINITION", "NOTICE", "ALERT",
}


@dataclass(frozen=True, slots=True)
class _Unit:
    text: str
    element_ids: tuple[str, ...]
    element_type: ElementType
    hierarchy: tuple[str, ...]
    page_from: int | None
    page_to: int | None
    language_hint: str | None
    sentence_count: int
    continuation_index: int = 0
    synthetic_context: str = ""
    forced: bool = False
    role: str | None = None
    table_row_from: int | None = None
    table_row_to: int | None = None

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def words(self) -> int:
        return len(_WORD.findall(self.text))


def _canonical_profile_hash(profile: SplitterProfile) -> str:
    payload = json.dumps(asdict(profile), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LogicalSplitter:
    def __init__(self, profile: SplitterProfile | None = None):
        self.profile = profile or SplitterProfile()
        self.profile.validate()
        self.profile_config_hash = _canonical_profile_hash(self.profile)

    def split(self, document: NormalizedDocument) -> tuple[LogicalFragment, ...]:
        groups: list[tuple[list[_Unit], str, bool]] = []
        buffer: list[_Unit] = []

        def flush(reason: str, forced: bool = False) -> None:
            nonlocal buffer
            if not buffer:
                return
            if self._exceeds_hard(buffer):
                raise RuntimeError("SPLITTER_HARD_GUARD_VIOLATION")
            groups.append((buffer, reason, forced))
            buffer = []

        for unit in self._iter_units(document):
            if not unit.text:
                continue
            if self._exceeds_hard([unit]):
                raise RuntimeError("SPLITTER_UNIT_EXCEEDS_HARD_GUARD")

            if unit.element_type == ElementType.HEADING and buffer:
                if self._size(buffer)[0] >= self.profile.min_chars or self._small_exception(buffer):
                    flush("SECTION_BOUNDARY")

            if not buffer:
                buffer.append(unit)
                continue

            trial = [*buffer, unit]
            if self._exceeds_hard(trial):
                flush("HARD_MAX", True)
                buffer.append(unit)
            elif self._exceeds_soft(trial) and self._at_safe_boundary(buffer[-1], unit):
                flush("SOFT_MAX_SAFE_BOUNDARY")
                buffer.append(unit)
            elif self._reaches_target(buffer) and self._at_strong_boundary(buffer[-1], unit):
                flush("STRUCTURAL_BOUNDARY")
                buffer.append(unit)
            else:
                buffer.append(unit)

        flush("DOCUMENT_END")
        groups = self._consolidate_small_groups(groups)
        fragments = [
            self._make_fragment(document, units, index, reason, forced)
            for index, (units, reason, forced) in enumerate(groups)
        ]
        for fragment in fragments:
            if (
                fragment.statistics.char_count > self.profile.hard_max_chars
                or fragment.statistics.word_count > self.profile.hard_max_words
                or fragment.statistics.sentence_count > self.profile.hard_max_sentences
            ):
                raise RuntimeError("SPLITTER_FRAGMENT_EXCEEDS_HARD_GUARD")
        return tuple(fragments)

    def _iter_units(self, document: NormalizedDocument) -> Iterable[_Unit]:
        hierarchy: list[str] = []
        list_intro: str | None = None

        for element in sorted(document.elements, key=lambda item: item.order_index):
            if element.suppressed_from_index:
                continue
            text = (element.normalized_text or "").strip()

            if element.type == ElementType.HEADING and text:
                level = max(1, element.level or 1)
                hierarchy[:] = hierarchy[: level - 1]
                hierarchy.append(text)

            if element.type == ElementType.TABLE:
                yield from self._table_units(element, tuple(hierarchy))
                list_intro = None
                continue

            if not text:
                continue

            if element.type == ElementType.PARAGRAPH and text.endswith(":"):
                list_intro = text
            elif element.type not in {ElementType.LIST, ElementType.LIST_ITEM}:
                list_intro = None

            context = ""
            if self.profile.repeat_list_intro and element.type in {ElementType.LIST, ElementType.LIST_ITEM} and list_intro:
                context = list_intro
            yield from self._text_units(element, text, tuple(hierarchy), context)

    def _text_units(self, element: NormalizedElement, text: str, hierarchy: tuple[str, ...], context: str) -> list[_Unit]:
        page = element.geometry.page_number if element.geometry else None
        sentences = split_sentences(text, language_hint=element.language_hint) or (text,)

        chunks: list[tuple[str, int, bool]] = []
        current: list[str] = []
        current_sentence_count = 0

        def emit_current() -> None:
            nonlocal current, current_sentence_count
            if current:
                chunks.append((" ".join(current).strip(), current_sentence_count, False))
                current = []
                current_sentence_count = 0

        for sentence in sentences:
            sentence_words = len(_WORD.findall(sentence))
            if len(sentence) > self.profile.hard_max_chars or sentence_words > self.profile.hard_max_words:
                emit_current()
                for forced_chunk in self._force_text_chunks(sentence):
                    chunks.append((forced_chunk, 1, True))
                continue

            candidate = " ".join([*current, sentence]).strip()
            candidate_words = len(_WORD.findall(candidate))
            candidate_sentences = current_sentence_count + 1
            if current and (
                len(candidate) > self.profile.hard_max_chars
                or candidate_words > self.profile.hard_max_words
                or candidate_sentences > self.profile.hard_max_sentences
            ):
                emit_current()
                current = [sentence]
                current_sentence_count = 1
            else:
                current.append(sentence)
                current_sentence_count = candidate_sentences
        emit_current()

        result: list[_Unit] = []
        for index, (chunk, sentence_count, forced) in enumerate(chunks):
            result.append(_Unit(
                text=chunk,
                element_ids=(element.source_element_id,),
                element_type=element.type,
                hierarchy=hierarchy,
                page_from=page,
                page_to=page,
                language_hint=element.language_hint,
                sentence_count=sentence_count,
                continuation_index=index,
                synthetic_context=context,
                forced=forced or len(chunks) > 1,
                role=element.role,
            ))
        return result

    def _table_units(self, element: NormalizedElement, hierarchy: tuple[str, ...]) -> list[_Unit]:
        rows = element.normalized_structured_data.get("rows")
        if not isinstance(rows, list) or not rows:
            text = (element.normalized_text or "").strip()
            if not text:
                return []
            return self._text_units(element, text, hierarchy, "")

        def row_text(row: list[str]) -> str:
            return "\t".join(str(cell) for cell in row)

        header = row_text(rows[0])
        page = element.geometry.page_number if element.geometry else None
        units: list[_Unit] = []
        group_rows: list[str] = []
        group_start = 0

        def emit_group(end_index: int) -> None:
            nonlocal group_rows, group_start
            if not group_rows:
                return
            index = len(units)
            units.append(_Unit(
                text="\n".join(group_rows),
                element_ids=(element.source_element_id,),
                element_type=ElementType.TABLE,
                hierarchy=hierarchy,
                page_from=page,
                page_to=page,
                language_hint=element.language_hint,
                sentence_count=0,
                continuation_index=index,
                synthetic_context=(f"TABLE HEADER\n{header}" if index > 0 and self.profile.repeat_table_header else ""),
                forced=index > 0,
                role=element.role,
                table_row_from=group_start,
                table_row_to=end_index,
            ))
            group_rows = []

        for row_index, row in enumerate(rows):
            rendered = row_text(row)
            rendered_words = len(_WORD.findall(rendered))
            if len(rendered) > self.profile.hard_max_chars or rendered_words > self.profile.hard_max_words:
                emit_group(row_index - 1)
                forced_chunks = self._force_text_chunks(rendered)
                for chunk in forced_chunks:
                    index = len(units)
                    units.append(_Unit(
                        text=chunk,
                        element_ids=(element.source_element_id,),
                        element_type=ElementType.TABLE,
                        hierarchy=hierarchy,
                        page_from=page,
                        page_to=page,
                        language_hint=element.language_hint,
                        sentence_count=0,
                        continuation_index=index,
                        synthetic_context=(f"TABLE HEADER\n{header}" if index > 0 and self.profile.repeat_table_header else ""),
                        forced=True,
                        role=element.role,
                        table_row_from=row_index,
                        table_row_to=row_index,
                    ))
                group_start = row_index + 1
                continue

            trial = "\n".join([*group_rows, rendered])
            if group_rows and (
                len(trial) > self.profile.hard_max_chars
                or len(_WORD.findall(trial)) > self.profile.hard_max_words
            ):
                emit_group(row_index - 1)
                group_start = row_index
                group_rows = [rendered]
            else:
                if not group_rows:
                    group_start = row_index
                group_rows.append(rendered)
        emit_group(len(rows) - 1)
        return units

    def _force_text_chunks(self, text: str) -> list[str]:
        words = text.split()
        if not words:
            return [text[i:i + self.profile.hard_max_chars] for i in range(0, len(text), self.profile.hard_max_chars)] or [""]
        chunks: list[str] = []
        current: list[str] = []
        for word in words:
            if len(word) > self.profile.hard_max_chars:
                if current:
                    chunks.append(" ".join(current))
                    current = []
                chunks.extend(word[i:i + self.profile.hard_max_chars] for i in range(0, len(word), self.profile.hard_max_chars))
                continue
            trial = " ".join([*current, word])
            if current and (len(trial) > self.profile.hard_max_chars or len(current) + 1 > self.profile.hard_max_words):
                chunks.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            chunks.append(" ".join(current))
        return chunks

    def _consolidate_small_groups(
        self,
        groups: list[tuple[list[_Unit], str, bool]],
    ) -> list[tuple[list[_Unit], str, bool]]:
        if len(groups) < 2:
            return groups
        out: list[tuple[list[_Unit], str, bool]] = []
        index = 0
        while index < len(groups):
            units, reason, forced = groups[index]
            if (
                self._size(units)[0] < self.profile.min_chars
                and not self._small_exception(units)
                and index + 1 < len(groups)
            ):
                next_units, next_reason, next_forced = groups[index + 1]
                combined = [*units, *next_units]
                if (
                    not self._small_exception(next_units)
                    and self._compatible_small_merge(units, next_units)
                    and not self._exceeds_hard(combined)
                ):
                    out.append((combined, "SMALL_FRAGMENT_MERGE", forced or next_forced))
                    index += 2
                    continue
            if out and self._size(units)[0] < self.profile.min_chars and not self._small_exception(units):
                prev_units, prev_reason, prev_forced = out[-1]
                combined = [*prev_units, *units]
                if (
                    not self._small_exception(prev_units)
                    and self._compatible_small_merge(prev_units, units)
                    and not self._exceeds_hard(combined)
                ):
                    out[-1] = (combined, "SMALL_FRAGMENT_MERGE", prev_forced or forced)
                    index += 1
                    continue
            out.append((units, reason, forced))
            index += 1
        return out

    @staticmethod
    def _small_exception(units: list[_Unit]) -> bool:
        return any((unit.role or "").upper() in _SMALL_FRAGMENT_EXCEPTION_ROLES for unit in units) or any(
            unit.element_type in {ElementType.TABLE, ElementType.CODE_BLOCK} for unit in units
        )

    @staticmethod
    def _compatible_small_merge(left: list[_Unit], right: list[_Unit]) -> bool:
        if not left or not right:
            return False
        blocked = {ElementType.TABLE, ElementType.CODE_BLOCK}
        if any(unit.element_type in blocked for unit in [*left, *right]):
            return False
        left_h = left[-1].hierarchy
        right_h = right[0].hierarchy
        left_parent = left_h[:-1] if left_h else ()
        right_parent = right_h[:-1] if right_h else ()
        return left_parent == right_parent

    def _reaches_target(self, units: list[_Unit]) -> bool:
        chars, words, sentences = self._size(units)
        return chars >= self.profile.target_chars or words >= self.profile.target_words or sentences >= self.profile.target_sentences

    def _exceeds_soft(self, units: list[_Unit]) -> bool:
        chars, words, _ = self._size(units)
        return chars > self.profile.soft_max_chars or words > self.profile.soft_max_words

    def _exceeds_hard(self, units: list[_Unit]) -> bool:
        chars, words, sentences = self._size(units)
        return (
            chars > self.profile.hard_max_chars
            or words > self.profile.hard_max_words
            or sentences > self.profile.hard_max_sentences
        )

    @staticmethod
    def _size(units: list[_Unit]) -> tuple[int, int, int]:
        text = "\n".join(unit.text for unit in units)
        return len(text), len(_WORD.findall(text)), sum(unit.sentence_count for unit in units)

    @staticmethod
    def _at_safe_boundary(previous: _Unit, current: _Unit) -> bool:
        if previous.element_type == ElementType.PARAGRAPH and previous.text.endswith(":") and current.element_type in {
            ElementType.LIST, ElementType.LIST_ITEM
        }:
            return False
        return previous.element_ids != current.element_ids or previous.element_type in {
            ElementType.PARAGRAPH, ElementType.LIST_ITEM, ElementType.TABLE, ElementType.CODE_BLOCK
        }

    @staticmethod
    def _at_strong_boundary(previous: _Unit, current: _Unit) -> bool:
        return current.element_type == ElementType.HEADING or previous.hierarchy != current.hierarchy

    def _make_fragment(self, document: NormalizedDocument, units: list[_Unit], sequence: int, reason: str, forced: bool) -> LogicalFragment:
        normalized_text = "\n".join(unit.text for unit in units).strip()
        hierarchy = units[-1].hierarchy if units else ()
        source_ids: list[str] = []
        for unit in units:
            for element_id in unit.element_ids:
                if element_id not in source_ids:
                    source_ids.append(element_id)
        pages = [page for unit in units for page in (unit.page_from, unit.page_to) if page is not None]
        contexts = [unit.synthetic_context for unit in units if unit.synthetic_context and unit.synthetic_context not in normalized_text]
        begins_with_heading = bool(units and units[0].element_type == ElementType.HEADING)

        hierarchy_context: list[str] = []
        if self.profile.repeat_heading_context and hierarchy:
            candidate = hierarchy[:-1] if begins_with_heading else hierarchy
            if self.profile.repeat_parent_headings:
                hierarchy_context = list(candidate)
            elif candidate:
                hierarchy_context = [candidate[-1]]
        context_parts = [*hierarchy_context, *contexts]
        context_prefix = "\n".join(dict.fromkeys(part for part in context_parts if part))

        languages = self._languages(units, normalized_text)
        primary = languages[0] if languages else "und"
        fragment_type = self._fragment_type(units)
        content_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        continuation = max((unit.continuation_index for unit in units), default=0)
        table_rows_from = [u.table_row_from for u in units if u.table_row_from is not None]
        table_rows_to = [u.table_row_to for u in units if u.table_row_to is not None]
        identity_payload = "\x1f".join([
            str(document.document_id),
            str(document.document_version),
            ",".join(source_ids),
            content_hash,
            document.processing_fingerprint,
            document.normalizer_version,
            document.normalizer_profile,
            self.profile.version,
            self.profile.profile_id,
            self.profile_config_hash,
            fragment_type.value,
            str(continuation),
            str(min(table_rows_from) if table_rows_from else ""),
            str(max(table_rows_to) if table_rows_to else ""),
        ])
        fragment_id = hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()
        sentence_count = sum(unit.sentence_count for unit in units)
        final_forced = forced or any(unit.forced for unit in units)
        roles = tuple(dict.fromkeys(unit.role for unit in units if unit.role))
        return LogicalFragment(
            fragment_id=fragment_id,
            document_id=document.document_id,
            document_version=document.document_version,
            sequence=sequence,
            fragment_type=fragment_type,
            normalized_text=normalized_text,
            context_prefix=context_prefix,
            hierarchy=hierarchy,
            source=FragmentSource(
                element_ids=tuple(source_ids),
                element_from=source_ids[0] if source_ids else None,
                element_to=source_ids[-1] if source_ids else None,
                page_from=min(pages) if pages else None,
                page_to=max(pages) if pages else None,
                table_row_from=min(table_rows_from) if table_rows_from else None,
                table_row_to=max(table_rows_to) if table_rows_to else None,
            ),
            statistics=FragmentStatistics(
                char_count=len(normalized_text),
                word_count=len(_WORD.findall(normalized_text)),
                sentence_count=sentence_count,
            ),
            split=SplitDecision(
                reason="FORCED_SPLIT" if final_forced else reason,
                forced=final_forced,
                profile=self.profile.profile_id,
                splitter_version=self.profile.version,
                continuation_index=continuation,
            ),
            primary_language=primary,
            languages=languages,
            mixed_language=len(languages) > 1,
            metadata={"splitterProfileConfigHash": self.profile_config_hash, "roles": roles},
        )

    @staticmethod
    def _fragment_type(units: list[_Unit]) -> FragmentType:
        types = {unit.element_type for unit in units}
        if types <= {ElementType.TABLE}:
            return FragmentType.TABLE
        if types <= {ElementType.LIST, ElementType.LIST_ITEM}:
            return FragmentType.LIST
        if types <= {ElementType.CODE_BLOCK}:
            return FragmentType.CODE
        if any(unit.element_type == ElementType.HEADING for unit in units):
            return FragmentType.SECTION
        if types <= {ElementType.PARAGRAPH}:
            return FragmentType.PARAGRAPH
        return FragmentType.OTHER

    @staticmethod
    def _languages(units: list[_Unit], text: str) -> tuple[str, ...]:
        hints: list[str] = []
        for unit in units:
            if unit.language_hint:
                primary = unit.language_hint.lower().split("-", 1)[0]
                if primary in {"ru", "kk", "en"} and primary not in hints:
                    hints.append(primary)
        if hints:
            return tuple(hints)

        detected: list[str] = []
        has_kazakh_specific = any(char in _KK for char in text)
        if has_kazakh_specific:
            detected.append("kk")
        elif _CYR.search(text):
            detected.append("ru")
        if _LAT.search(text):
            detected.append("en")
        return tuple(detected) or ("und",)
