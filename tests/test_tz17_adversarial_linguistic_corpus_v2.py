from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from astra_indexator.verification.linguistic import (
    BoundaryMetrics,
    evaluate_corpus,
    load_corpus,
    verify_corpus_gates,
)

CORPUS = Path(__file__).parent / "fixtures" / "linguistic" / "corpus-v2-adversarial.json"


def _category_metrics(corpus: dict, report) -> dict[str, BoundaryMetrics]:
    categories: dict[str, BoundaryMetrics] = defaultdict(lambda: BoundaryMetrics(0, 0, 0))
    case_by_id = {case["id"]: case for case in corpus["cases"]}
    for result in report.case_results:
        category = str(case_by_id[result.case_id].get("category", "uncategorized"))
        categories[category] = categories[category] + result.metrics
    return dict(categories)


def test_v2_has_adversarial_depth_and_twenty_languages():
    corpus = load_corpus(CORPUS)
    declared = set(corpus["languages"])
    case_languages = {case["language"] for case in corpus["cases"] if case["language"] != "mixed"}
    assert declared == case_languages
    assert len(declared) >= 20
    assert len(corpus["cases"]) >= int(corpus["gates"]["minimumCases"])
    categories = {case.get("category") for case in corpus["cases"]}
    assert {
        "abbreviation-title",
        "abbreviation-terminal",
        "initials",
        "technical-token",
        "decimal",
        "question",
        "quotes",
        "multi-punctuation",
        "paragraph-boundary",
        "mixed-technical",
        "legal-numbering",
    }.issubset(categories)


def test_v2_adversarial_precision_recall_quality_gate():
    corpus = load_corpus(CORPUS)
    report = evaluate_corpus(CORPUS)
    verify_corpus_gates(CORPUS, report)

    per_language_min = float(corpus["gates"]["perLanguageMinF1"])
    for language, metrics in report.by_language.items():
        assert metrics.f1 >= per_language_min, (language, metrics)

    category_min = float(corpus["gates"]["categoryMinF1"])
    for category, metrics in _category_metrics(corpus, report).items():
        assert metrics.f1 >= category_min, (category, metrics)


def test_v2_core_kk_ru_en_remain_exact():
    report = evaluate_corpus(CORPUS)
    for language in ("kk", "ru", "en"):
        metrics = report.by_language[language]
        assert metrics.precision == 1.0
        assert metrics.recall == 1.0
        assert metrics.f1 == 1.0


def test_v2_all_gold_sentences_are_materialized_exactly():
    corpus = load_corpus(CORPUS)
    by_id = {result.case_id: result for result in evaluate_corpus(CORPUS).case_results}
    failures = []
    for case in corpus["cases"]:
        predicted = by_id[case["id"]].predicted_sentences
        expected = tuple(case["expectedSentences"])
        if predicted != expected:
            failures.append((case["id"], expected, predicted))
    assert not failures, failures
