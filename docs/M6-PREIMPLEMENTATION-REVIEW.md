# M6 — Pre-Implementation Review of TZ-07 + TZ-08

## Status

- **Milestone:** M6 — Text Normalization & Logical Splitter
- **Reviewed specifications:** TZ-07, TZ-08
- **Decision:** **GO after these clarifications are treated as normative for M6 implementation**
- **Scope:** implementation contract clarification only; no change to M4/M5 canonical parser/OCR ownership

## Review conclusion

TZ-07 and TZ-08 are sufficiently complete to start M6. The review found three implementation ambiguities that must be resolved explicitly so the implementation does not violate source-evidence, table/provenance, or downstream identity invariants.

This document is a normative implementation clarification for M6. Where a detail below is more specific than TZ-07/TZ-08, this document governs M6 implementation while preserving the intent of those specifications.

---

## 1. Immutable source evidence: ParsedDocument is not rewritten

Current M4/M5 `DocumentElement` has a canonical source/reconciled field:

```text
DocumentElement.text
```

TZ-07 requires distinct source/reconciled and normalized representations:

```text
originalText
normalizedText
```

Therefore M6 SHALL NOT overwrite or mutate `ParsedDocument.elements[].text` in-place.

M6 SHALL introduce a separate immutable normalization view, conceptually:

```text
ParsedDocument
  DocumentElement.text  # accepted M4/M5 evidence
        |
        v
NormalizedDocument
  NormalizedElement
    sourceElementId
    originalText
    normalizedText
    suppression
    provenance
    normalizedStructuredData
```

Required invariants:

1. `ParsedDocument` remains immutable and traceable.
2. `originalText` equals the accepted M4/M5 textual evidence for that element before TZ-07 transformation.
3. `normalizedText` is the deterministic TZ-07 representation used by TZ-08.
4. A suppressed element is not silently deleted; suppression reason and source element identity remain available.
5. Source geometry, source locator, parent/section relationships, OCR metadata/confidence and language hints remain traceable.
6. Normalized IDs/hashes SHALL NOT replace stable M4 source element IDs.

Recommended conceptual types:

```text
NormalizationIdentity
  normalizerVersion
  profile

NormalizationOperationSummary

NormalizedElement
  sourceElementId
  elementType
  orderIndex
  originalText?
  normalizedText?
  parentSourceElementId?
  level?
  sectionPath[]
  sourceGeometry?
  sourceLocator
  provenanceMetadata
  normalizedStructuredData
  suppressionDecision
  warnings[]

NormalizedDocument
  documentId
  documentVersion
  sourceSha256
  sourceFormat
  normalizationIdentity
  elements[]
  diagnostics
```

The exact Python dataclass naming may differ, but these semantics are mandatory.

---

## 2. Structured tables are normalized structurally, not flattened

The current parser baseline represents table structure through canonical element metadata, including row/cell arrays where available (for example DOCX `metadata.rows`).

M6 SHALL treat this structured representation as source structure.

Rules:

1. table cells are normalized independently with safe TZ-07 rules;
2. row order and column position are preserved;
3. empty cells remain represented when position is meaningful;
4. no normalization step may merge text across cell boundaries;
5. table header evidence remains distinguishable from body rows where supplied by the parser;
6. a pipe-delimited/Markdown-like textual rendering MAY be produced later as a deterministic derived representation for LogicalFragment/LogicalBlock text, but it is not the canonical table source model;
7. normalization diagnostics SHALL identify cell-level warnings without requiring a full character diff.

Conceptual normalized table payload:

```json
{
  "rows": [
    [
      {"source": "  Наименование ", "normalized": "Наименование"},
      {"source": "Сумма", "normalized": "Сумма"}
    ],
    [
      {"source": "Услуга", "normalized": "Услуга"},
      {"source": "1 000 000", "normalized": "1 000 000"}
    ]
  ]
}
```

This preserves TZ-07 requirements for numeric fidelity and TZ-08 requirements for row-group splitting/header repetition.

---

## 3. Page-furniture suppression requires real page/layout evidence

TZ-07 page-header/footer/page-number suppression is allowed only when page/layout evidence is available.

M6 SHALL NOT infer page furniture for source formats/elements that do not provide reliable page/region provenance merely from repeated text.

Required baseline:

