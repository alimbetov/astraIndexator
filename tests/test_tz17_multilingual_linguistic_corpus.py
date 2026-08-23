from __future__ import annotations

from pathlib import Path
import json

import pytest

from astra_indexator.verification.linguistic import evaluate_corpus, load_corpus, verify_corpus_gates


CORPUS = Path(__file__).parent / "fixtures" / "linguistic" / "corpus-v1.json"


def test_linguistic_corpus_covers_twenty_declared_languages():
    corpus = load_corpus(CORPUS)
    declared = set(corpus["languages"])
    case_languages = {case["language"] for case in corpus["cases"] if case["language"] != "mixed"}
    assert len(declared) >= 20
    assert declared == case_languages
    assert {"kk", "ru", "en", "zh", "ja", "ar", "hi"}.issubset(declared)


def test_unicode_sentence_boundary_precision_recall_quality_gate():
    report = evaluate_corpus(CORPUS)
    verify_corpus_gates(CORPUS, report)
    assert report.micro.precision >= 0.98
    assert report.micro.recall >= 0.98
    assert report.micro.f1 >= 0.98
    assert report.macro_f1 >= 0.98


def test_core_kk_ru_en_are_exact_on_v1_corpus():
    report = evaluate_corpus(CORPUS)
    for language in ("kk", "ru", "en"):
        metrics = report.by_language[language]
        assert metrics.precision == 1.0
        assert metrics.recall == 1.0
        assert metrics.f1 == 1.0


def test_all_v1_cases_preserve_expected_sentence_materialization():
    corpus = load_corpus(CORPUS)
    by_id = {result.case_id: result for result in evaluate_corpus(CORPUS).case_results}
    for case in corpus["cases"]:
        assert by_id[case["id"]].predicted_sentences == tuple(case["expectedSentences"]), case["id"]


def test_false_split_and_missed_split_are_scored_separately(tmp_path: Path):
    corpus = {
        "schemaVersion": "astra-indexator-linguistic-corpus-v1",
        "fixtureVersion": "test",
        "languages": ["en"],
        "cases": [{
            "id": "scoring",
            "language": "en",
            "backend": "unicode",
            "text": "One. Two. Three.",
            "expectedBoundaries": [4, 9],
            "expectedSentences": ["One.", "Two.", "Three."],
        }],
        "gates": {"minimumLanguages": 1},
    }
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus), encoding="utf-8")
    report = evaluate_corpus(path)
    assert report.micro.true_positive == 2
    assert report.micro.false_positive == 0
    assert report.micro.false_negative == 0


def test_duplicate_case_ids_are_rejected(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({
        "schemaVersion": "astra-indexator-linguistic-corpus-v1",
        "fixtureVersion": "bad",
        "cases": [
            {"id": "x", "language": "en", "text": "A."},
            {"id": "x", "language": "en", "text": "B."},
        ],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="CASE_ID_INVALID"):
        load_corpus(path)


def test_unknown_schema_is_rejected(tmp_path: Path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schemaVersion": "v0", "fixtureVersion": "x", "cases": [{}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="SCHEMA_UNSUPPORTED"):
        load_corpus(path)
