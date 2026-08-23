from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

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

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def words(self) -> int:
        return len(_WORD.findall(self.text))


class LogicalSplitter:
    def __init__(self, profile: SplitterProfile | None = None):
        self.profile = profile or SplitterProfile()
        self.profile.validate()

    def split(self, document: NormalizedDocument) -> tuple[LogicalFragment, ...]:
        units = self._build_units(document)
        fragments: list[LogicalFragment] = []
        buffer: list[_Unit] = []

        def flush(reason: str, forced: bool = False) -> None:
            nonlocal buffer
            if not buffer:
                return
            fragments.append(self._make_fragment(document, buffer, len(fragments), reason, forced))
            buffer = []

        for unit in units:
            if not unit.text:
                continue
            if unit.element_type == ElementType.HEADING and buffer:
                # Strong structural boundary: prefer ending prior coherent content.
                flush("SECTION_BOUNDARY")

            if not buffer:
                buffer.append(unit)
                if self._exceeds_hard(buffer):
                    flush("FORCED_SPLIT", True)
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
        return tuple(fragments)

    def _build_units(self, document: NormalizedDocument) -> list[_Unit]:
        units: list[_Unit] = []
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
                units.extend(self._table_units(element, tuple(hierarchy)))
                list_intro = None
                continue

            if not text:
                continue

            if element.type == ElementType.PARAGRAPH and text.endswith(":"):
                list_intro = text
            elif element.type not in {ElementType.LIST, ElementType.LIST_ITEM}:
                list_intro = None

            context = list_intro if element.type in {ElementType.LIST, ElementType.LIST_ITEM} and list_intro else ""
            units.extend(self._text_units(element, text, tuple(hierarchy), context))
        return units

    def _text_units(self, element: NormalizedElement, text: str, hierarchy: tuple[str, ...], context: str) -> list[_Unit]:
        page = element.geometry.page_number if element.geometry else None
        sentences = split_sentences(text, language_hint=element.language_hint)
        if not sentences:
            sentences = (text,)

        chunks: list[str] = []
        current: list[str] = []
        for sentence in sentences:
            if len(sentence) > self.profile.hard_max_chars:
                if current:
                    chunks.append(" ".join(current))
                    current = []
                chunks.extend(self._force_text_chunks(sentence))
                continue
            candidate = " ".join([*current, sentence]).strip()
            if current and len(candidate) > self.profile.hard_max_chars:
                chunks.append(" ".join(current))
                current = [sentence]
            else:
                current.append(sentence)
        if current:
            chunks.append(" ".join(current))

        result: list[_Unit] = []
        for index, chunk in enumerate(chunks):
            forced = len(chunks) > 1
            result.append(_Unit(
                text=chunk,
                element_ids=(element.source_element_id,),
                element_type=element.type,
                hierarchy=hierarchy,
                page_from=page,
                page_to=page,
                language_hint=element.language_hint,
                sentence_count=len(split_sentences(chunk, language_hint=element.language_hint)) or 1,
                continuation_index=index,
                synthetic_context=context if index > 0 else "",
                forced=forced,
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
            # This is a derived index representation only; canonical structure remains rows/cells.
            return "\t".join(str(cell) for cell in row)

        header = row_text(rows[0])
        groups: list[list[str]] = []
        current: list[str] = []
        for row in rows:
            rendered = row_text(row)
            trial = "\n".join([*current, rendered])
            if current and len(trial) > self.profile.hard_max_chars:
                groups.append(current)
                current = [rendered]
            else:
                current.append(rendered)
        if current:
            groups.append(current)

        page = element.geometry.page_number if element.geometry else None
        return [
            _Unit(
                text="\n".join(group),
                element_ids=(element.source_element_id,),
                element_type=ElementType.TABLE,
                hierarchy=hierarchy,
                page_from=page,
                page_to=page,
                language_hint=element.language_hint,
                sentence_count=0,
                continuation_index=index,
                synthetic_context=(f"TABLE HEADER\n{header}" if index > 0 and self.profile.repeat_table_header else ""),
                forced=len(groups) > 1,
            )
            for index, group in enumerate(groups)
        ]

    def _force_text_chunks(self, text: str) -> list[str]:
        words = text.split()
        if not words:
            return [text[: self.profile.hard_max_chars]]
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
            if current and len(trial) > self.profile.hard_max_chars:
                chunks.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            chunks.append(" ".join(current))
        return chunks

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
        contexts = [unit.synthetic_context for unit in units if unit.synthetic_context]
        begins_with_heading = bool(units and units[0].element_type == ElementType.HEADING)
        hierarchy_context = hierarchy[:-1] if begins_with_heading and hierarchy else hierarchy
        context_parts = [*hierarchy_context, *contexts]
        context_prefix = "\n".join(dict.fromkeys(part for part in context_parts if part))

        languages = self._languages(units, normalized_text)
        primary = languages[0] if languages else "und"
        fragment_type = self._fragment_type(units)
        content_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        continuation = max((unit.continuation_index for unit in units), default=0)
        identity_payload = "\x1f".join([
            str(document.document_id),
            str(document.document_version),
            ",".join(source_ids),
            content_hash,
            document.normalizer_version,
            document.normalizer_profile,
            self.profile.version,
            self.profile.profile_id,
            fragment_type.value,
            str(continuation),
        ])
        fragment_id = hashlib.sha256(identity_payload.encode("utf-8")).hexdigest()
        sentence_count = sum(unit.sentence_count for unit in units)
        final_forced = forced or any(unit.forced for unit in units)
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
        if any(char in _KK for char in text):
            detected.append("kk")
        if _CYR.search(text) and "ru" not in detected:
            detected.append("ru")
        if _LAT.search(text):
            detected.append("en")
        return tuple(detected) or ("und",)