```text
repeat text
+ page identity
+ top/bottom region evidence
+ repetition threshold
+ structural-role exclusion
= eligible furniture suppression
```

Without page/position evidence:

```text
KEEP_CONTENT
or
KEEP_WITH_WARNING
```

not silent suppression.

This prevents repeated headings, legal clauses, spreadsheet headers, slide titles, EPUB navigation text or recurring business labels from being incorrectly removed.

---

## 4. Normalization profile and protected spans are executable contracts

M6 SHALL implement a typed, startup-validated normalization profile.

Baseline profile:

```text
multilingual-general-v1
text-normalizer-v1
Unicode NFC
translation = false
transliteration = false
spell correction = false
```

Protected-span recognition is mandatory before dehyphenation or risky line-join rules for at least:

```text
UUID
URL
email
IPv4/IPv6
host/domain
path
package/class/method identifier
snake_case/camelCase
semantic version
number/decimal/percentage/currency
date/time
article/clause numbering
access-zone code
hash/digest
```

Particularly:

```text
0000
0100
...
0900
```

and any valid four-digit access-zone code MUST retain leading zeroes.

---

## 5. Conservative line joining and dehyphenation

Line joining SHALL only operate within structural continuity already supported by parser/OCR evidence.

M6 MUST NOT join across:

```text
heading boundary
list item boundary
table cell boundary
code/preformatted boundary
independent OCR region without continuity evidence
section boundary
```

Conservative dehyphenation requires evidence of a physical line-wrap artifact. Ambiguous forms remain unchanged.

Mandatory preserved examples:

```text
бизнес-процесс
по-русски
state-of-the-art
ISO-9001
X-Request-ID
10-20
```

---

## 6. Logical Splitter consumes only the normalized view

TZ-08 SHALL consume `NormalizedDocument/NormalizedElement[]`, not mutate or re-clean parser/OCR text.

Splitter SHALL NOT repeat TZ-07 cleanup rules.

Canonical flow for M6:

```text
ParsedDocument from M5
        |
        v
TextNormalizationService
        |
        v
NormalizedDocument
        |
        v
LogicalSplitter
        |
        v
LogicalFragment[]
```

This creates one cleanup authority and makes deterministic reruns testable.

---

## 7. LogicalFragment source text and synthetic context are separate

A `LogicalFragment` SHALL keep these concepts separate:

```text
normalizedText   # source-derived normalized content
contextPrefix    # synthetic hierarchy/context
```

`contextPrefix` SHALL NOT be represented as physically present source text and SHALL NOT expand the source range/provenance.

Downstream materialization may construct:

```text
contextPrefix + separator + normalizedText
```

for `LogicalBlock.text`, according to TZ-09/TZ-11.

Citation/source ranges always refer to the real contributing source elements.

---

## 8. Fragment identity and determinism

M6 fragment identity SHALL be deterministic and independent of worker/job attempt/transport batching.

Required identity inputs:

```text
documentId
documentVersion
ordered contributing sourceElementIds/source range
normalized content hash
normalizerVersion/profile
splitterVersion/profile
fragmentType/continuation discriminator where required
```

The following MUST NOT affect fragment identity:

```text
jobId
processingAttemptId
workerId
leaseGeneration
RPC batch number
runtime load
```

A normalizer or splitter semantic version/profile change is expected to change downstream fragment identity/fingerprint where content/boundaries can change.

---

## 9. Size guards and no-tokenizer runtime

TZ-08 baseline size guards remain approved as initial calibration values:

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

These are NOT AstraVector model constants.

M6 SHALL remain tokenizer-free at runtime. Real BGE-M3/AstraVector calibration is a TZ-17/TZ-11 verification gate and may produce a new versioned splitter profile without changing the runtime ownership boundary.

---

## 10. Lists and tables continuation semantics

For oversized structured content:

### Lists

Continuation fragments SHALL preserve source item identity and may repeat the list introduction only in `contextPrefix`/synthetic context, not duplicate it as new physical source evidence.

### Tables

Continuation fragments SHALL split only at row-group boundaries where possible. Column/table headers may be repeated as synthetic context while source range points only to the rows actually represented by the fragment.

A single row MUST NOT be split unless it independently exceeds hard guards and no safer structural split exists; such a fallback is `forced=true` with an explicit reason.

