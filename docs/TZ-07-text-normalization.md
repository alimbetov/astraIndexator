# TZ-07 — Text Normalization

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-07
- **Title:** Text Normalization
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-03, TZ-04, TZ-05, TZ-06, TZ-08, TZ-09, TZ-11, TZ-13, TZ-14, TZ-16, TZ-17
- **Primary input:** `ParsedDocument` / reconciled native + OCR `DocumentElement[]`
- **Primary output:** normalized canonical `DocumentElement[]` for TZ-08

---

## 2. Purpose

TZ-07 defines the deterministic text-normalization boundary between parsing/OCR and logical fragmentation.

The normalizer exists to remove representation noise that harms structural segmentation and retrieval while preserving semantic content, source language, identifiers, provenance and document structure.

Canonical flow:

```text
TZ-05 native parser
        +
TZ-06 accepted OCR observations
        ↓
reconciled DocumentElement[]
        ↓
TZ-07 deterministic normalization
        ↓
normalized DocumentElement[]
        ↓
TZ-08 Logical Splitter
```

The normalizer is not a translator, spell-checker, summarizer or semantic rewrite engine.

---

## 3. Core principle

The governing rule is:

> Normalize representation; do not normalize meaning.

AstraIndexator MAY canonicalize Unicode representation, whitespace, line-boundary artifacts and demonstrably mechanical parser/OCR artifacts.

AstraIndexator MUST NOT silently alter lexical meaning, legal wording, identifiers, numbers, codes, units, names, language/script or table semantics merely to make text look cleaner.

---

## 4. Core invariants

### NORM-01 — Original evidence is preserved

Normalization SHALL NOT destroy the ability to trace normalized text back to source/native/OCR evidence.

### NORM-02 — Deterministic output

Identical canonical input plus identical normalizer profile/version SHALL produce byte-identical normalized textual output and identical normalization diagnostics.

### NORM-03 — Multilingual safety

Russian, Kazakh, English and mixed-language content SHALL be preserved without transliteration or language-specific destructive substitutions.

### NORM-04 — Unicode canonicalization is representation-only

Unicode normalization MAY canonicalize equivalent code-point sequences but MUST NOT map distinct letters to other letters.

### NORM-05 — Structural elements remain structural

TABLE, LIST, HEADING, CODE_BLOCK, CAPTION and other element semantics SHALL not be flattened merely as a side effect of text cleanup.

### NORM-06 — Numbers and identifiers are protected

Technical identifiers, UUIDs, IP addresses, versions, URLs, emails, monetary values, dates, article numbers, legal clause numbers and codes SHALL not be altered by generic punctuation/whitespace heuristics.

### NORM-07 — OCR repair is conservative

OCR-origin text MAY receive mechanical cleanup only when confidence/evidence is sufficient. Ambiguous lexical corrections are not performed automatically.

### NORM-08 — Page furniture handling is explicit

Repeated headers, footers and page numbers are classified/suppressed through deterministic evidence and retained in provenance/diagnostics rather than silently deleted as arbitrary lines.

### NORM-09 — No translation or transliteration

Normalization SHALL NOT translate or transliterate document content.

### NORM-10 — Normalizer version participates in processing identity

Any semantic-affecting normalization rule/profile change SHALL affect `processingFingerprint` and therefore prepared-artifact/reindex compatibility.

---

## 5. Responsibility boundary

### 5.1 TZ-05 owns

- source-format parsing;
- document structure reconstruction;
- reading order;
- native text elements;
- page-furniture candidate classification;
- tables/lists/images;
- source coordinates/provenance.

### 5.2 TZ-06 owns

- OCR execution;
- OCR block text;
- native/OCR reconciliation;
- OCR confidence/model provenance;
- acceptance/suppression of duplicate OCR observations.

### 5.3 TZ-07 owns

