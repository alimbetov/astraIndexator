from __future__ import annotations

from uuid import uuid4

import pytest

from astra_indexator.normalization import NormalizationProfile, TextNormalizationService
from astra_indexator.parser import (
    DocumentElement,
    ElementType,
    ParsedDocument,
    ParseQuality,
    ParserIdentity,
    QualityStatus,
)
from astra_indexator.splitter import LogicalSplitter, SplitterProfile, split_sentences


def _doc(elements: list[DocumentElement], *, version: int = 1) -> ParsedDocument:
    return ParsedDocument(
        "astra-indexator-document-v1",
        uuid4(),
        version,
        "a" * 64,
        "PDF",
        ParserIdentity("fixture", "v1", "default"),
        tuple(elements),
        (),
        ParseQuality(QualityStatus.GOOD, sum(len(e.text or "") for e in elements), 0),
    )


def _element(
    element_id: str,
    text: str | None,
    *,
    kind: ElementType = ElementType.PARAGRAPH,
    order: int = 0,
    role: str | None = None,
    metadata: dict | None = None,
    level: int | None = None,
) -> DocumentElement:
    return DocumentElement(
        element_id,
        kind,
        order,
        text=text,
        role=role,
        metadata=metadata or {},
        level=level,
    )


def _profile(**overrides) -> SplitterProfile:
    values = dict(
        min_chars=1,
        target_chars=60,
        soft_max_chars=90,
        hard_max_chars=120,
        target_words=12,
        soft_max_words=24,
        hard_max_words=40,
        target_sentences=3,
        hard_max_sentences=8,
    )
    values.update(overrides)
    return SplitterProfile(**values)


def test_upstream_ocr_processing_fingerprint_changes_normalization_identity():
    source = _doc([_element("p", "same accepted OCR text")])
    normalizer = TextNormalizationService()
    first = normalizer.normalize(source, upstream_processing_fingerprint="ocr-bundle-a")
    second = normalizer.normalize(source, upstream_processing_fingerprint="ocr-bundle-b")
    assert first.processing_fingerprint != second.processing_fingerprint


def test_normalization_semantic_profile_config_changes_processing_identity_without_version_change():
    source = _doc([_element("p", "same text")])
    first = TextNormalizationService(NormalizationProfile(furniture_min_fraction=0.60)).normalize(
        source
    )
    second = TextNormalizationService(NormalizationProfile(furniture_min_fraction=0.75)).normalize(
        source
    )
    assert first.normalizer_version == second.normalizer_version
    assert first.normalizer_profile == second.normalizer_profile
    assert first.processing_fingerprint != second.processing_fingerprint


def test_ocr_multiline_text_is_not_joined_without_line_wrap_evidence():
    source = _doc([_element("ocr", "Итого:\n1 000 000 ₸\nПодпись: Иванов", role="OCR_TEXT")])
    normalized = TextNormalizationService().normalize(source)
    assert normalized.elements[0].normalized_text == "Итого:\n1 000 000 ₸\nПодпись: Иванов"
    assert normalized.stats.line_wrap_joins == 0


def test_line_join_occurs_when_upstream_supplies_explicit_wrap_evidence():
    source = _doc(
        [_element("p", "первая строка\nпродолжение", metadata={"lineWrapEvidence": True})]
    )
    normalized = TextNormalizationService().normalize(source)
    assert normalized.elements[0].normalized_text == "первая строка продолжение"
    assert normalized.stats.line_wrap_joins == 1


def test_hard_word_limit_is_a_real_postcondition():
    text = " ".join(f"word{i}" for i in range(23))
    normalized = TextNormalizationService().normalize(_doc([_element("p", text)]))
    fragments = LogicalSplitter(
        _profile(
            target_chars=1000,
            soft_max_chars=2000,
            hard_max_chars=5000,
            target_words=3,
            soft_max_words=4,
            hard_max_words=5,
            target_sentences=4,
            hard_max_sentences=10,
        )
    ).split(normalized)
    assert len(fragments) >= 5
    assert all(fragment.statistics.word_count <= 5 for fragment in fragments)


def test_hard_sentence_limit_is_a_real_postcondition():
    text = "One done. Two done. Three done. Four done. Five done."
    normalized = TextNormalizationService().normalize(_doc([_element("p", text)]))
    fragments = LogicalSplitter(
        _profile(
            target_chars=1000,
            soft_max_chars=2000,
            hard_max_chars=5000,
            target_words=100,
            soft_max_words=200,
            hard_max_words=300,
            target_sentences=1,
            hard_max_sentences=2,
        )
    ).split(normalized)
    assert len(fragments) >= 3
    assert all(fragment.statistics.sentence_count <= 2 for fragment in fragments)


