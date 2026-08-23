from __future__ import annotations

import unicodedata
from uuid import uuid4

from astra_indexator.normalization import NormalizationProfile, TextNormalizationService
from astra_indexator.parser import (
    DocumentElement,
    ElementType,
    ParsedDocument,
    ParseQuality,
    ParserIdentity,
    QualityStatus,
    SourceGeometry,
)
from astra_indexator.splitter import LogicalSplitter, SplitterProfile, split_sentences


def _doc(elements: list[DocumentElement]) -> ParsedDocument:
    return ParsedDocument(
        "astra-indexator-document-v1",
        uuid4(),
        1,
        "a" * 64,
        "PDF",
        ParserIdentity("fixture", "v1", "default"),
        tuple(elements),
        (),
        ParseQuality(QualityStatus.GOOD, sum(len(e.text or "") for e in elements), 0),
    )


def _element(element_id: str, text: str | None, *, kind=ElementType.PARAGRAPH, order=0, **kwargs) -> DocumentElement:
    return DocumentElement(element_id, kind, order, text=text, **kwargs)


def _small_profile(**overrides) -> SplitterProfile:
    values = dict(
        min_chars=10,
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


def test_nfc_equivalents_normalize_identically_without_mutating_source():
    decomposed = "Cafe\u0301"
    source = _doc([_element("p1", decomposed)])
    result = TextNormalizationService().normalize(source)
    assert result.elements[0].original_text == decomposed
    assert result.elements[0].normalized_text == unicodedata.normalize("NFC", decomposed)
    assert source.elements[0].text == decomposed


def test_kazakh_letters_and_russian_yo_survive_normalization():
    text = "Ә Ғ Қ Ң Ө Ұ Ү Һ І ә ғ қ ң ө ұ ү һ і Ё ё"
    result = TextNormalizationService().normalize(_doc([_element("p1", text)]))
    assert result.elements[0].normalized_text == text


def test_prose_whitespace_is_cleaned_but_code_layout_is_preserved():
    prose = _element("p", "  Бір   жол\r\nекінші   жол  ", order=0)
    code = _element("c", "if x:\r\n    print(\"ok\")\t# keep", kind=ElementType.CODE_BLOCK, order=1)
    result = TextNormalizationService().normalize(_doc([prose, code]))
    assert result.elements[0].normalized_text == "Бір жол екінші жол"
    assert result.elements[1].normalized_text == 'if x:\n    print("ok")\t# keep'


def test_ordinary_hyphen_is_not_guessed_without_upstream_wrap_evidence():
    element = _element("p", "бизнес-\nпроцесс")
    result = TextNormalizationService().normalize(_doc([element]))
    assert result.elements[0].normalized_text == "бизнес- процесс"


def test_dehyphenation_requires_explicit_line_wrap_evidence():
    element = _element("p", "информа-\nционная система", metadata={"lineWrapHyphenEvidence": True})
    result = TextNormalizationService().normalize(_doc([element]))
    assert result.elements[0].normalized_text == "информационная система"


def test_table_cells_are_normalized_individually_and_empty_positions_survive():
    table = DocumentElement(
        "t1", ElementType.TABLE, 0, metadata={"rows": [["  A ", ""], [" B   C ", None]], "rowCount": 2, "columnCount": 2}
    )
    result = TextNormalizationService().normalize(_doc([table]))
    rows = result.elements[0].normalized_structured_data["rows"]
    assert rows == [["A", ""], ["B C", ""]]
    assert result.elements[0].metadata["rows"][0][0] == "  A "


def test_repeated_top_page_furniture_is_suppressed_only_with_page_evidence():
    elements = []
    for page in range(1, 5):
        elements.append(_element(
            f"h{page}", "Company Confidential", order=len(elements),
            geometry=SourceGeometry(page_number=page, y0=5, y1=20, page_width=600, page_height=1000, coordinate_space="points"),
        ))
        elements.append(_element(
            f"b{page}", f"Body page {page}", order=len(elements),
            geometry=SourceGeometry(page_number=page, y0=200, y1=240, page_width=600, page_height=1000, coordinate_space="points"),
        ))
    result = TextNormalizationService().normalize(_doc(elements))
    suppressed = {e.source_element_id for e in result.elements if e.suppressed_from_index}
    assert suppressed == {"h1", "h2", "h3", "h4"}
    assert result.stats.furniture_suppressed == 4


def test_one_off_edge_text_is_not_false_positive_furniture():
    elements = [
        _element("x", "Important notice", geometry=SourceGeometry(page_number=1, y0=5, y1=20, page_height=1000)),
        _element("a", "A", order=1, geometry=SourceGeometry(page_number=2, y0=200, y1=220, page_height=1000)),
        _element("b", "B", order=2, geometry=SourceGeometry(page_number=3, y0=200, y1=220, page_height=1000)),
    ]
    result = TextNormalizationService().normalize(_doc(elements))
    assert not any(e.suppressed_from_index for e in result.elements)


def test_russian_abbreviation_does_not_split_sentence():
    sentences = split_sentences("См. приложение. Далее текст.", language_hint="ru")
    assert sentences == ("См. приложение.", "Далее текст.")


def test_kazakh_abbreviations_do_not_create_false_boundaries():
    sentences = split_sentences("Құжаттар т.б. материалдар тіркелді. Келесі бөлім.", language_hint="kk")
    assert sentences == ("Құжаттар т.б. материалдар тіркелді.", "Келесі бөлім.")


def test_english_title_abbreviation_does_not_split():
    sentences = split_sentences("Dr. Smith approved it. Next item.", language_hint="en")
    assert sentences == ("Dr. Smith approved it.", "Next item.")


def test_initials_versions_ip_and_url_are_protected_from_false_sentence_boundaries():
    text = "A. Smith uses v1.2.3 at 10.20.30.40 and https://example.com/api. Done."
    sentences = split_sentences(text, language_hint="mixed")
    assert sentences == ("A. Smith uses v1.2.3 at 10.20.30.40 and https://example.com/api.", "Done.")


def test_language_switch_itself_is_not_a_boundary():
    text = "Spring Boot сервис SeaweedFS-ке файл жүктейді және returns status OK. Келесі сөйлем."
    sentences = split_sentences(text, language_hint="mixed")
    assert sentences == (
        "Spring Boot сервис SeaweedFS-ке файл жүктейді және returns status OK.",
        "Келесі сөйлем.",
    )


def test_heading_is_kept_with_body_and_parent_context_is_separate():
    source = _doc([
        _element("h1", "1. Жалпы ережелер", kind=ElementType.HEADING, order=0, level=1),
        _element("p1", "Бұл бөлім құжаттың мақсатын сипаттайды.", order=1),
    ])
    normalized = TextNormalizationService().normalize(source)
    fragments = LogicalSplitter(_small_profile()).split(normalized)
    assert len(fragments) == 1
    assert "1. Жалпы ережелер" in fragments[0].normalized_text
    assert fragments[0].source.element_ids == ("h1", "p1")


def test_suppressed_furniture_does_not_enter_fragment_text():
    elements = []
    for page in range(1, 4):
        elements.append(_element(
            f"h{page}", "HEADER", order=len(elements),
            geometry=SourceGeometry(page_number=page, y0=1, y1=10, page_height=1000),
        ))
        elements.append(_element(f"p{page}", f"Content {page}.", order=len(elements)))
    normalized = TextNormalizationService().normalize(_doc(elements))
    fragments = LogicalSplitter(_small_profile()).split(normalized)
    assert all("HEADER" not in fragment.normalized_text for fragment in fragments)


def test_oversized_paragraph_uses_sentence_or_forced_boundaries_and_never_exceeds_hard_chars():
    text = " ".join(f"Sentence {i} has useful content." for i in range(20))
    normalized = TextNormalizationService().normalize(_doc([_element("p1", text)]))
    fragments = LogicalSplitter(_small_profile(hard_max_chars=100, soft_max_chars=80, target_chars=50)).split(normalized)
    assert len(fragments) > 1
    assert all(fragment.statistics.char_count <= 100 for fragment in fragments)
    assert any(fragment.split.forced for fragment in fragments)


def test_deterministic_repeated_run_produces_identical_fragment_ids():
    source = _doc([
        _element("h", "Section", kind=ElementType.HEADING, level=1),
        _element("p", "One sentence. Another sentence.", order=1),
    ])
    normalizer = TextNormalizationService()
    splitter = LogicalSplitter(_small_profile())
    first = splitter.split(normalizer.normalize(source))
    second = splitter.split(normalizer.normalize(source))
    assert [f.fragment_id for f in first] == [f.fragment_id for f in second]
    assert [f.normalized_text for f in first] == [f.normalized_text for f in second]


def test_profile_version_changes_fragment_identity():
    source = _doc([_element("p", "Stable source text for identity.")])
    normalized = TextNormalizationService().normalize(source)
    first = LogicalSplitter(_small_profile(version="logical-v1")).split(normalized)
    second = LogicalSplitter(_small_profile(version="logical-v2")).split(normalized)
    assert first[0].fragment_id != second[0].fragment_id


def test_table_continuation_repeats_header_as_synthetic_context_not_source_text():
    rows = [["Name", "Value"]] + [[f"row-{i}", "x" * 30] for i in range(8)]
    table = DocumentElement("t", ElementType.TABLE, 0, metadata={"rows": rows})
    normalized = TextNormalizationService().normalize(_doc([table]))
    fragments = LogicalSplitter(_small_profile(hard_max_chars=90, soft_max_chars=70, target_chars=50)).split(normalized)
    assert len(fragments) > 1
    continuation = fragments[1]
    assert "TABLE HEADER" in continuation.context_prefix
    assert continuation.context_prefix not in continuation.normalized_text
    assert continuation.source.element_ids == ("t",)


def test_list_intro_is_repeated_only_as_context_for_continuation():
    elements = [
        _element("intro", "Участник обязан:", order=0),
        _element("l1", "предоставить документы;", kind=ElementType.LIST_ITEM, order=1),
        _element("l2", "пройти проверку;", kind=ElementType.LIST_ITEM, order=2),
        _element("l3", "подписать соглашение.", kind=ElementType.LIST_ITEM, order=3),
    ]
    normalized = TextNormalizationService().normalize(_doc(elements))
    fragments = LogicalSplitter(_small_profile(target_chars=25, soft_max_chars=35, hard_max_chars=55)).split(normalized)
    assert any("Участник обязан:" in fragment.context_prefix for fragment in fragments[1:])


def test_raw_text_overlap_is_rejected_in_v1_profile():
    try:
        SplitterProfile(raw_text_overlap=True).validate()
    except ValueError as exc:
        assert "raw_text_overlap" in str(exc)
    else:
        raise AssertionError("blind raw overlap must be rejected")


def test_access_zone_like_codes_and_technical_tokens_survive_normalization():
    text = "accessZoneCode=0000; next=0100; X-Request-ID abc; v1.2.3; 10.20.30.40"
    result = TextNormalizationService().normalize(_doc([_element("p", text)]))
    assert result.elements[0].normalized_text == text
