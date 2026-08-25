from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from astra_indexator.splitter.sentence import split_sentences


@dataclass(frozen=True, slots=True)
class BoundaryMetrics:
    true_positive: int
    false_positive: int
    false_negative: int

    @property
    def precision(self) -> float:
        denominator = self.true_positive + self.false_positive
        return 1.0 if denominator == 0 else self.true_positive / denominator

    @property
    def recall(self) -> float:
        denominator = self.true_positive + self.false_negative
        return 1.0 if denominator == 0 else self.true_positive / denominator

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 0.0 if p + r == 0.0 else 2.0 * p * r / (p + r)

    def __add__(self, other: "BoundaryMetrics") -> "BoundaryMetrics":
        return BoundaryMetrics(
            self.true_positive + other.true_positive,
            self.false_positive + other.false_positive,
            self.false_negative + other.false_negative,
        )


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    language: str
    category: str | None
    expected_boundaries: tuple[int, ...]
    predicted_boundaries: tuple[int, ...]
    predicted_sentences: tuple[str, ...]
    metrics: BoundaryMetrics


@dataclass(frozen=True, slots=True)
class CorpusReport:
    fixture_version: str
    case_results: tuple[CaseResult, ...]
    by_language: dict[str, BoundaryMetrics]
    by_category: dict[str, BoundaryMetrics]
    micro: BoundaryMetrics
    macro_f1: float


def _boundary_offsets(text: str, sentences: tuple[str, ...]) -> tuple[int, ...]:
    if len(sentences) <= 1:
        return ()
    offsets: list[int] = []
    cursor = 0
    for index, sentence in enumerate(sentences):
        position = text.find(sentence, cursor)
        if position < 0:
            raise ValueError("LINGUISTIC_SENTENCE_NOT_TRACEABLE_TO_SOURCE")
        end = position + len(sentence)
        if index < len(sentences) - 1:
            offsets.append(end)
        cursor = end
    return tuple(offsets)


def _gold_boundaries(case: dict) -> tuple[int, ...]:
    text = str(case.get("text", ""))
    expected_sentences_raw = case.get("expectedSentences")
    if isinstance(expected_sentences_raw, list) and expected_sentences_raw:
        expected_sentences = tuple(str(value) for value in expected_sentences_raw)
        derived = _boundary_offsets(text, expected_sentences)
        explicit = tuple(int(value) for value in case.get("expectedBoundaries", []))
        # Human-maintained Unicode offsets are easy to miscount. Exact sentence
        # materialization is the canonical annotation; offsets are derived from it.
        # A corpus may opt into strict explicit-offset validation when needed.
        if case.get("strictBoundaryOffsets") and explicit != derived:
            raise ValueError(
                f"LINGUISTIC_EXPLICIT_BOUNDARY_MISMATCH:{case.get('id')}:{explicit}!={derived}"
            )
        return derived
    return tuple(int(value) for value in case.get("expectedBoundaries", []))