def test_single_oversized_table_row_is_forced_but_never_breaks_hard_char_guard():
    long_cell = " ".join(["value"] * 30)
    table = DocumentElement("t", ElementType.TABLE, 0, metadata={"rows": [["Header"], [long_cell]]})
    normalized = TextNormalizationService().normalize(_doc([table]))
    fragments = LogicalSplitter(
        _profile(
            target_chars=20,
            soft_max_chars=30,
            hard_max_chars=40,
            target_words=20,
            soft_max_words=30,
            hard_max_words=40,
        )
    ).split(normalized)
    assert len(fragments) > 1
    assert all(fragment.statistics.char_count <= 40 for fragment in fragments)
    body = [fragment for fragment in fragments if fragment.source.table_row_from == 1]
    assert body
    assert all(fragment.source.table_row_to == 1 for fragment in body)
    assert any(fragment.split.forced for fragment in body)


def test_min_chars_consolidates_compatible_tiny_sections():
    source = _doc(
        [
            _element("h1", "A", kind=ElementType.HEADING, order=0, level=1),
            _element("p1", "short one.", order=1),
            _element("h2", "B", kind=ElementType.HEADING, order=2, level=1),
            _element("p2", "short two.", order=3),
        ]
    )
    normalized = TextNormalizationService().normalize(source)
    fragments = LogicalSplitter(
        _profile(min_chars=40, target_chars=60, soft_max_chars=100, hard_max_chars=160)
    ).split(normalized)
    assert len(fragments) == 1
    assert "A" in fragments[0].normalized_text and "B" in fragments[0].normalized_text


def test_small_warning_is_allowed_to_remain_independent():
    source = _doc(
        [
            _element("w", "Critical warning.", role="WARNING", order=0),
            _element("h", "Next section", kind=ElementType.HEADING, order=1, level=1),
            _element("p", "Body text follows.", order=2),
        ]
    )
    normalized = TextNormalizationService().normalize(source)
    fragments = LogicalSplitter(
        _profile(min_chars=40, target_chars=60, soft_max_chars=100, hard_max_chars=160)
    ).split(normalized)
    assert len(fragments) >= 2
    assert fragments[0].normalized_text == "Critical warning."
    assert "WARNING" in fragments[0].metadata["roles"]


def test_context_profile_flags_are_honored():
    source = _doc(
        [
            _element("h", "Parent", kind=ElementType.HEADING, order=0, level=1),
            _element("intro", "Участник обязан:", order=1),
            _element("l1", "первое действие;", kind=ElementType.LIST_ITEM, order=2),
            _element("l2", "второе действие;", kind=ElementType.LIST_ITEM, order=3),
            _element("l3", "третье действие.", kind=ElementType.LIST_ITEM, order=4),
        ]
    )
    normalized = TextNormalizationService().normalize(source)
    splitter = LogicalSplitter(
        _profile(
            target_chars=20,
            soft_max_chars=28,
            hard_max_chars=48,
            repeat_heading_context=False,
            repeat_parent_headings=False,
            repeat_list_intro=False,
        )
    )
    fragments = splitter.split(normalized)
    assert all("Parent" not in fragment.context_prefix for fragment in fragments)
    assert all("Участник обязан:" not in fragment.context_prefix for fragment in fragments)


def test_splitter_semantic_config_changes_fragment_identity_even_when_version_is_same():
    source = _doc([_element("p", "Stable text. Another sentence.")])
    normalized = TextNormalizationService().normalize(source)
    first = LogicalSplitter(_profile(target_chars=50)).split(normalized)
    second = LogicalSplitter(_profile(target_chars=55)).split(normalized)
    assert first[0].split.splitter_version == second[0].split.splitter_version
    assert first[0].fragment_id != second[0].fragment_id
    assert (
        first[0].metadata["splitterProfileConfigHash"]
        != second[0].metadata["splitterProfileConfigHash"]
    )


def test_terminal_capable_abbreviation_can_end_sentence_with_uppercase_lookahead():
    assert split_sentences("Используются методы и др. Далее архитектура.", language_hint="ru") == (
        "Используются методы и др.",
        "Далее архитектура.",
    )
    assert split_sentences("Use standard methods etc. Next section.", language_hint="en") == (
        "Use standard methods etc.",
        "Next section.",
    )


def test_title_and_location_prefix_abbreviations_remain_nonterminal():
    assert split_sentences("Dr. Smith approved it. Next.", language_hint="en") == (
        "Dr. Smith approved it.",
        "Next.",
    )
    assert split_sentences("г. Алматы расположен здесь. Далее.", language_hint="ru") == (
        "г. Алматы расположен здесь.",
        "Далее.",
    )


def test_unknown_language_uses_safe_generic_fallback_without_failure():
    assert split_sentences("Alpha complete. Beta complete!", language_hint="zz") == (
        "Alpha complete.",
        "Beta complete!",
    )


def test_invalid_document_version_is_rejected_at_m6_boundary():
    source = _doc([_element("p", "text")], version=0)
    with pytest.raises(ValueError, match="document_version"):
        TextNormalizationService().normalize(source)
