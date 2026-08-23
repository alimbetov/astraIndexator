from __future__ import annotations

from pathlib import Path

from astra_indexator.verification.linguistic import evaluate_corpus, load_corpus, verify_corpus_gates

CORPUS = Path(__file__).parent / "fixtures" / "linguistic" / "corpus-v3-enterprise-legal-technical.json"


def test_v3_corpus_has_enterprise_legal_technical_depth():
    corpus = load_corpus(CORPUS)
    categories = {case.get("category") for case in corpus["cases"]}
    required = {
        "legal-numbering",
        "legal-identifiers",
        "regulatory-reference",
        "money",
        "enterprise-abbreviation",
        "names-initials",
        "bilingual",
        "access-zone",
        "api-identifiers",
        "technical-identifiers",
        "technical-version",
        "technical-status",
        "table-context",
        "date-time",
    }
    assert required.issubset(categories)
    assert len(corpus["cases"]) >= 25


def test_v3_quality_gate():
    report = evaluate_corpus(CORPUS)
    verify_corpus_gates(CORPUS, report)
    assert report.micro.precision >= 0.98
    assert report.micro.recall >= 0.98
    assert report.macro_f1 >= 0.98


def test_v3_core_languages_remain_exact():
    report = evaluate_corpus(CORPUS)
    for language in ("kk", "ru", "en"):
        metrics = report.by_language[language]
        assert metrics.precision == 1.0
        assert metrics.recall == 1.0
        assert metrics.f1 == 1.0


def test_v3_gold_materialization_is_exact():
    corpus = load_corpus(CORPUS)
    by_id = {result.case_id: result for result in evaluate_corpus(CORPUS).case_results}
    failures = []
    for case in corpus["cases"]:
        predicted = by_id[case["id"]].predicted_sentences
        expected = tuple(case["expectedSentences"])
        if predicted != expected:
            failures.append((case["id"], expected, predicted))
    assert not failures, failures
