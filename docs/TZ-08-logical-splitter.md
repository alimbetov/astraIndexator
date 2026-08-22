# TZ-08 — Multilingual Logical Splitter

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-08
- **Title:** Multilingual Structure-Aware Logical Splitter
- **Status:** Design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-05, TZ-06, TZ-07, TZ-09, TZ-11, TZ-17
- **Primary downstream contract:** AstraVector `CreateMultiGranularityChunks`

---

## 2. Purpose

This specification defines how AstraIndexator converts a parsed multilingual document into stable logical fragments suitable for downstream ingestion by AstraVector.

The Logical Splitter is responsible for preserving document semantics and structure while bounding fragment size. It MUST NOT implement BGE-M3 tokenization, embedding generation, sparse encoding or AstraVector multi-granularity chunk generation.

The target design is:

```text
Document
  -> Parser / OCR
  -> ParsedDocument
  -> normalization
  -> structure reconstruction
  -> atomic semantic units
  -> LogicalFragment[]
  -> AstraVector CreateMultiGranularityChunks(source_text)
  -> SOURCE / PARENT / SUB_180 / SUB_260
```

AstraIndexator owns semantic containerization. AstraVector owns model-aware searchable chunking.

---

## 3. Architectural decision

AstraIndexator SHALL use **multilingual structure-aware, tokenizer-calibrated logical fragmentation**.

The splitter SHALL be:

- structure-first;
- multilingual-safe;
- tokenizer-free at runtime;
- bounded by deterministic character/word/sentence guards;
- calibrated against the real AstraVector tokenizer/model contract during verification;
- compatible with mixed-language documents;
- conservative about raw text overlap;
- provenance-preserving.

The splitter SHALL NOT use a fixed token count as its primary runtime boundary.

---

## 4. Relationship with AstraVector

AstraVector already owns multi-granularity chunk generation. The current downstream integration path uses `CreateMultiGranularityChunks`, which accepts a source text container and creates its internal SOURCE/PARENT/searchable sub-chunk structure.

Therefore AstraIndexator SHALL NOT pre-create `SUB_180`, `SUB_260` or equivalent embedding-sized chunks.

Canonical responsibility boundary:

```text
AstraIndexator
  LogicalFragment (~semantic container)
        |
        v
AstraVector
  SOURCE
    -> PARENT
       -> SUB_180
       -> SUB_260
```

This avoids duplicating model-aware chunking logic across Python and Rust services.

---

## 5. Business/RAG objective

The splitter MUST maximize retrievability while minimizing semantic rupture.

A good logical fragment SHALL:

1. represent one coherent local topic or document unit;
2. preserve headings and hierarchy needed to understand the content;
3. avoid joining unrelated sections merely to reach a target size;
4. avoid splitting clauses/lists/tables in a way that removes their governing context;
5. remain small enough that AstraVector can safely perform its downstream parent/sub-chunk generation;
6. remain stable enough for repeatable reindexing and deterministic IDs;
7. preserve original source provenance for citation and diagnostics.

---

## 6. Canonical input

The splitter consumes a canonical `ParsedDocument` produced by TZ-05/TZ-06/TZ-07.

Conceptual hierarchy:

```text
ParsedDocument
  metadata
  document language hints
  elements[]
    HEADING
    PARAGRAPH
    LIST
    LIST_ITEM
    TABLE
    IMAGE
    OCR_TEXT
    CAPTION
    CODE_BLOCK
    PAGE_BREAK
    OTHER
```

Each element SHOULD contain order/provenance fields sufficient to reconstruct reading order and origin.

The splitter MUST NOT depend directly on PDF/DOCX/PPTX implementation details after canonical parsing.

---

## 7. Canonical output — LogicalFragment

Conceptual DTO:

```json
{
  "fragmentId": "DOC-100:F-000042",
  "documentId": "DOC-100",
  "documentVersion": "3",
  "sequence": 42,
  "fragmentType": "SECTION",
  "language": {
    "primary": "kk",
    "detected": ["kk", "ru"],
    "mixed": true
  },
  "hierarchy": [
    "3. Жеткізу",
    "3.2 Жеткізу мерзімі"
  ],
  "text": "Тауарды жеткізу мерзімі...",
  "contextPrefix": "3. Жеткізу\n3.2 Жеткізу мерзімі",
  "source": {
    "pageFrom": 14,
    "pageTo": 15,
    "elementFrom": "el-213",
    "elementTo": "el-228"
  },
  "statistics": {
    "charCount": 5318,
    "wordCount": 734,
    "sentenceCount": 29
  },
  "split": {
    "reason": "SECTION_BOUNDARY",
    "forced": false,
    "profile": "multilingual-general-v1",
    "splitterVersion": "logical-v1"
  }
}
```