- Unicode canonicalization;
- line ending normalization;
- bounded whitespace cleanup;
- parser/OCR line-break artifacts;
- conservative dehyphenation;
- paragraph-internal line joining where structure proves continuity;
- repeated page-furniture suppression decision;
- punctuation/character representation cleanup when unambiguous;
- normalized-text statistics and diagnostics;
- preservation of protected spans;
- normalized textual representation of canonical elements.

### 5.4 TZ-08 owns

- logical fragment boundaries;
- hierarchy propagation;
- semantic grouping;
- fragment size guards;
- `contextPrefix` construction;
- downstream logical fragmentation.

TZ-07 SHALL NOT perform final semantic chunking.

---

## 6. Canonical text representations

The system SHALL distinguish at least:

```text
source/native/OCR evidence
        ↓
originalText
        ↓
normalizedText
        ↓
TZ-08 fragment originalText
        +
contextPrefix
        ↓
embeddingText
```

Semantics:

### `originalText`

Text produced by the accepted parser/OCR reconciliation result before TZ-07 normalization, preserving the content as recovered from the document.

### `normalizedText`

Deterministically cleaned representation used by TZ-08 and downstream indexing.

### `contextPrefix`

Synthetic hierarchy/context generated later by TZ-08. It is not part of source content.

### `embeddingText`

Downstream materialized text constructed from `contextPrefix` and normalized source text.

TZ-07 MUST NOT conflate these representations.

---

## 7. Normalization profile

Every run SHALL use an explicit profile, for example:

```text
normalizerProfile = multilingual-general-v1
normalizerVersion = text-normalizer-v1
```

The profile MUST define all enabled transformations and safety options.

Conceptual configuration:

```yaml
normalization:
  profile: multilingual-general-v1
  unicode-form: NFC
  normalize-line-endings: true
  collapse-horizontal-whitespace: true
  preserve-paragraph-breaks: true
  max-consecutive-blank-lines: 1
  normalize-nbsp: true
  dehyphenation: conservative
  page-furniture-suppression: evidence-based
  preserve-code-whitespace: true
  preserve-table-cells: true
  transliteration: false
  translation: false
  spell-correction: false
```

Configuration SHALL be startup-validated.

---

## 8. Unicode normalization

Baseline form:

```text
NFC
```

The purpose is canonical representation of canonically equivalent Unicode sequences.

Permitted:

```text
canonically equivalent sequence -> NFC equivalent
```

Forbidden language rewriting examples:

```text
ё -> е        forbidden
і -> i        forbidden
і -> и        forbidden
қ -> к        forbidden
ң -> н        forbidden
ғ -> г        forbidden
ө -> о        forbidden
ұ -> у        forbidden
ү -> у        forbidden
һ -> х        forbidden
ә -> а        forbidden
```

Kazakh Cyrillic characters are semantically distinct and MUST survive byte/content normalization correctly after NFC.

Compatibility normalization such as NFKC/NFKD MUST NOT be used globally without a separately approved profile because it may alter semantically relevant typography/symbols.

---

## 9. Control and invisible characters

The normalizer SHALL handle Unicode control/invisible characters through an explicit allow/remove policy.

Possible categories:

```text
CR/LF/TAB
NBSP
zero-width space
zero-width no-break/BOM
soft hyphen
directionality marks
other C0/C1 controls
```

Rules:

- CRLF/CR may normalize to LF;
- NBSP may normalize to ordinary space in prose when cell/layout semantics do not require distinction;
- accidental zero-width spacing artifacts MAY be removed when they are not part of required script shaping/control semantics;
- directionality controls MUST NOT be blindly removed from RTL text if future language support depends on them;
- unknown control characters SHALL be counted/diagnosed;
- document contents SHALL never be executed/interpreted as control commands.

For v1 RU/KK/EN profiles, any removal policy MUST have golden tests.

---

## 10. Whitespace normalization

For ordinary prose elements, baseline may:

```text
tabs/multiple horizontal spaces -> one space
trim leading/trailing whitespace
excess blank lines -> bounded paragraph separators
CRLF/CR -> LF
```