---

## 11. Sentence segmentation fallback

Sentence segmentation is a fallback boundary mechanism, not a primary semantic parser.

M6 SHALL use protected-span-aware lightweight sentence segmentation suitable for RU/KK/EN/mixed content.

Language detection is advisory:

```text
unknown -> und -> generic safe segmentation
```

No failure is allowed solely because language detection is uncertain.

---

## 12. Bounded-memory requirement

Both normalization and splitting SHALL support bounded-memory operation.

Approved approach:

```text
Normalization pass A
  bounded evidence/statistics
Normalization pass B
  element normalization stream
        |
        v
Splitter accumulator
  current hierarchy
  current fragment candidate
  bounded look-behind/look-ahead
```

No M6 implementation may require concatenating the whole document into one `document_text` string as the canonical algorithm.

---

## 13. M6 minimum executable verification matrix

Before M6 is considered implementation-complete, tests MUST cover at least:

### Normalization positive/negative

1. NFC equivalent input -> byte-identical normalized output;
2. Kazakh `ә ғ қ ң ө ұ ү һ і` preserved;
3. `ё` preserved;
4. mixed RU/KK/EN preserved;
5. prose whitespace/CRLF normalization;
6. code indentation preserved;
7. safe line-wrap join;
8. list boundaries not joined;
9. safe dehyphenation;
10. lexical hyphen preserved;
11. UUID/URL/version/IP/identifier preserved;
12. `0000`/`0100` and other four-digit zone codes preserve leading zeroes;
13. numeric/date/currency formatting preserved;
14. page furniture suppressed only with repeated positional evidence;
15. same repeated text without page/location evidence is not suppressed;
16. table cell boundaries and empty cells preserved;
17. OCR provenance/confidence preserved;
18. deterministic repeated normalization;
19. normalizer profile/version changes processing identity;
20. malformed/ambiguous normalization preserves source + warning rather than guessing.

### Logical splitter positive/negative

21. heading stays with body;
22. strong section boundary beats target-size filling;
23. mixed-language switch alone does not split;
24. unknown language falls back safely;
25. short compatible sections may merge;
26. legal/FAQ/warning small-fragment exceptions remain independent;
27. oversized paragraph uses sentence fallback;
28. unsegmentable text uses marked forced split;
29. no normal fragment exceeds hard guards;
30. no raw sliding-window overlap by default;
31. oversized list continuation repeats only synthetic context;
32. compact table kept intact;
33. oversized table splits by row groups and preserves/repeats header context;
34. OCR-derived content keeps source image/page provenance;
35. deterministic repeated run gives identical boundaries/IDs;
36. retry/job/worker identity does not change boundaries/IDs;
37. `contextPrefix` does not contaminate physical source provenance;
38. `documentVersion` remains positive numeric;
39. downstream LogicalBlock mapping contract can consume output without legacy SOURCE/PARENT/SUB DTOs;
40. bounded-memory large-document test.

Real AstraVector tokenizer/truncation and retrieval-quality calibration remain mandatory production evidence under TZ-17/TZ-11 and are not replaced by unit tests.

---

## 14. GO / NO-GO decision

### GO conditions satisfied

- M4/M5 provide stable canonical source/reconciled `DocumentElement[]`.
- TZ-07 clearly owns representation normalization, not semantic rewriting.
- TZ-08 clearly owns structure-first logical fragmentation, not BGE-M3 tokenizer-aware chunking.
- Runtime tokenization remains correctly owned by AstraVector.
- Multilingual RU/KK/EN and mixed content rules are coherent.
- Access-zone-like identifiers and business/legal precision can be protected explicitly.
- Bounded-memory and determinism requirements are defined.
- The three implementation ambiguities identified by this review are resolved above.

### Remaining later gates, not blockers for M6 start

- real AstraVector tokenizer calibration;
- 99.9% downstream truncation gate;
- retrieval-quality comparison against naive splitting;
- full crash/reclaim E2E through production worker orchestration;
- production corpus performance calibration.

These belong to TZ-17/TZ-11 and do not prevent implementing M6.

## Final decision

**M6 implementation is authorized to start.**

Implementation SHALL treat this review as a normative clarification of TZ-07/TZ-08 and SHALL preserve all original acceptance criteria from both specifications.