The exact canonical schema is finalized in TZ-09, but these semantics are mandatory for TZ-08.

---

## 8. Original text vs embedding text

The system SHALL distinguish original content from contextualized downstream embedding input.

```text
originalText = exact normalized source content
contextPrefix = selected structural context
embeddingText = contextPrefix + separator + originalText
```

Example:

```text
contextPrefix:
3. Поставка
3.2 Сроки поставки

originalText:
Поставка осуществляется в течение 10 рабочих дней.
```

Materialized downstream text:

```text
3. Поставка
3.2 Сроки поставки

Поставка осуществляется в течение 10 рабочих дней.
```

Citation/provenance MUST refer to original source content, not to synthetic contextual text as if it were physically present in the source.

---

## 9. Multilingual design principles

### 9.1 One splitter, not one implementation per language

AstraIndexator SHALL NOT maintain separate full splitter implementations such as `RussianSplitter`, `KazakhSplitter`, `EnglishSplitter`.

The canonical implementation SHALL use one generic structural algorithm with language-aware helpers for:

- sentence boundary detection;
- abbreviations;
- punctuation handling;
- Unicode/script metadata;
- optional size calibration coefficients;
- OCR/language diagnostics.

### 9.2 Language is a signal, not a partition key

A change of language MUST NOT automatically create a fragment boundary.

Mixed content such as:

```text
Сервис вызывает CreateMultiGranularityChunks через gRPC.
```

or:

```text
Spring Boot сервис SeaweedFS-ке файл жүктейді.
```

is a normal multilingual technical sentence and SHALL remain intact when structurally coherent.

### 9.3 Unknown language is valid

Language detection is advisory.

If language cannot be determined reliably:

```text
language = und
```

The document SHALL continue through a generic punctuation/paragraph/structure splitter profile.

Failure to identify language MUST NOT by itself fail indexing.

### 9.4 No automatic translation

AstraIndexator 1.0 SHALL NOT translate content before embedding.

Original Russian, Kazakh, English, German or mixed content SHALL be delivered in its original language to AstraVector.

Translation would introduce latency, semantic drift and provenance ambiguity and is outside this specification.

---

## 10. Language metadata

Language information MAY be retained at document, fragment and optionally element level.

Recommended fragment metadata:

```json
{
  "primaryLanguage": "ru",
  "languages": ["ru", "kk", "en"],
  "mixed": true,
  "script": "CYRILLIC"
}
```

Language metadata MUST NOT change access-zone or document identity semantics.

Language detection confidence MAY be stored for diagnostics but SHALL NOT be required for downstream ingestion.

---

## 11. Unicode and multilingual normalization

The normalization pipeline SHALL preserve lexical distinctions across supported languages.

Recommended canonical Unicode normalization: `NFC`.

The following aggressive transformations are prohibited unless separately configured and proven safe:

```text
ё -> е
і -> i
қ -> к
ө -> о
ү -> у
```

General rule:

```text
normalize representation != normalize language
```

Transliteration SHALL be disabled by default.

---

## 12. Structure-first boundary model

Boundary quality SHALL follow a hierarchy similar to:

```text
CHAPTER
SECTION
SUBSECTION
ARTICLE
CLAUSE
PARAGRAPH
LIST_GROUP
SENTENCE
FORCED_SPLIT
```

Representative boundary scores:

```text
CHAPTER_END       100
SECTION_END        95
SUBSECTION_END     90
ARTICLE_END        85
CLAUSE_END         80
PARAGRAPH_END      70
LIST_GROUP_END     65
SENTENCE_END       50
FORCED_SPLIT        0
```

Exact weights are implementation parameters, but higher-order document structure MUST outrank sentence-level fallback boundaries.

---

## 13. Size model

Logical fragmentation SHALL use multiple size signals rather than a tokenizer.

Primary runtime guards:

- characters;
- words;
- sentences;
- structural element count where useful.

Initial multilingual baseline:

```yaml
logical_fragmentation:
  size:
    min_chars: 800
    target_chars: 5000
    soft_max_chars: 8000
    hard_max_chars: 12000

    target_words: 700
    soft_max_words: 1200
    hard_max_words: 1800

    target_sentences: 25
    hard_max_sentences: 80
```

These values are a calibration baseline, not a permanent model contract.