But normalization is element-type-aware.

The following MUST NOT use generic whitespace collapse:

```text
CODE_BLOCK
preformatted text
layout-sensitive table cell content when spacing is meaningful
ASCII diagrams
```

For those elements, only safe line-ending/Unicode normalization applies unless a dedicated profile exists.

---

## 11. Paragraph boundaries

Paragraph boundaries are structural evidence from TZ-05 and MUST be preserved.

TZ-07 MAY join lines inside one paragraph when line breaks are parser/PDF wrapping artifacts.

Example:

```text
Поставка осуществляется в течение
10 рабочих дней.
```

may become:

```text
Поставка осуществляется в течение 10 рабочих дней.
```

But:

```text
1. Поставка
2. Оплата
```

MUST NOT be joined merely because both lines are short.

The normalizer SHALL rely on element structure and line provenance, not only regex length heuristics.

---

## 12. Conservative dehyphenation

PDF/OCR line wrapping often yields:

```text
информа-
ционная система
```

AstraIndexator MAY reconstruct:

```text
информационная система
```

only under conservative evidence.

Required evidence SHOULD include:

- hyphen at physical line end;
- next line is continuation of the same paragraph/region;
- no structural boundary between lines;
- no list/table/cell boundary;
- resulting merge does not alter a protected identifier;
- profile/language rules permit the operation.

Do NOT automatically remove hyphens from:

```text
по-русски
бизнес-процесс
state-of-the-art
ISO-9001
X-Request-ID
10-20
```

Ambiguous cases remain unchanged.

Each applied dehyphenation SHOULD be countable in diagnostics.

---

## 13. Soft hyphen

Unicode soft hyphen (`U+00AD`) MAY be removed when it is verified as a discretionary rendering artifact.

However:

```text
soft-hyphen removal
!=
ordinary hyphen deletion
```

The rule MUST be separate and testable.

---

## 14. Punctuation normalization

TZ-07 SHOULD be conservative.

Safe examples MAY include normalizing clearly equivalent parser artifacts such as repeated whitespace around punctuation when no protected span is affected.

TZ-07 SHALL NOT globally transform:

```text
“ ” -> "
« » -> "
— -> -
– -> -
… -> ...
```

as a mandatory baseline rule, because these transformations can alter legal/source fidelity and offsets.

Typography may be preserved exactly after Unicode NFC unless a downstream-specific derived representation is introduced later.

---

## 15. Protected spans

Before risky normalization rules, the implementation SHOULD identify protected spans.

Baseline protected categories:

```text
UUID
URL
email
IPv4/IPv6
host/domain
file path
package/class/method identifier
snake_case/camelCase identifier
version number
semantic version
decimal number
percentage
currency amount
date/time
article/clause numbering
access-zone code
hash/digest
phone-like structured values where detected
```

Examples that MUST survive:

```text
CreateMultiGranularityChunks
accessZoneIds
kz.acb.service.PaymentService
10.20.30.40
v1.2.3
1.2.3.
0001
20fd6906-cf10-4d2a-bdbf-31ae32316716
SHA-256
```

Protected-span detection is a safety mechanism, not a semantic entity extractor.

---

## 16. Numbers and legal/business precision

Normalization SHALL NOT alter numerical value or formatting in a way that changes interpretation.

Forbidden examples:

```text
1,25 -> 1.25            forbidden without locale semantics
01.02.2026 -> 2026-02-01 forbidden as source rewrite
1 000 000 -> 1000000    forbidden as source rewrite
№ 15 -> 15              forbidden
15% -> 0.15             forbidden
```

Search-time/query normalization, if needed, belongs elsewhere and must not rewrite source truth.

---

## 17. Multilingual RU/KK/EN handling

The baseline normalizer is language-aware but not language-rewriting.

It MUST preserve code-switching:

```text
Сервис вызывает CreateMultiGranularityChunks через gRPC.
```

and:

