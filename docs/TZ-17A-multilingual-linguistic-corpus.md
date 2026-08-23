# TZ-17A — Multilingual Linguistic Corpus

## 1. Status

- **System:** AstraIndexator 1.0
- **Parent:** TZ-17 Testing & Verification
- **Related:** TZ-07, TZ-08, M6.2 Universal Multilingual Boundaries
- **Status:** executable verification baseline
- **Purpose:** provide corpus-backed evidence for sentence-boundary behavior rather than relying on hand-picked unit tests.

TZ-17A is a normative verification extension of TZ-17 for M6 multilingual segmentation.

---

## 2. Verification target

The corpus measures whether M6 places sentence boundaries at the expected source offsets.

For every fixture case:

```text
expected boundary offsets
        vs
predicted boundary offsets
        -> TP / FP / FN
        -> precision / recall / F1
```

Definitions:

```text
TP = expected boundary predicted correctly
FP = false split introduced by M6
FN = expected split missed by M6
precision = TP / (TP + FP)
recall    = TP / (TP + FN)
F1        = harmonic mean(precision, recall)
```

The end of the complete text is not scored as a boundary because both systems necessarily terminate there.

---

## 3. Corpus v1 language coverage

The initial executable corpus covers 20 declared languages:

```text
en English
ru Russian
kk Kazakh
de German
fr French
es Spanish
pt Portuguese
it Italian
el Greek
ar Arabic
he Hebrew
hi Hindi
zh Chinese
ja Japanese
ko Korean
th Thai
hy Armenian
my Myanmar
am Amharic
tr Turkish
```

A separate `mixed` case covers RU/KK/EN/technical-token code switching and protected values such as versions/IPs.

The corpus intentionally spans multiple writing systems and sentence-termination traditions rather than only Latin/Cyrillic scripts.

---

## 4. Golden fixture contract

Canonical fixture:

```text
tests/fixtures/linguistic/corpus-v1.json
```

Required fields:

```text
schemaVersion
fixtureVersion
languages[]
cases[]
gates
```

Each case contains:

```text
id
language
backend
text
expectedBoundaries[]
expectedSentences[]
```

`expectedBoundaries` are Python/Unicode string offsets immediately after each gold sentence except the final sentence.

Case IDs are unique and stable inside one fixture version.

---

## 5. Quality gates

Initial v1 gate:

```text
minimum declared languages = 20

core languages:
  kk
  ru
  en

core precision = 1.0
core recall    = 1.0

macro F1 >= 0.98
micro F1 >= 0.98
```

Rationale:

- `kk/ru/en` are the current product-critical languages and therefore use a strict no-regression gate;
- macro F1 prevents high-volume languages from hiding a weak language;
- micro F1 protects overall boundary quality;
- both FP and FN are visible, because over-splitting and under-splitting harm RAG differently.

Threshold changes require an explicit corpus/specification review and SHALL NOT be silently changed to make CI green.

---

## 6. Corpus governance

Golden data is test evidence, not implementation output.

A PR SHALL NOT change an expected boundary merely because the current splitter predicts something different.

A gold change requires one of:

1. clear annotation error;
2. documented linguistic correction;
3. approved policy change in TZ-07/TZ-08/M6 tailoring;
4. fixture-version increment with migration rationale.

When a language-specific rule changes, the PR SHOULD include both:

```text
positive example: boundary should exist
negative example: superficially similar boundary must not exist
```

This prevents one-sided regex tuning.

---

## 7. Required corpus categories

The corpus SHALL grow beyond simple punctuation examples. Release-candidate evidence SHOULD include, where linguistically applicable:

```text
ordinary declaratives
questions/exclamations
abbreviations and titles
initials
quoted sentences
parenthetical closers
numbers/decimals
semantic versions
IPv4/host/domain/email/URL
legal/article numbering
currency/date/time
ellipsis/multiple punctuation
paragraph boundaries
no-space writing systems
code switching
OCR noise samples
Unicode combining marks
mixed punctuation conventions
```

For languages with dictionary-dependent word segmentation, sentence-boundary evidence remains separate from word-count reliability evidence.

---

## 8. Backend policy

Every corpus case declares the boundary backend.

Baseline PR gate uses:

```text
backend = unicode
```

An ICU corpus run MAY be added as a separate verification profile when PyICU is installed. ICU and Unicode results SHALL NOT be mixed into one unnamed baseline because backend selection participates in M6 processing identity.

No silent backend fallback is accepted.

---

## 9. Executable evaluator

Implementation:

```text
src/astra_indexator/verification/linguistic.py
```

It SHALL:

- validate corpus schema/version and unique case IDs;
- run the real `split_sentences()` implementation;
- project predicted sentences back to source offsets;
- reject predictions that cannot be traced to source text;
- calculate TP/FP/FN;
- calculate per-language precision/recall/F1;
- calculate macro and micro quality;
- enforce corpus gates.

Primary CI test:

```text
tests/test_tz17_multilingual_linguistic_corpus.py
```

---

## 10. What this corpus proves

A green TZ-17A gate proves only:

```text
for the versioned corpus
+ configured boundary backend
+ current M6 profiles
sentence boundary placement satisfies approved thresholds
```

It does NOT by itself prove:

```text
perfect linguistic segmentation for every language
BGE-M3 token-size calibration
fragment semantic quality
retrieval relevance
OCR accuracy
production corpus representativeness
```

Those remain separate TZ-17 verification layers.

---

## 11. Growth requirement

Corpus v1 is a foundation, not the final production corpus.

Before production quality sign-off, each supported/core language SHOULD have multiple fixtures across the categories in section 7, with particular emphasis on real enterprise/legal/technical text.

The preferred evolution is:

```text
v1: breadth across >=20 languages
v2: adversarial abbreviation/numeric/quote coverage
v3: domain/legal/technical corpus
v4: OCR-derived multilingual boundary corpus
```

Every tailoring rule added to M6 after v1 SHALL be accompanied by corpus evidence demonstrating the motivating false positive/false negative and protecting against regression.

---

## 12. Relation to RAG quality

Sentence-boundary precision/recall is a local linguistic metric.

RAG acceptance remains downstream:

```text
boundary quality
  -> logical fragments
  -> AstraVector tokenizer-aware chunking
  -> embeddings
  -> retrieval
  -> relevance/citation metrics
```

A boundary change may improve linguistic F1 while harming retrieval, or vice versa. Therefore production tuning SHALL evaluate both TZ-17A linguistic metrics and the TZ-17 retrieval-quality corpus before changing the default splitter profile.