---

## 14. Target / soft max / hard max semantics

### 14.1 Target

Around the target size, the splitter SHOULD emit at a strong semantic boundary if one is available.

### 14.2 Soft maximum

After `soft_max_*` is reached, the splitter SHOULD actively choose the best available safe boundary rather than continue accumulating content without reason.

### 14.3 Hard maximum

At `hard_max_*`, the fragment MUST be split even if an ideal structural boundary is unavailable.

Fallback priority:

```text
paragraph
list item group
sentence
forced textual boundary as last resort
```

Forced splits MUST be explicitly marked in fragment metadata.

---

## 15. Why AstraIndexator does not target AstraVector's model maximum

The Logical Splitter MUST NOT aim to fill the maximum model context window.

The downstream model limit is a safety ceiling, not a desirable RAG fragment size.

Logical fragments SHOULD generally remain large enough to preserve local semantics yet small enough for AstraVector to create multiple internal PARENT/SUB granularities.

The initial design target is a typical logical fragment in the approximate range of 800–2000 downstream model tokens after calibration, without runtime tokenization inside AstraIndexator.

---

## 16. Tokenizer-calibrated but tokenizer-free runtime

AstraIndexator MUST NOT load the BGE-M3 tokenizer in production merely to make logical split decisions.

Instead, verification SHALL calibrate the deterministic size guards against the real AstraVector runtime.

Calibration procedure:

1. assemble representative corpus;
2. run AstraIndexator logical fragmentation;
3. submit fragment embedding text to AstraVector preview/diagnostic tokenization capability;
4. collect actual model token counts and truncation flags;
5. calculate language/script percentile distributions;
6. adjust char/word guards;
7. freeze a versioned splitter profile.

This creates model-aware limits without duplicating model/runtime ownership.

---

## 17. Required calibration corpus

The verification corpus SHOULD include at minimum:

- Russian documents;
- Kazakh documents;
- English documents;
- Russian + Kazakh bilingual documents;
- Russian + English technical documents;
- mixed Russian/Kazakh/English documents;
- OCR-derived documents;
- legal/contract documents;
- long technical manuals;
- large tables/lists where supported.

Recommended initial evidence target:

```text
>= 500 representative fragments per major language/content category
```

Larger corpora are preferred for production calibration.

---

## 18. Calibration acceptance thresholds

A normal logical fragment SHOULD not be truncated by AstraVector.

Recommended baseline quality gate:

```text
truncated == false for >= 99.9% normal calibrated fragments
```

Any intentional exceptional oversized structure MUST be identifiable and separately tested.

Metrics SHALL include at least:

- char count distribution;
- word count distribution;
- model token count distribution;
- P50/P95/P99/P99.9 token ratios;
- truncation count;
- forced split rate;
- fragments below minimum threshold;
- fragments above soft threshold;
- per-language/script distributions.

---

## 19. Structural context retention

A heading SHOULD remain attached to its body.

Incorrect:

```text
Fragment A: "3. Ответственность сторон"
Fragment B: "3.1 Поставщик обязан..."
```

Preferred:

```text
Fragment:
3. Ответственность сторон
3.1 Поставщик обязан...
```

Heading-only fragments are allowed only when the heading itself is meaningful content and a downstream rule explicitly supports it.

---

## 20. Structural overlap policy

Blind sliding-window overlap SHALL be disabled by default.

Default:

```yaml
overlap:
  raw_text: false
  repeat_heading_context: true
  repeat_parent_headings: true
  repeat_table_header: true
  repeat_list_intro: true
```

Rationale: AstraVector already creates multiple searchable granularities. Large raw overlap in AstraIndexator would multiply near-duplicate vector candidates, storage and retrieval noise.

Only context needed to understand a fragment SHOULD be repeated.

---

## 21. Lists

Short and medium lists SHOULD remain intact with their governing introduction.

Example:

```text
Участник обязан:
1. предоставить документы;
2. пройти проверку;
3. подписать соглашение.
```

The list introduction SHALL not be separated from list items unless required by size constraints.

For oversized lists, each continuation fragment SHOULD repeat the list introduction or an equivalent structural context prefix.

---

## 22. Tables

Tables MUST NOT be treated as ordinary paragraph text.

If a table fits safely within one logical fragment, keep it intact.

For oversized tables, split by row groups while preserving:

- table title/caption;
- column headers;
- source range;
- row continuity metadata.

Each continuation fragment SHOULD repeat the table header context.

A table row SHALL not be split across fragments unless the source format itself represents it as independent nested content and there is no safer alternative.