```text
Spring Boot сервис SeaweedFS-ке файл жүктейді.
```

Language switching is not a reason to insert/remove boundaries.

`language=und` is valid and uses generic safe rules.

No separate destructive `RussianNormalizer`/`KazakhNormalizer` pipeline is required. Language-specific helpers MAY be used for safe dehyphenation/abbreviation diagnostics.

---

## 18. OCR-origin normalization

OCR text has additional representation noise, but TZ-07 SHALL remain conservative.

Safe candidates:

- surrounding whitespace cleanup;
- line-break joining based on OCR geometry/reading order;
- soft-hyphen/discretionary line-wrap handling;
- removal of duplicate whitespace artifacts;
- page-furniture suppression after repeated-pattern evidence;
- Unicode canonicalization.

Not baseline:

```text
0 <-> O correction
1 <-> l/I correction
rn <-> m correction
word dictionary correction
LLM-based spelling correction
translation-based repair
```

These may alter identifiers, amounts, names or legal language and therefore require a separately versioned optional correction stage if introduced.

Low-confidence OCR text SHALL remain flagged through provenance rather than silently rewritten to a guessed word.

---

## 19. Native/OCR provenance

Normalization output MUST preserve origin metadata such as:

```text
origin = NATIVE | OCR | MERGED
sourceElementId
sourceImageId
pageNumber
bbox/region
ocrEngine
ocrModelId
ocrConfidence
```

If normalization joins several observations, output provenance SHALL be able to reference all contributing source elements/observations.

A normalized string MUST NOT erase the fact that some portion originated from OCR.

---

## 20. Page furniture suppression

Repeated page headers/footers/page numbers are common retrieval noise.

Suppression MAY occur only after deterministic evidence, for example:

```text
same/similar normalized line
+
consistent top/bottom page region
+
repeated across configurable fraction/count of pages
+
not a real section heading/content body
```

Potential outcomes:

```text
KEEP_CONTENT
MARK_HEADER_FOOTER
SUPPRESS_FROM_INDEX_TEXT
KEEP_WITH_WARNING
```

Suppression affects indexed normalized text but MUST preserve source/provenance evidence and diagnostic counts.

One occurrence on one page is not enough to classify arbitrary text as furniture.

---

## 21. Page numbers

Standalone page numbers MAY be classified as page furniture when supported by layout/repetition evidence.

Do not delete numerical lines globally.

For example:

```text
15
```

could be a page number, table value, clause, amount or code depending on structure.

---

## 22. Headers and headings

Document headings are semantic structure and MUST NOT be suppressed merely because they repeat in a table of contents or appear in uppercase.

TZ-05 heading classification remains authoritative structural evidence.

TZ-07 MAY normalize safe whitespace inside a heading but SHALL preserve heading text and hierarchy identity.

---

## 23. Lists

Lists remain structured.

Normalization SHALL preserve:

```text
ordered/unordered semantics
item order
item text
nesting
introductory paragraph/context
```

List markers MAY receive a canonical derived representation only if the original marker remains represented in provenance/structure.

Do not flatten:

```text
1. item A
2. item B
```

into one prose sentence.

---

## 24. Tables

Table normalization is cell-aware.

The normalizer SHALL:

- normalize text inside cells using safe rules;
- preserve rows/columns/cell spans;
- preserve header semantics;
- preserve empty cells when position matters;
- avoid merging text across cell boundaries;
- retain table/cell provenance.

It SHALL NOT treat `|`-delimited serialization as the canonical table model.

A deterministic textual table representation for AstraVector may be materialized later from the structured model, but structure remains canonical.

---

## 25. Code blocks and preformatted content

For `CODE_BLOCK` or equivalent elements:

- preserve indentation;
- preserve line breaks;
- preserve tabs unless profile explicitly converts them deterministically;
- preserve punctuation and case;
- do not dehyphenate;
- do not collapse spaces globally;
- apply only safe Unicode/line-ending rules.

This protects source code, configuration snippets and technical examples.

