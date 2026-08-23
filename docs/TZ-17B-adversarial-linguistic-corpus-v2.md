# TZ-17B — Adversarial Multilingual Linguistic Corpus v2

## Status

- System: AstraIndexator 1.0
- Parent: TZ-17 Testing & Verification
- Related: TZ-07, TZ-08, TZ-17A, M6.2
- Scope: sentence-boundary adversarial verification
- Status: executable quality gate

## Purpose

TZ-17A v1 established breadth across 20 languages. TZ-17B v2 increases depth without widening the language list. The goal is to expose false sentence splits and missed sentence boundaries that basic punctuation fixtures do not detect.

## Corpus policy

The v2 fixture is versioned and immutable once merged:

```text
tests/fixtures/linguistic/corpus-v2-adversarial.json
```

Gold boundaries MUST represent the intended linguistic interpretation. A failing implementation is not a reason to rewrite gold data. Corrections to gold annotations require explicit linguistic justification and a fixture-version change when semantics change.

## Coverage classes

The adversarial set includes, where linguistically applicable:

- title and inline abbreviations;
- abbreviations that may terminate a sentence;
- initials;
- quotes and closing punctuation;
- ellipsis and repeated terminal punctuation;
- decimals and locale-formatted numbers;
- dates;
- versions, IP addresses, URLs and email addresses;
- legal/article numbering;
- paragraph boundaries without final punctuation;
- locale-specific punctuation such as Greek question-semicolon;
- CJK no-space sentence boundaries;
- Arabic/Indic/Armenian/Myanmar/Ethiopic terminal punctuation;
- mixed RU/KK/EN technical and legal text;
- Access Zone codes with leading zeroes.

## Languages

The v2 corpus keeps the same 20-language breadth baseline:

```text
am ar de el en es fr he hi hy it ja kk ko my pt ru th tr zh
```

Mixed-language cases are evaluated separately and do not inflate the declared language count.

## Metrics

Each gold sentence boundary is represented as a source-text offset. Predicted boundaries are compared against those offsets:

```text
TP = predicted boundary matches gold
FP = predicted boundary where gold has none
FN = gold boundary not predicted

precision = TP / (TP + FP)
recall    = TP / (TP + FN)
F1        = harmonic mean(precision, recall)
```

Materialized sentence strings are also checked exactly so that a numerically correct boundary set cannot hide text loss or punctuation corruption.

## Gates

Core project languages remain strict:

```text
kk precision = 1.0
kk recall    = 1.0
ru precision = 1.0
ru recall    = 1.0
en precision = 1.0
en recall    = 1.0
```

Global corpus:

```text
macro F1 >= 0.98
micro F1 >= 0.98
minimum languages = 20
minimum adversarial cases = 50
```

Additional v2 guards:

```text
per-language F1 >= 0.90
per-category F1 >= 0.90
```

These secondary floors prevent a weak language or adversarial category from being hidden by strong aggregate scores.

## Change governance

Any change to sentence-boundary logic SHALL run both v1 and v2. New tailoring should normally add at least:

1. a positive example where the boundary must exist;
2. a negative/minimal-pair example where visually similar punctuation must not split;
3. a technical-token example when the tailoring can interact with dots or punctuation inside identifiers.

## Non-goals

This corpus does not prove full syntactic parsing, semantic interpretation, word segmentation quality, OCR accuracy or retrieval quality. It specifically proves sentence-boundary behavior. Those concerns remain covered by their respective TZ-17 suites.
