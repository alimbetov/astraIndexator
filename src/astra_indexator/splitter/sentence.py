from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class SentenceBoundaryProfile:
    profile_id: str
    languages: tuple[str, ...]
    terminal_chars: frozenset[str]
    abbreviations: frozenset[str]
    always_nonterminal: frozenset[str]


_COMMON_TERMINALS = frozenset({".", "!", "?", "…"})
_EN = frozenset({"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "etc.", "e.g.", "i.e.", "vs.", "fig.", "no."})
_RU = frozenset({"г.", "гг.", "т.е.", "т.д.", "т.п.", "др.", "стр.", "рис.", "им.", "см.", "напр."})
_KK = frozenset({"ж.", "т.б.", "т.с.с.", "мыс."})
_ALWAYS_EN = frozenset({"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "fig.", "no."})
_ALWAYS_RU = frozenset({"г.", "гг.", "стр.", "рис.", "им.", "см.", "напр."})
_ALWAYS_KK = frozenset({"ж.", "мыс."})

PROFILES: dict[str, SentenceBoundaryProfile] = {
    "en": SentenceBoundaryProfile("sentence-en-v2", ("en",), _COMMON_TERMINALS, _EN, _ALWAYS_EN),
    "ru": SentenceBoundaryProfile("sentence-ru-v2", ("ru",), _COMMON_TERMINALS, _RU, _ALWAYS_RU),
    "kk": SentenceBoundaryProfile("sentence-kk-v2", ("kk",), _COMMON_TERMINALS, _KK | _RU, _ALWAYS_KK | _ALWAYS_RU),
    "und": SentenceBoundaryProfile("sentence-generic-v2", ("und",), _COMMON_TERMINALS, frozenset(), frozenset()),
    "mixed": SentenceBoundaryProfile(
        "sentence-mixed-ru-kk-en-v2",
        ("ru", "kk", "en"),
        _COMMON_TERMINALS,
        _EN | _RU | _KK,
        _ALWAYS_EN | _ALWAYS_RU | _ALWAYS_KK,
    ),
}

_CLOSERS = set('"\'»”’)]}')
_WORD_BEFORE = re.compile(r"([\wÀ-žА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]+)$", re.UNICODE)


def profile_for(language_hint: str | None) -> SentenceBoundaryProfile:
    if not language_hint:
        return PROFILES["mixed"]
    value = language_hint.lower().replace("_", "-")
    if "," in value or "+" in value or value in {"mixed", "mul"}:
        return PROFILES["mixed"]
    primary = value.split("-", 1)[0]
    return PROFILES.get(primary, PROFILES["und"])


def _previous_token(text: str, period_index: int) -> str:
    start = period_index
    while start > 0 and not text[start - 1].isspace() and text[start - 1] not in '()[]{}"\'«»“”':
        start -= 1
    return text[start:period_index + 1].casefold()


def _next_lexical_char(text: str, index: int) -> str:
    cursor = index + 1
    while cursor < len(text) and (text[cursor].isspace() or text[cursor] in _CLOSERS):
        cursor += 1
    return text[cursor] if cursor < len(text) else ""


def _abbreviation_is_protected(text: str, index: int, token: str, profile: SentenceBoundaryProfile) -> bool:
    if token in profile.always_nonterminal:
        return True
    if token not in profile.abbreviations and not any(token.endswith(abbr) for abbr in profile.abbreviations):
        return False

    # Terminal-capable abbreviations (e.g. "etc.", "др.", "т.д.") are
    # treated as sentence-final when followed by an uppercase lexical start or
    # when they end the text. Lowercase continuation remains inside a sentence.
    next_char = _next_lexical_char(text, index)
    if not next_char:
        return False
    if next_char.isalpha() and next_char.isupper():
        return False
    return True


def _period_is_protected(text: str, index: int, profile: SentenceBoundaryProfile) -> bool:
    prev_char = text[index - 1] if index > 0 else ""
    next_char = text[index + 1] if index + 1 < len(text) else ""

    # Decimal, version, IPv4/domain/package-like internal dot.
    if prev_char.isalnum() and next_char.isalnum():
        return True

    token = _previous_token(text, index)
    if _abbreviation_is_protected(text, index, token, profile):
        return True

    match = _WORD_BEFORE.search(text[:index])
    if match and len(match.group(1)) == 1 and match.group(1).isalpha():
        return True

    # URL/email punctuation is protected only while the token continues after
    # the period. A terminal prose period after a URL/email remains a boundary.
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


def split_sentences(text: str, *, language_hint: str | None = None) -> tuple[str, ...]:
    if not text or not text.strip():
        return ()
    profile = profile_for(language_hint)
    out: list[str] = []
    start = 0
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch not in profile.terminal_chars:
            i += 1
            continue
        if ch == "." and _period_is_protected(text, i, profile):
            i += 1
            continue

        end = i + 1
        while end < length and text[end] in profile.terminal_chars:
            end += 1
        while end < length and text[end] in _CLOSERS:
            end += 1

        if end < length and not text[end].isspace():
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