---

## 26. URLs, paths and identifiers

Do not insert spaces into or remove punctuation from protected technical values.

Examples:

```text
https://nexus.astrabase.asia
/api/v1/retrieve
C:\\data\\file.pdf
kz.acb.service
foo.bar()
X-Request-ID
```

Line-wrap reconstruction MAY join a URL broken by PDF layout only when continuity is unambiguous and source provenance supports it.

---

## 27. Case normalization

Global lowercasing is forbidden for canonical source text.

Reasons:

- identifiers are case-sensitive in some contexts;
- abbreviations/acronyms lose fidelity;
- legal/source citation fidelity is reduced;
- proper names may be affected;
- code/configuration semantics may change.

Any search-specific lowercase representation belongs downstream and SHALL NOT replace canonical normalized text.

---

## 28. Repeated whitespace and empty content

After normalization, an element MAY become empty only when all its content was classified as non-indexable representation noise/page furniture.

Such an element SHOULD be retained structurally with a suppression reason or omitted from index text through an explicit decision, not silently disappear without diagnostics.

Statistics SHALL include:

```text
elements_input
elements_output
elements_suppressed
chars_before
chars_after
blank_lines_removed
whitespace_runs_collapsed
dehyphenations_applied
furniture_suppressed
control_chars_removed
```

---

## 29. Normalization operation audit model

The implementation SHOULD support compact operation summaries rather than storing a full character-by-character diff for every document.

Conceptual:

```json
{
  "normalizerVersion": "text-normalizer-v1",
  "profile": "multilingual-general-v1",
  "operations": {
    "unicodeNfc": 124,
    "whitespaceCollapsed": 381,
    "lineWrapJoins": 62,
    "dehyphenations": 7,
    "pageFurnitureSuppressed": 18
  },
  "warnings": [
    "AMBIGUOUS_HYPHENATION_PRESERVED"
  ]
}
```

For risky/exceptional modifications, element-level reason codes SHOULD be retained.

---

## 30. Deterministic IDs and normalization

Normalization affects downstream element/fragment text hashes.

Therefore the system MUST define which identity is generated before versus after normalization.

Baseline:

```text
source locator + parser reading order
  -> stable source element identity

normalized text + normalizer version/profile
  -> normalized content hash

TZ-08 boundaries + normalized content
  -> fragment identity inputs
```

A normalizer rule change MAY alter downstream fragment IDs and prepared artifacts. This is expected and MUST be visible through `normalizerVersion/profile` in the processing fingerprint.

---

## 31. Streaming and bounded-memory behavior

TZ-07 SHALL support large documents without requiring all normalized textual content to exist in one Python string.

The implementation SHOULD process canonical elements in bounded sequences while retaining only the look-behind/look-ahead needed for:

- line/paragraph continuity;
- page-furniture statistics;
- list/table local structure;
- deterministic context.

Where global document evidence is required (for example repeated header/footer detection), the design MAY use a bounded first-pass statistics structure followed by a second streaming pass.

Do not require one huge `document_text` concatenation.

---

## 32. Two-pass normalization

A practical baseline is:

```text
Pass A — evidence collection
  -> repeated header/footer candidates
  -> page-position frequencies
  -> document-level language hints
  -> normalization warnings/statistics

Pass B — deterministic element normalization
  -> Unicode
  -> safe whitespace/line joins
  -> dehyphenation
  -> furniture suppression
  -> normalized elements
```

This permits global page-furniture decisions without holding the full document text in RAM.

Two-pass behavior MUST remain deterministic.

---

## 33. Failure policy

Most normalization operations should not fail a valid document.

Failure classes:

```text
NORMALIZATION_PROFILE_INVALID
NORMALIZATION_UNSUPPORTED_UNICODE_POLICY
NORMALIZATION_INVARIANT_VIOLATION
NORMALIZATION_RESOURCE_LIMIT
NORMALIZATION_INTERNAL_ERROR
OWNERSHIP_LOST
```

