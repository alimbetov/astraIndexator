from __future__ import annotations

from dataclasses import dataclass
import unicodedata

import regex


@dataclass(frozen=True, slots=True)
class SentenceBoundaryProfile:
    profile_id: str
    languages: tuple[str, ...]
    abbreviations: frozenset[str]
    always_nonterminal: frozenset[str]
    extra_terminal_chars: frozenset[str] = frozenset()


_EN = frozenset({"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "etc.", "e.g.", "i.e.", "vs.", "fig.", "no."})
_RU = frozenset({"г.", "гг.", "т.е.", "т.д.", "т.п.", "др.", "стр.", "рис.", "им.", "см.", "напр."})
_KK = frozenset({"ж.", "т.б.", "т.с.с.", "мыс."})
_ALWAYS_EN = frozenset({"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "fig.", "no."})
_ALWAYS_RU = frozenset({"г.", "гг.", "стр.", "рис.", "им.", "см.", "напр."})
_ALWAYS_KK = frozenset({"ж.", "мыс."})

PROFILES: dict[str, SentenceBoundaryProfile] = {
    "en": SentenceBoundaryProfile("sentence-en-v3", ("en",), _EN, _ALWAYS_EN),
    "ru": SentenceBoundaryProfile("sentence-ru-v3", ("ru",), _RU, _ALWAYS_RU),
    "kk": SentenceBoundaryProfile("sentence-kk-v3", ("kk",), _KK | _RU, _ALWAYS_KK | _ALWAYS_RU),
    "el": SentenceBoundaryProfile("sentence-el-v1", ("el",), frozenset(), frozenset(), frozenset({";"})),
    "und": SentenceBoundaryProfile("sentence-unicode-generic-v1", ("und",), frozenset(), frozenset()),
    "mixed": SentenceBoundaryProfile(
        "sentence-mixed-ru-kk-en-v3",
        ("ru", "kk", "en"),
        _EN | _RU | _KK,
        _ALWAYS_EN | _ALWAYS_RU | _ALWAYS_KK,
    ),
}

_STERM = regex.compile(r"\p{Sentence_Terminal}")
_WORDLIKE = regex.compile(r"(?V1)\b[\p{L}\p{N}][\p{L}\p{M}\p{N}\p{Pc}\p{Pd}'’]*\b", regex.WORD)
_COMPLEX_WORD_SCRIPT = regex.compile(
    r"[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}"
    r"\p{Script=Thai}\p{Script=Lao}\p{Script=Khmer}\p{Script=Myanmar}]"
)
_LETTER_BEFORE = regex.compile(r"([\p{L}\p{M}]+)$")


def profile_for(language_hint: str | None) -> SentenceBoundaryProfile:
    if not language_hint:
        return PROFILES["mixed"]
    value = language_hint.lower().replace("_", "-")
    if "," in value or "+" in value or value in {"mixed", "mul"}:
        return PROFILES["mixed"]
    primary = value.split("-", 1)[0]
    return PROFILES.get(primary, PROFILES["und"])


def _locale_id(language_hint: str | None) -> str:
    if not language_hint or language_hint.lower() in {"mixed", "mul", "und"}:
        return "root"
    return language_hint.replace("_", "-")


def _is_closer(ch: str) -> bool:
    if not ch:
        return False
    return unicodedata.category(ch) in {"Pe", "Pf"} or ch in {'"', "'", "»", "”", "’"}


def _is_sentence_terminal(ch: str, profile: SentenceBoundaryProfile) -> bool:
    return ch == "." or ch in profile.extra_terminal_chars or bool(_STERM.fullmatch(ch))


def _previous_token(text: str, period_index: int) -> str:
    start = period_index
    while start > 0:
        ch = text[start - 1]
        if ch.isspace() or _is_closer(ch) or unicodedata.category(ch) in {"Ps", "Pi"}:
            break
        start -= 1
    return text[start:period_index + 1].casefold()


def _next_lexical_char(text: str, index: int) -> str:
    cursor = index + 1
    while cursor < len(text) and (text[cursor].isspace() or _is_closer(text[cursor])):
        cursor += 1
    return text[cursor] if cursor < len(text) else ""


def _abbreviation_is_protected(text: str, index: int, token: str, profile: SentenceBoundaryProfile) -> bool:
    if token in profile.always_nonterminal:
        return True
    if token not in profile.abbreviations and not any(token.endswith(abbr) for abbr in profile.abbreviations):
        return False
    next_char = _next_lexical_char(text, index)
    if not next_char:
        return False
    if next_char.isalpha() and next_char.isupper():
        return False
    return True


def _period_is_protected(text: str, index: int, profile: SentenceBoundaryProfile) -> bool:
    prev_char = text[index - 1] if index > 0 else ""
    next_char = text[index + 1] if index + 1 < len(text) else ""
    if prev_char.isalnum() and next_char.isalnum():
        return True
    token = _previous_token(text, index)
    if _abbreviation_is_protected(text, index, token, profile):
        return True
    match = _LETTER_BEFORE.search(text[:index])
    if match and len(match.group(1)) == 1:
        return True
    left_start = max(text.rfind(" ", 0, index), text.rfind("\n", 0, index)) + 1
    right_end = index + 1
    while right_end < len(text) and not text[right_end].isspace():
        right_end += 1
    token_full = text[left_start:right_end]
    period_is_inside_token = index < right_end - 1
    if period_is_inside_token and any(marker in token_full for marker in ("://", "@")):
        return True
    if period_is_inside_token and token_full.count(".") >= 2:
        return True
    return False


def _split_unicode(text: str, language_hint: str | None) -> tuple[str, ...]:
    profile = profile_for(language_hint)
    out: list[str] = []
    start = 0
    i = 0
    length = len(text)
    while i < length:
        if text.startswith("\n\n", i):
            sentence = text[start:i].strip()
            if sentence:
                out.append(sentence)
            while i < length and text[i] == "\n":
                i += 1
            start = i
            continue
        ch = text[i]
        if not _is_sentence_terminal(ch, profile):
            i += 1
            continue
        if ch == "." and _period_is_protected(text, i, profile):
            i += 1
            continue
        end = i + 1
        while end < length and _is_sentence_terminal(text[end], profile):
            end += 1
        while end < length and _is_closer(text[end]):
            end += 1
        if end < length and not text[end].isspace() and ch == ".":
            i += 1
            continue
        sentence = text[start:end].strip()
        if sentence:
            out.append(sentence)
        while end < length and text[end].isspace():
            end += 1
        start = end
        i = end
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return tuple(out)


def _split_icu(text: str, language_hint: str | None) -> tuple[str, ...]:
    try:
        import icu  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("SPLITTER_ICU_UNAVAILABLE") from exc
    locale_name = _locale_id(language_hint)
    locale = icu.Locale(f"{locale_name}@ss=standard") if locale_name != "root" else icu.Locale.getRoot()
    iterator = icu.BreakIterator.createSentenceInstance(locale)
    iterator.setText(text)
    out: list[str] = []
    start = iterator.first()
    for end in iterator:
        sentence = text[start:end].strip()
        if sentence:
            out.append(sentence)
        start = end
    return tuple(out)


def split_sentences(text: str, *, language_hint: str | None = None, backend: str = "unicode") -> tuple[str, ...]:
    if not text or not text.strip():
        return ()
    if backend == "unicode":
        return _split_unicode(text, language_hint)
    if backend == "icu":
        return _split_icu(text, language_hint)
    raise ValueError(f"SPLITTER_BOUNDARY_BACKEND_UNSUPPORTED:{backend}")


def grapheme_clusters(text: str) -> tuple[str, ...]:
    r"""Extended grapheme clusters (UAX #29-style `\X`) for split safety."""
    return tuple(regex.findall(r"\X", text))


def count_words(text: str, *, language_hint: str | None = None, backend: str = "unicode") -> tuple[int, bool]:
    """Return ``(count, reliable_for_word_guards)``."""
    if not text:
        return 0, True
    if backend == "unicode":
        count = len(_WORDLIKE.findall(text))
        reliable = _COMPLEX_WORD_SCRIPT.search(text) is None
        return count, reliable
    if backend != "icu":
        raise ValueError(f"SPLITTER_BOUNDARY_BACKEND_UNSUPPORTED:{backend}")
    try:
        import icu  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("SPLITTER_ICU_UNAVAILABLE") from exc
    locale = icu.Locale(_locale_id(language_hint))
    iterator = icu.BreakIterator.createWordInstance(locale)
    iterator.setText(text)
    count = 0
    start = iterator.first()
    for end in iterator:
        token = text[start:end]
        if regex.search(r"[\p{L}\p{N}]", token):
            count += 1
        start = end
    return count, True
