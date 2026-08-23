# M6 — Text Normalization + Logical Splitter

## Status

Implementation milestone for TZ-07 + TZ-08, including the normative M6 pre-implementation clarification merged via PR #20.

## Boundary

```text
ParsedDocument (M4/M5 source evidence)
        ↓
TextNormalizationService
        ↓
NormalizedDocument / NormalizedElement
        ↓
LogicalSplitter
        ↓
LogicalFragment[]
```

`ParsedDocument` is never rewritten in-place. `NormalizedElement` keeps both `originalText` and `normalizedText` and preserves source element identity/provenance.

## Normalization baseline

The implementation is deterministic and conservative:

- NFC only;
- CRLF/CR -> LF;
- prose horizontal whitespace collapse;
- code/preformatted layout preserved;
- NBSP normalization;
- soft-hyphen removal;
- ordinary hyphen dehyphenation only with explicit upstream `lineWrapHyphenEvidence`;
- TABLE cells normalized independently through structured `rows` data;
- empty table-cell positions preserved;
- repeated page furniture may be suppressed only with page + edge geometry + repetition evidence;
- source evidence is retained even when text is suppressed from index materialization.

The baseline does not perform translation, transliteration, spelling repair, lemmatization, lowercasing, numeric rewrite or OCR character guessing.

## Language-aware sentence boundaries

Sentence segmentation is a fallback boundary mechanism; document structure outranks punctuation.

One scanner is used with versioned boundary profiles:

```text
sentence-ru-v1
sentence-kk-v1
sentence-en-v1
sentence-mixed-ru-kk-en-v1
sentence-generic-v1
```

Baseline terminal punctuation is:

```text
. ! ? …
```

The language-specific difference is not merely the delimiter character. A valid boundary depends on abbreviation and protected-token context.

Examples that must not be split at internal periods:

```text
RU:  т.е.  т.д.  см.  г.
KK:  т.б.  т.с.с.  ж.
EN:  Dr.  Mr.  e.g.  i.e.

A. Smith
v1.2.3
10.20.30.40
https://example.com/api
name@example.com
```

For mixed RU/KK/EN content the scanner uses the union safety profile. A language switch is not itself a fragment boundary.

Kazakh lexical distinctions remain unchanged, including:

```text
Ә Ғ Қ Ң Ө Ұ Ү Һ І
ә ғ қ ң ө ұ ү һ і
```

Russian `Ё/ё` is also preserved.

Abbreviation inventories are versioned implementation data. Expanding or changing them requires regression fixtures because it may change sentence boundaries and therefore fragment identities.

## Logical fragmentation

The splitter is structure-first and tokenizer-free at runtime.

Priority:

```text
heading/section structure
→ paragraph/list/table boundary
→ language-aware sentence boundary
→ forced bounded text boundary
```

Default limits follow TZ-08:

```text
min_chars        800
target_chars    5000
soft_max_chars  8000
hard_max_chars 12000

target_words     700
soft_max_words  1200
hard_max_words  1800

target_sentences 25
hard_max_sentences 80
```

Blind raw sliding-window overlap is rejected in v1.

## Context and provenance

`normalizedText` is source-derived text.

`contextPrefix` is synthetic context and remains separate. It may repeat:

- parent headings;
- list introduction on continuation;
- table header on continuation.

Synthetic repeated context does not create new source provenance.

## Table behavior

Canonical table structure remains rows/cells. A tab/newline text form may be materialized only as derived fragment text.

Oversized tables split by row groups. Continuations repeat table-header context through `contextPrefix`; source `elementId` remains the table element.

## Deterministic fragment identity

Identity input includes:

```text
documentId
documentVersion
ordered sourceElementIds
normalized content hash
normalizer version/profile
splitter version/profile
fragment type
continuation discriminator
```

It does not include job/attempt/worker/lease/batch/runtime identity.

## Verification baseline

Tests cover at least:

- NFC equivalence;
- RU/KK/EN character preservation;
- prose vs code whitespace;
- conservative dehyphenation;
- structured table cells and empty cells;
- page-furniture true/false positives;
- RU/KK/EN abbreviations;
- initials/version/IP/URL guards;
- mixed-language sentence;
- heading/body structure;
- suppression from fragment text;
- forced hard split;
- deterministic IDs;
- splitter-version identity change;
- table continuation header context;
- list continuation context;
- no blind raw overlap;
- access-zone leading-zero and technical-token preservation.

## Deferred production calibration

M6 implementation does not claim final AstraVector model calibration. TZ-17/TZ-11 still must prove on representative RU/KK/EN/mixed corpora that:

- actual BGE-M3 token counts remain within downstream expectations;
- >=99.9% normal calibrated fragments are not truncated (or stricter approved target);
- structure-aware splitting does not regress retrieval quality against naive baselines;
- boundary profile changes are evaluated on golden and retrieval corpora.