Ambiguous text cleanup SHOULD normally preserve original representation plus warning instead of failing or guessing.

Example:

```text
uncertain dehyphenation
→ KEEP ORIGINAL
→ warning
```

This is preferable to destructive correction.

---

## 34. Retry and recovery

Normalization is a deterministic local stage.

If a worker crashes before prepared artifact publication:

```text
new worker
→ reuse compatible earlier checkpoint if any
→ rerun normalization deterministically
```

If a compatible TZ-03 prepared artifact already contains normalized canonical output under the same processing fingerprint, normalization may be skipped during downstream recovery.

No local temporary normalized file is a cross-worker checkpoint.

Lease/fencing requirements from TZ-02/TZ-13 apply before committing authoritative prepared output.

---

## 35. Security considerations

Normalization SHALL NOT:

- execute macros/scripts;
- resolve external links;
- fetch URLs embedded in text;
- expand arbitrary entity references;
- invoke shell commands;
- use untrusted document text as filesystem path/configuration;
- send document text to an external LLM/spellchecker by default.

Normalization is local deterministic processing of already admitted canonical content.

---

## 36. Observability

Required dimensions SHOULD include:

```text
normalizer_version
normalizer_profile
source_format
origin_type(native/ocr/mixed)
primary_language when safely available
warning_code
operation_type
```

Metrics SHOULD include:

```text
normalization_documents_total
normalization_duration_seconds
normalization_elements_total{type}
normalization_chars_before_total
normalization_chars_after_total
normalization_line_joins_total
normalization_dehyphenations_total
normalization_furniture_suppressed_total
normalization_warnings_total{code}
normalization_failures_total{reason}
```

Logs MUST NOT dump full document content at normal production levels.

---

## 37. RAG-quality considerations

Normalization is successful only if it improves/maintains retrieval quality without corrupting evidence.

Expected benefits:

- fewer false splits from PDF line wrapping;
- fewer duplicated/repeated page headers in index;
- restored words broken by layout hyphenation;
- stable multilingual Unicode;
- cleaner logical fragments;
- better lexical/sparse matching of split words;
- preserved identifiers and numbers for exact-term retrieval.

TZ-17 SHALL compare normalization profiles on a representative corpus rather than assuming more aggressive cleanup is always better.

---

## 38. Golden corpus requirements

At minimum include:

### Russian

- ordinary prose;
- `ё` preserved;
- legal numbering;
- hyphenated words;
- UUIDs/URLs/versions.

### Kazakh

Explicit coverage of:

```text
ә ғ қ ң ө ұ ү һ і
Ә Ғ Қ Ң Ө Ұ Ү Һ І
```

with line-wrap/dehyphenation cases.

### English/technical

- identifiers;
- code/configuration;
- URLs;
- semantic versions;
- hyphenated English compounds.

### Mixed language

RU+KK, RU+EN and RU+KK+EN in one paragraph/document.

### OCR

- low-confidence noisy text;
- word split across lines;
- repeated page furniture;
- ambiguous `0/O`, `1/l` cases that MUST remain unguessed.

### Tables/lists

- empty cells;
- multi-line cells;
- numbered nested lists;
- repeated table headers.

---

## 39. Required TZ-17 verification scenarios

TZ-17 SHALL provide executable evidence for at least:

1. NFC canonical equivalence produces identical normalized output;
2. Kazakh-specific letters survive normalization unchanged;
3. `ё` is not rewritten to `е`;
4. mixed RU/KK/EN sentence remains intact;
5. CRLF/CR line endings normalize deterministically;
6. prose multiple spaces collapse safely;
7. code block indentation is preserved;
8. paragraph line-wrap joins correctly;
9. list items are not joined into prose;
10. valid line-wrap dehyphenation joins a split word;
11. lexical hyphen in `бизнес-процесс` is preserved;
12. `X-Request-ID` is preserved;
13. `v1.2.3` is preserved;
14. `0001` access-zone-like code is preserved with leading zeros;
15. UUID remains unchanged;
16. URL remains unchanged;
17. monetary/numeric formatting is not semantically rewritten;
18. repeated page header is suppressed only after evidence threshold;
19. one-off short line is not falsely removed as page furniture;
20. page number suppression is layout/repetition aware;
21. table cell boundaries are preserved;
22. empty table cells remain structurally meaningful;
23. OCR low-confidence ambiguous characters are not guessed;
24. native/OCR provenance survives normalization;
25. same input/profile produces byte-identical normalized output;
26. normalizer profile change changes processing fingerprint;
27. large document normalization remains bounded-memory;
28. crash/retry reproduces normalized hashes;
29. logs do not expose document content;
30. normalized vs unnormalized RAG benchmark demonstrates no regression beyond approved threshold.

---

## 40. Acceptance criteria

TZ-07 is satisfied when:

- **AC-01:** normalization is explicitly versioned/profiled;
- **AC-02:** NFC is the default Unicode representation policy;
- **AC-03:** distinct Kazakh/Russian letters are never transliterated/substituted;
- **AC-04:** original/source evidence remains traceable after normalization;
- **AC-05:** prose whitespace/line-ending cleanup is deterministic;
- **AC-06:** code/preformatted whitespace is preserved;
- **AC-07:** dehyphenation is conservative and evidence-based;
- **AC-08:** protected identifiers/numbers/URLs/codes remain unchanged;
- **AC-09:** page-furniture suppression is deterministic, layout/repetition based and auditable;
- **AC-10:** TABLE/LIST/CODE structural semantics survive normalization;
- **AC-11:** OCR-origin text keeps OCR provenance/confidence evidence;
- **AC-12:** no lexical spell correction/translation/transliteration occurs in baseline;
- **AC-13:** ambiguity defaults to preservation rather than guessing;
- **AC-14:** normalization can operate bounded-memory on large documents;
- **AC-15:** global evidence needs can be handled with bounded two-pass processing;
- **AC-16:** normalizer version/profile participates in `processingFingerprint`;
- **AC-17:** retries under identical fingerprint reproduce normalized content hashes;
- **AC-18:** observability reports operations/warnings without leaking full content;
- **AC-19:** TZ-08 consumes normalized canonical elements without needing source-format-specific cleanup;
- **AC-20:** TZ-17 contains multilingual, OCR, structural and RAG-quality evidence for the normalization boundary.

---

## 41. Recommended implementation decomposition

```text
TextNormalizationService
NormalizationProfile
UnicodeNormalizer
WhitespaceNormalizer
LineWrapResolver
ConservativeDehyphenator
ProtectedSpanDetector
PageFurnitureClassifier
ElementTextNormalizer
TableCellNormalizer
CodeBlockNormalizer
NormalizationDiagnostics
NormalizationResult
```

These components SHOULD be pure/deterministic where practical so unit/golden testing is straightforward.

---

## 42. Explicit non-goals for AstraIndexator 1.0

TZ-07 does not provide:

- grammar correction;
- spelling correction;
- entity normalization;
- stemming/lemmatization;
- translation;
- transliteration;
- LLM rewriting;
- summarization;
- semantic deduplication across different source passages;
- query-time normalization;
- BGE-M3 token normalization;
- embedding-specific preprocessing owned by AstraVector.

Any future addition that changes semantic source text MUST be introduced as a separate, versioned and benchmarked capability rather than hidden inside generic normalization.

---

## 43. Final invariant

The normalization boundary is:

```text
accepted native/OCR evidence
        ↓
structure-aware deterministic representation cleanup
        ↓
multilingual-safe normalized canonical elements
        ↓
TZ-08 semantic fragmentation
```

It is NOT:

```text
raw text
→ aggressive regex cleanup
→ spelling/translation guesses
→ loss of provenance
```

AstraIndexator SHALL prefer preserving an ambiguous original form over making an irreversible semantic guess.