---

## 23. Images and OCR-derived text

OCR-derived content SHALL remain linked to the originating image/page/region.

If OCR text belongs to a coherent surrounding section, it MAY be merged into the same logical fragment when reading order and semantics justify this.

The splitter MUST avoid duplicating content where native text and OCR represent the same physical page region.

Image OCR text SHOULD carry at least:

- origin element ID;
- page/slide reference;
- OCR extraction method/model metadata inherited from parser/OCR stages.

---

## 24. Bilingual/multicolumn documents

Language detection MUST NOT substitute for layout reconstruction.

For a page with parallel columns, e.g. Russian on the left and Kazakh on the right, parsing SHALL preserve column reading order before logical splitting.

Incorrect reading order:

```text
RU line 1
KK line 1
RU line 2
KK line 2
```

Preferred canonical structure:

```text
column A -> RU content
column B -> KK content
```

Parallel language versions SHOULD remain separate logical fragments when they are separate source regions.

A future `translationGroupId` MAY link parallel translations, but automatic semantic deduplication/translation is out of scope for 1.0.

---

## 25. Technical tokens and protected spans

Sentence splitting MUST avoid breaking technical identifiers solely because they contain punctuation.

Protected spans SHOULD include patterns such as:

- URLs;
- email addresses;
- IP addresses;
- semantic versions;
- decimal values;
- Java/package names;
- method/function identifiers;
- filesystem paths;
- configuration keys;
- common abbreviations.

Examples that SHOULD remain intact:

```text
CreateMultiGranularityChunks
accessZoneIds
kz.acb.service
10.20.30.40
v1.2.3
foo.bar()
```

---

## 26. Sentence segmentation role

Sentence segmentation is a fallback boundary mechanism, not the primary document segmentation strategy.

Priority order:

```text
1. document hierarchy boundary
2. paragraph/list/table boundary
3. language-aware sentence boundary
4. forced size split
```

This reduces language-specific complexity and makes structural extraction quality more important than sentence tokenizer sophistication.

---

## 27. Language/script-specific calibration coefficients

AstraIndexator MAY apply versioned safety coefficients after real calibration demonstrates a need.

Conceptual example:

```yaml
calibration:
  default:
    hard_max_chars: 12000
  cyrl:
    hard_max_chars: 11000
  latin:
    hard_max_chars: 13000
  cjk:
    hard_max_chars: 7000
```

These values MUST be evidence-backed before production use.

The system SHALL NOT hardcode unsupported assumptions merely because a language uses a given script.

---

## 28. Fragment merge rules

The splitter SHOULD avoid very small fragments.

Baseline minimum:

```text
min_chars ~= 800
```

If a logical section is below the minimum, it MAY be merged with a compatible adjacent section when doing so does not cross a strong topic boundary.

Potential exceptions that MAY remain small:

- legal definition;
- short clause;
- FAQ pair;
- compact table;
- standalone warning;
- semantically complete list.

Small-fragment exceptions MUST be classified rather than silently merged indiscriminately.

---

## 29. Fragment determinism

Given identical:

- source content;
- canonical ParsedDocument;
- normalization version;
- splitter profile/version;

the splitter SHOULD produce identical logical boundaries and deterministic fragment identities.

Runtime load, worker replica identity and processing attempt MUST NOT change fragmentation results.

This is required for reproducible reindexing and downstream idempotency.

---

## 30. Fragment identity

Recommended conceptual identity basis:

```text
fragmentIdentityInput =
  documentId
  + documentVersion
  + source element range
  + normalized fragment content hash
  + splitterVersion
```

The exact deterministic ID algorithm belongs to TZ-09/TZ-11.

AstraVector ingestion idempotency MUST distinguish separate logical fragments of the same document/version.

---

## 31. Downstream AstraVector mapping

One logical fragment is the preferred unit for one AstraVector multi-granularity source-group ingestion operation unless TZ-11 proves a better batching contract.

Conceptual mapping:

```text
LogicalFragment
  documentId
  documentVersion
  fragmentId
  embeddingText
  hierarchy/provenance metadata
        |
        v
CreateMultiGranularityChunks
```

Recommended downstream metadata includes:

- `logicalFragmentId`;
- `pageFrom` / `pageTo` or equivalent source range;
- hierarchy path;
- language metadata;
- splitter version/profile;
- extraction/provenance reference.

The downstream idempotency key SHOULD incorporate the logical fragment identity.

---

## 32. Important AstraVector contract concern

