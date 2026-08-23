from __future__ import annotations

import pytest

from astra_indexator.splitter import SplitterProfile, split_sentences
from astra_indexator.splitter.sentence import count_words, grapheme_clusters


def test_cjk_sentence_terminals_do_not_require_ascii_space():
    assert split_sentences("第一句。第二句！第三句？", language_hint="zh") == (
        "第一句。",
        "第二句！",
        "第三句？",
    )
    assert split_sentences("最初の文。次の文！最後？", language_hint="ja") == (
        "最初の文。",
        "次の文！",
        "最後？",
    )


def test_arabic_question_mark_and_full_stop_are_supported_without_forcing_latin_rules():
    assert split_sentences("هذه جملة؟هذه ثانية.", language_hint="ar") == (
        "هذه جملة؟",
        "هذه ثانية.",
    )


def test_indic_danda_and_double_danda_are_sentence_terminals():
    assert split_sentences("पहला वाक्य।दूसरा वाक्य॥", language_hint="hi") == (
        "पहला वाक्य।",
        "दूसरा वाक्य॥",
    )


def test_armenian_and_myanmar_native_sentence_punctuation():
    assert split_sentences("Առաջին։Երկրորդ։", language_hint="hy") == ("Առաջին։", "Երկրորդ։")
    assert split_sentences("ပထမ။ဒုတိယ။", language_hint="my") == ("ပထမ။", "ဒုတိယ။")


def test_greek_semicolon_is_locale_specific_question_mark_boundary():
    assert split_sentences("Πρώτη πρόταση; Δεύτερη πρόταση.", language_hint="el") == (
        "Πρώτη πρόταση;",
        "Δεύτερη πρόταση.",
    )
    # The same ASCII semicolon is not globally promoted to sentence terminal.
    assert split_sentences("First clause; second clause.", language_hint="en") == (
        "First clause; second clause.",
    )


def test_blank_line_is_a_strong_paragraph_boundary_without_terminal_punctuation():
    assert split_sentences("Heading-like text\n\nNext paragraph without punctuation", language_hint="en") == (
        "Heading-like text",
        "Next paragraph without punctuation",
    )


def test_combining_marks_and_emoji_zwj_sequences_remain_extended_graphemes():
    assert grapheme_clusters("e\u0301") == ("e\u0301",)
    assert grapheme_clusters("👨‍👩‍👧‍👦") == ("👨‍👩‍👧‍👦",)


def test_unicode_word_count_marks_complex_scripts_unreliable_for_word_guards():
    en_count, en_reliable = count_words("one two three", language_hint="en", backend="unicode")
    zh_count, zh_reliable = count_words("这是一个没有空格的中文句子", language_hint="zh", backend="unicode")
    th_count, th_reliable = count_words("ภาษาไทยไม่มีการเว้นวรรคทุกคำ", language_hint="th", backend="unicode")
    assert en_count == 3 and en_reliable is True
    assert zh_count >= 1 and zh_reliable is False
    assert th_count >= 1 and th_reliable is False


def test_boundary_backend_is_part_of_validated_splitter_configuration():
    SplitterProfile(sentence_boundary_backend="unicode").validate()
    SplitterProfile(sentence_boundary_backend="icu").validate()
    with pytest.raises(ValueError, match="sentence_boundary_backend"):
        SplitterProfile(sentence_boundary_backend="unknown").validate()


def test_unicode_terminal_inventory_handles_non_latin_sentence_terminal_property():
    # Ethiopic full stop and question mark are Unicode Sentence_Terminal characters.
    assert split_sentences("አንደኛ።ሁለተኛ፧", language_hint="am") == ("አንደኛ።", "ሁለተኛ፧")


def test_period_overloads_remain_protected_across_mixed_technical_text():
    text = "Version v1.2.3 is at api.example.com. Далее версия 2.0.1 работает."
    assert split_sentences(text, language_hint="mixed") == (
        "Version v1.2.3 is at api.example.com.",
        "Далее версия 2.0.1 работает.",
    )