def load_corpus(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != "astra-indexator-linguistic-corpus-v1":
        raise ValueError("LINGUISTIC_CORPUS_SCHEMA_UNSUPPORTED")
    if not data.get("fixtureVersion"):
        raise ValueError("LINGUISTIC_CORPUS_FIXTURE_VERSION_REQUIRED")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("LINGUISTIC_CORPUS_CASES_REQUIRED")
    case_ids = [case.get("id") for case in cases]
    if len(case_ids) != len(set(case_ids)) or any(not value for value in case_ids):
        raise ValueError("LINGUISTIC_CORPUS_CASE_ID_INVALID")
    for case in cases:
        _gold_boundaries(case)
    return data


def _score(expected: tuple[int, ...], predicted: tuple[int, ...]) -> BoundaryMetrics:
    expected_set = set(expected)
    predicted_set = set(predicted)
    return BoundaryMetrics(
        true_positive=len(expected_set & predicted_set),
        false_positive=len(predicted_set - expected_set),
        false_negative=len(expected_set - predicted_set),
    )


def evaluate_corpus(path: Path, *, backend_override: str | None = None) -> CorpusReport:
    corpus = load_corpus(path)
    results: list[CaseResult] = []
    by_language: dict[str, BoundaryMetrics] = {}
    by_category: dict[str, BoundaryMetrics] = {}
    micro = BoundaryMetrics(0, 0, 0)

    for case in corpus["cases"]:
        text = str(case["text"])
        language = str(case["language"])
        category_raw = case.get("category")
        category = str(category_raw) if category_raw else None
        backend = backend_override or str(case.get("backend", "unicode"))
        expected = _gold_boundaries(case)
        predicted_sentences = split_sentences(text, language_hint=language, backend=backend)
        predicted = _boundary_offsets(text, predicted_sentences)
        metrics = _score(expected, predicted)
        result = CaseResult(
            case_id=str(case["id"]),
            language=language,
            category=category,
            expected_boundaries=expected,
            predicted_boundaries=predicted,
            predicted_sentences=predicted_sentences,
            metrics=metrics,
        )
        results.append(result)
        by_language[language] = by_language.get(language, BoundaryMetrics(0, 0, 0)) + metrics
        if category:
            by_category[category] = by_category.get(category, BoundaryMetrics(0, 0, 0)) + metrics
        micro = micro + metrics

    language_f1 = [metrics.f1 for metrics in by_language.values()]
    macro_f1 = sum(language_f1) / len(language_f1) if language_f1 else 0.0
    return CorpusReport(
        fixture_version=str(corpus["fixtureVersion"]),
        case_results=tuple(results),
        by_language=by_language,
        by_category=by_category,
        micro=micro,
        macro_f1=macro_f1,
    )


def verify_corpus_gates(path: Path, report: CorpusReport) -> None:
    corpus = load_corpus(path)
    gates = corpus.get("gates") or {}
    declared_languages = set(corpus.get("languages") or [])
    minimum_languages = int(gates.get("minimumLanguages", 1))
    if len(declared_languages) < minimum_languages:
        raise AssertionError(
            f"LINGUISTIC_LANGUAGE_COVERAGE_TOO_SMALL:{len(declared_languages)}<{minimum_languages}"
        )

    core_precision = float(gates.get("coreMinPrecision", 1.0))
    core_recall = float(gates.get("coreMinRecall", 1.0))
    for language in gates.get("coreLanguages", []):
        metrics = report.by_language.get(language)
        if metrics is None:
            raise AssertionError(f"LINGUISTIC_CORE_LANGUAGE_MISSING:{language}")
        if metrics.precision < core_precision:
            raise AssertionError(
                f"LINGUISTIC_CORE_PRECISION_REGRESSION:{language}:{metrics.precision:.6f}<{core_precision:.6f}"
            )
        if metrics.recall < core_recall:
            raise AssertionError(
                f"LINGUISTIC_CORE_RECALL_REGRESSION:{language}:{metrics.recall:.6f}<{core_recall:.6f}"
            )

    macro_min = float(gates.get("macroMinF1", 0.0))
    micro_min = float(gates.get("microMinF1", 0.0))
    if report.macro_f1 < macro_min:
        raise AssertionError(
            f"LINGUISTIC_MACRO_F1_REGRESSION:{report.macro_f1:.6f}<{macro_min:.6f}"
        )
    if report.micro.f1 < micro_min:
        raise AssertionError(
            f"LINGUISTIC_MICRO_F1_REGRESSION:{report.micro.f1:.6f}<{micro_min:.6f}"
        )

    per_language_min = float(gates.get("perLanguageMinF1", 0.0))
    if per_language_min:
        for language, metrics in report.by_language.items():
            if metrics.f1 < per_language_min:
                raise AssertionError(
                    f"LINGUISTIC_LANGUAGE_F1_REGRESSION:{language}:{metrics.f1:.6f}<{per_language_min:.6f}"
                )

    per_category_min = float(gates.get("perCategoryMinF1", 0.0))
    if per_category_min:
        for category, metrics in report.by_category.items():
            if metrics.f1 < per_category_min:
                raise AssertionError(
                    f"LINGUISTIC_CATEGORY_F1_REGRESSION:{category}:{metrics.f1:.6f}<{per_category_min:.6f}"
                )