Multiple logical fragments may belong to the same `documentId + documentVersion`.

TZ-11 MUST formally verify that AstraVector can create multiple independent source/root groups for one document version without collisions.

AstraIndexator SHALL NOT rely solely on free-form metadata for uniqueness.

At least one deterministic fragment-scoped identity mechanism is required, for example:

```text
idempotencyKey = hash(
  documentId
  + documentVersion
  + fragmentId
  + splitterVersion
)
```

If AstraVector requires an explicit `source_fragment_id`/equivalent contract extension, that change must be documented in TZ-11 and coordinated with `alimbetov/llm2`.

---

## 33. Profiles

Initial splitter profiles SHOULD include:

### 33.1 `multilingual-general-v1`

General prose, mixed business documents, basic structured documents.

### 33.2 `legal-v1`

Prioritize:

```text
chapter -> article -> clause -> subclause
```

Avoid combining separate legal articles merely to fill size target.

### 33.3 `technical-v1`

Prioritize headings, procedures, lists, code/config blocks and protected technical spans.

### 33.4 `ocr-layout-v1`

More conservative handling of page/layout boundaries and OCR confidence/provenance.

### 33.5 `table-aware-v1`

Explicit row-group/table-header handling for tabular-heavy source documents.

Profiles SHALL reuse the same core algorithm rather than fork into unrelated implementations.

---

## 34. Baseline configuration

```yaml
logical_fragmentation:
  profile: multilingual-general-v1

  language_detection:
    enabled: true
    required: false
    allow_mixed: true
    fallback: und

  size:
    min_chars: 800
    target_chars: 5000
    soft_max_chars: 8000
    hard_max_chars: 12000
    target_words: 700
    soft_max_words: 1200
    hard_max_words: 1800
    target_sentences: 25
    hard_max_sentences: 80

  boundaries:
    priority:
      - CHAPTER
      - SECTION
      - SUBSECTION
      - ARTICLE
      - CLAUSE
      - PARAGRAPH
      - LIST_GROUP
      - SENTENCE

  overlap:
    raw_text: false
    repeat_heading_context: true
    repeat_parent_headings: true
    repeat_table_header: true
    repeat_list_intro: true

  preservation:
    heading_with_body: true
    short_lists: true
    table_semantics: true
    bilingual_text: true
    technical_tokens: true
    provenance: true

  normalization:
    unicode: NFC
    transliteration: false
    translation: false
```

Production values MUST be versioned and evidence-backed.

---

## 35. Observability requirements

The splitter SHALL emit metrics sufficient to detect quality regressions without logging document content.

Recommended metrics:

- fragments per document;
- average/P50/P95/P99 fragment chars;
- average/P50/P95/P99 fragment words;
- forced split count/rate;
- small-fragment count/rate;
- fragments above soft max;
- detected mixed-language document count;
- unknown-language count;
- boundary reason distribution;
- table split count;
- OCR fragment count.

Document text MUST NOT be emitted as metric labels.

---

## 36. Error and fallback behavior

Logical fragmentation SHOULD fail only when no safe canonical document representation can be produced.

Recoverable conditions SHOULD use deterministic fallback behavior:

- unknown language -> generic structural splitter;
- no headings -> paragraph/sentence structure;
- malformed numbering -> ignore numbering signal;
- oversized paragraph -> sentence fallback;
- sentence segmentation failure -> forced bounded split with provenance flag.

A forced split is not automatically a job failure.

---

## 37. Performance requirements

The splitter SHOULD operate in bounded memory relative to document size.

Large documents SHOULD be processable incrementally once upstream canonical order/structure is available.

The splitter MUST NOT require loading a BGE-M3 model/tokenizer into AstraIndexator runtime.

Language detection and sentence segmentation components SHOULD be lightweight enough not to dominate parsing/OCR cost.

---

## 38. Test matrix

Mandatory tests include:

1. RU monolingual prose;
2. KK monolingual prose;
3. EN monolingual prose;
4. RU+KK mixed paragraph;
5. RU+EN technical paragraph;
6. bilingual parallel-column layout input from parser;
7. legal article/clause hierarchy;
8. heading + body preservation;
9. large paragraph forced split;
10. short fragment merge;
11. short fragment exception;
12. short list preservation;
13. oversized list continuation context;
14. compact table preservation;
15. oversized table row grouping/header repetition;
16. OCR-derived fragment provenance;
17. native/OCR duplicate suppression input;
18. technical protected spans;
19. unknown-language fallback;
20. deterministic repeated run;
21. downstream AstraVector calibration against actual token counts;
22. truncation gate;
23. mixed-language retrieval quality fixtures through AstraVector.

---

## 39. RAG quality verification

Splitter acceptance MUST include retrieval-quality evidence, not only unit tests.

A representative benchmark SHOULD compare at least:

- current baseline/no logical fragmentation;
- naive fixed character splitting;
- structure-aware logical fragmentation.

Metrics SHOULD include:

- target fragment retrieval hit rate;
- MRR/nDCG or existing AstraVector quality metrics where available;
- hard-negative behavior;
- duplicate/near-duplicate top-k rate;
- parent-context usefulness;
- citation/source-boundary quality;
- cross-language query/document retrieval scenarios.

The splitter SHALL NOT be considered production-calibrated merely because all fragments fit under a size limit.

---

## 40. Acceptance criteria

TZ-08 implementation is accepted when all of the following are proven:

### AC-01 — Responsibility boundary
AstraIndexator performs logical fragmentation only; BGE-M3 tokenization and AstraVector multi-granularity chunking remain downstream.

### AC-02 — Structure-first splitting
Strong document boundaries are preferred over fixed-size cuts.

### AC-03 — Bounded fragments
No normal fragment exceeds configured hard guards without explicit exceptional handling.

### AC-04 — No blind raw overlap
Default fragmentation does not use arbitrary sliding-window text overlap.

### AC-05 — Context preservation
Headings, table headers and list introductions are retained/repeated where required to preserve meaning.

### AC-06 — Mixed-language safety
Language switching alone does not force fragmentation.

### AC-07 — Unknown-language robustness
Unknown language does not fail indexing.

### AC-08 — No translation/transliteration drift
Original language content is preserved.

### AC-09 — Unicode safety
Kazakh/Russian/other lexical distinctions survive normalization.

### AC-10 — Layout dependency
Parallel-column bilingual content can be fragmented correctly when parser-provided layout order is correct.

### AC-11 — Determinism
Identical canonical input and splitter version produce identical boundaries/IDs.

### AC-12 — Forced split visibility
Forced fallback boundaries are explicitly marked and measurable.

### AC-13 — Table/list semantics
Large tables/lists are split with required structural context retained.

### AC-14 — OCR provenance
OCR-derived text remains traceable to image/page source.

### AC-15 — Tokenizer-free runtime
AstraIndexator runtime does not require AstraVector/BGE-M3 tokenizer loading for fragmentation.

### AC-16 — Real tokenizer calibration
A representative corpus is evaluated using the actual AstraVector tokenizer/model diagnostic path.

### AC-17 — Truncation gate
At least 99.9% of normal calibrated fragments report no downstream model truncation, or a stricter threshold is documented.

### AC-18 — Downstream identity safety
Multiple logical fragments for one document version can be ingested idempotently without source-group collisions.

### AC-19 — Retrieval quality
Structure-aware fragmentation demonstrates no unacceptable regression and preferably measurable improvement over naive fixed-size splitting on AstraVector quality fixtures.

### AC-20 — Multilingual quality evidence
RU, KK, EN and mixed-language retrieval scenarios are represented in verification evidence.

---

## 41. Required implementation evidence

Before production readiness, the repository SHOULD contain:

- splitter configuration schema;
- canonical fragment DTO tests;
- boundary algorithm tests;
- multilingual fixtures;
- legal/technical/table/OCR fixtures;
- deterministic golden tests;
- calibration corpus tooling;
- AstraVector token-count/truncation calibration report;
- retrieval quality comparison report;
- forced split/fragment distribution report;
- documented final production profile values and version.

---

## 42. Out of scope

TZ-08 does not own:

- PDF/DOCX/PPTX binary parsing;
- OCR model execution;
- automatic machine translation;
- semantic embedding-based topic segmentation;
- BGE-M3 tokenization;
- dense/sparse embedding generation;
- AstraVector PARENT/SUB chunk algorithm;
- Qdrant indexing;
- retrieval/ranking logic;
- document version lifecycle.

---

## 43. Final architecture invariant

The canonical AstraIndexator/AstraVector boundary is:

```text
AstraIndexator
  preserves document meaning and structure
  -> LogicalFragment[]

AstraVector
  optimizes model/search granularity
  -> SOURCE / PARENT / SUB_* / embeddings / retrieval
```

AstraIndexator SHALL optimize for semantic continuity and reproducibility. AstraVector SHALL optimize for embedding and retrieval granularity. Neither service should duplicate the other's responsibility.
