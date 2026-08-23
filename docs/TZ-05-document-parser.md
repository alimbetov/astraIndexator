# TZ-05 — Document Parser

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-05
- **Title:** Document Parser
- **Status:** Consolidated implementation baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-03, TZ-04, TZ-04A, TZ-06, TZ-07, TZ-08, TZ-09, TZ-13, TZ-14, TZ-15, TZ-16, TZ-17, TZ-18
- **Input boundary:** TZ-04/TZ-04A `AcquiredSource`
- **Output boundary:** TZ-09 `ParsedDocument` / `DocumentElement[]`
- **Implementation milestone:** M4 — Canonical Parser
- **Reference implementation source for ideas:** `alimbetov/llm-indexator` (non-authoritative)

---

## 2. Purpose

TZ-05 defines the parsing layer that converts a validated source file into a structured, provenance-preserving canonical document representation suitable for OCR enrichment, normalization and logical fragmentation.

The parser SHALL be a **structure reconstruction layer**, not a flat text extractor.

Canonical flow:

```text
AcquiredSource
    ↓
FileTypeHandlerRegistry
    ↓
format-specific parser
    ↓
structure/layout reconstruction
    ↓
reading-order resolution
    ↓
ParsedDocument + DocumentElement[]
    ↓
TZ-06 OCR enrichment where required
    ↓
TZ-07 normalization
    ↓
TZ-08 logical fragmentation
```

The parser MUST preserve sufficient source structure and provenance so downstream stages do not need to reopen the original source merely to reconstruct basic document organization.

---

## 3. What is inherited from llm-indexator and what is not

`alimbetov/llm-indexator` is used as a source of practical lessons, not as the normative contract.

Useful concepts retained/adapted:

- versioned parser identity;
- low-signal / OCR-required diagnostics;
- safe transport/original filename separation;
- table-aware extraction ideas;
- explicit parser quality diagnostics;
- OCR as a separate subsystem;
- bounded processing and real-document smoke verification.

The following old architecture is NOT transferred into AstraIndexator:

- direct embedding generation inside Indexator;
- local Qdrant/vector ownership;
- tokenizer-aware final chunking;
- flat `ExtractedPage(text)` as the canonical parser contract;
- implicit archive ingestion as a normal document type;
- parser selection based solely on file extension.

AstraVector remains the owner of tokenizer/model/vector logic according to TZ-11.

---

## 4. Architectural invariants

### DP-01 — Structured output

Parser output SHALL preserve semantic/layout elements such as headings, paragraphs, lists, tables, images, captions and page/section boundaries when available.

### DP-02 — Explicit reading order

Every emitted element SHALL participate in deterministic canonical reading order.

### DP-03 — Provenance preservation

Where the source format exposes page/slide/sheet/object/paragraph coordinates, the parser SHALL retain them.

### DP-04 — Native-first extraction

Trustworthy native text is preferred over OCR. OCR is requested only according to TZ-06.

### DP-05 — No fabricated text

The parser SHALL NOT invent text for unreadable or graphical regions. Such content becomes image/opaque/OCR-candidate evidence.

### DP-06 — Images are first-class

Embedded images are represented as `IMAGE` elements even when no OCR is performed.

### DP-07 — Pluggable handlers

Each source format is handled behind a common parser port.

### DP-08 — Determinism

Same source bytes + same parser/profile version SHALL reproduce equivalent structure and ordering.

### DP-09 — Bounded execution

Page count, element count, extracted chars, tables, images, parser runtime and in-memory element buffers SHALL be bounded by configuration.

### DP-10 — No embedding chunking

The parser SHALL NOT use BGE-M3 tokenizer or generate AstraVector-owned chunk classes.

### DP-11 — Partial quality is explicit

If parsing succeeds structurally but text signal is insufficient, the result SHALL record a quality state and OCR candidates rather than pretending extraction is complete.

### DP-12 — `detectedFormat` is authoritative

TZ-05 SHALL NOT re-resolve type from file extension or producer-declared MIME. TZ-04/TZ-04A already admitted one canonical `detectedFormat`.

### DP-13 — Policy, not magic constants

Layout/quality thresholds SHALL be versioned, typed configuration. Numeric defaults are implementation defaults and MUST be backed by golden-corpus verification before being treated as production baselines.

### DP-14 — Large documents remain bounded

Parser implementations SHALL support incremental page/section emission and SHALL NOT require the full `DocumentElement[]` population to be materialized in memory before progress can be checkpointed/spooled.

---

## 5. Responsibility boundary

### 5.1 TZ-05 owns

- selecting a format handler from `AcquiredSource.detectedFormat`;
- native text extraction;
- structural reconstruction;
- reading-order determination;
- heading/list/table/image/caption recognition;
- source coordinates and provenance;
- page-level PDF mode signals;
- OCR-candidate generation;
- parser quality diagnostics;
- parser warnings/errors;
- incremental parser emission;
- `ParsedDocument` production.

### 5.2 TZ-04 / TZ-04A owns

- source admission;
- safe local source;
- SHA-256;
- MIME/signature validation;
- container/image safety preflight;
- bounded workspace/capacity/timeouts.

### 5.3 TZ-06 owns

- OCR policy and decision thresholds;
- OCR model lifecycle;
- model loading from approved internal supply;
- OCR execution and confidence;
- OCR page/image rendering policy;
- OCR result enrichment.

### 5.4 TZ-07 owns

- Unicode/whitespace normalization;
- conservative text cleanup;
- native/OCR overlap deduplication;
- repeated-noise suppression according to deterministic rules.

### 5.5 TZ-08 owns

- multilingual logical fragmentation.

### 5.6 TZ-09 owns

- canonical DTO/schema and identity semantics.

---

## 6. Parser architecture and handler resolution

Required structure:

```text
DocumentParserService
    ↓
FileTypeHandlerRegistry
    ├── PDF      -> PdfDocumentHandler
    ├── DOCX     -> DocxDocumentHandler
    ├── TXT      -> TextDocumentHandler
    ├── MARKDOWN -> MarkdownDocumentHandler
    ├── JPEG     -> ImageDocumentHandler
    ├── PNG      -> ImageDocumentHandler
    └── TIFF     -> ImageDocumentHandler
```

Conceptual interface:

```python
class DocumentHandler(Protocol):
    supported_formats: frozenset[DetectedFormat]

    def parse(
        self,
        source: AcquiredSource,
        context: ParseContext,
        sink: ElementSink,
    ) -> ParseSummary: ...
```

Normative resolution algorithm:

```text
1. read source.detectedFormat only;
2. lookup exact canonical format in registry;
3. require exactly one registered owner for that format;
4. zero matches -> PARSER_UNSUPPORTED_FORMAT;
5. more than one match -> PARSER_CONFIGURATION_ERROR;
6. no extension/MIME fallback is allowed inside TZ-05.
```

Registry requirements:

- registration SHALL be explicit at startup;
- duplicate format ownership SHALL fail startup/readiness;
- handlers MAY support multiple canonical formats only where implementation semantics are intentionally shared, e.g. image shell handler;
- third-party library DTOs SHALL not escape into AstraIndexator domain objects;
- parser implementation/version/profile SHALL be recorded in provenance;
- parser adapters SHALL be replaceable without changing TZ-09 canonical semantics.

Custom handler plugins are outside 1.0 dynamic runtime scope. Adding a handler is an application build/configuration change, not arbitrary runtime code loading.

---

## 7. ParseContext and typed parser policy

Conceptual context:

```json
{
  "jobId": "...",
  "processingAttemptId": "...",
  "documentId": "...",
  "documentVersion": 3,
  "sourceSha256": "...",
  "parserProfile": "default-v1",
  "readingOrderProfile": "reading-order-v1",
  "layoutProfile": "layout-v1",
  "qualityProfile": "quality-v1",
  "limits": {
    "maxPages": 5000,
    "maxElements": 200000,
    "maxExtractedChars": 50000000,
    "maxEmbeddedImages": 10000,
    "maxTables": 10000,
    "maxBufferedElements": 10000,
    "maxParserSeconds": 1800
  }
}
```

Values above are illustrative defaults only. Production values SHALL be typed configuration, startup-validated and included in parser provenance/fingerprint where they can alter canonical output.

Configuration categories:

```text
safety/resource limits          -> output-independent where possible
layout heuristics               -> output-affecting, versioned
reading-order heuristics        -> output-affecting, versioned
quality/OCR-candidate thresholds-> output-affecting, versioned
library/backend selection       -> provenance + fingerprint when output may change
```

---

## 8. Canonical parser output

TZ-05 SHALL produce a `ParsedDocument` conforming to TZ-09.

Conceptual summary:

```json
{
  "schemaVersion": "astra-indexator-document-v1",
  "documentId": "...",
  "documentVersion": 3,
  "sourceSha256": "...",
  "detectedFormat": "PDF",
  "parser": {
    "name": "pdf-parser",
    "version": "1.0.0",
    "profile": "default-v1",
    "readingOrderVersion": "reading-order-v1",
    "layoutVersion": "layout-v1"
  },
  "elements": [],
  "quality": {
    "status": "GOOD",
    "nativeTextChars": 28411,
    "ocrCandidateCount": 2,
    "warnings": []
  }
}
```

Exact DTO field ownership remains governed by TZ-09. TZ-05 SHALL NOT add a competing canonical schema.

For very large documents, the implementation MAY spool canonical element records incrementally while the final `ParsedDocument` carries collection descriptors/counts rather than requiring every element object in one Python list. TZ-09/TZ-03 prepared artifact semantics remain authoritative for durable representation.

---

## 9. DocumentElement baseline

Parser-produced types:

```text
HEADING
PARAGRAPH
LIST
LIST_ITEM
TABLE
IMAGE
CAPTION
CODE_BLOCK
PAGE_BREAK
OTHER
```

`OCR_TEXT` is produced/enriched by TZ-06.

Each element SHALL retain where meaningful:

```text
elementId
parentElementId
type
orderIndex
originalText or parser-native text evidence
level
pageNumber / slideNumber / sheetName
bbox
pageWidth/pageHeight when visual
sectionPath
sourceLocator
styleHints
languageHint optional
role optional
confidence optional
bounded metadata
```

`elementId` and `orderIndex` SHALL be deterministic for equivalent canonical output under the same parser profile. They are not AstraVector chunk IDs.

---

## 10. Reading-order contract

Reading order is a first-class quality requirement.

The parser SHALL distinguish:

```text
physical object order
visual order
logical reading order
```

Canonical `orderIndex` represents logical reading order.

### 10.1 Reading-order v1 algorithm

For each visual page:

```text
1. collect text/layout blocks with normalized bbox;
2. exclude blocks already owned by table-cell substructure from page-flow sorting;
3. form horizontal overlap groups;
4. detect candidate columns from persistent vertical whitespace corridors and bbox x-clusters;
5. classify full-width blocks whose horizontal span crosses column boundaries;
6. split page into ordered vertical bands separated by full-width anchors;
7. within each band:
   a. if one column -> sort top-to-bottom, then x as deterministic tie-break;
   b. if multiple independent columns -> sort columns by x0, then each column top-to-bottom;
8. append anchored captions/images/tables according to their spatial association rules;
9. assign monotonic orderIndex;
10. record ambiguity diagnostics when geometry does not support one reliable ordering.
```

Naïve line interleaving across columns is forbidden.

Overlapping blocks:

- substantial geometric overlap is resolved using container/z-order evidence where available;
- otherwise the parser preserves separate elements and emits `READING_ORDER_AMBIGUOUS` rather than silently merging text.

The algorithm/profile SHALL be versioned. Any material change to column detection, band formation or tie-breaking is fingerprint-significant.

---

## 11. Multilingual and bilingual layout

Language switching alone is not a parser boundary.

The parser SHALL preserve original text and layout for RU/KK/EN and mixed-language content. Other languages SHALL be preserved as text when the underlying parser can extract them; language-specific semantic transformations are out of scope.

### 11.1 Bilingual two-column policy

For visually parallel columns:

```text
1. spatial reconstruction occurs before language inference;
2. each column remains an independent ordered structural stream;
3. optional languageHint may be attached per element/column;
4. parser SHALL NOT translate/transliterate;
5. parser SHALL NOT fabricate 1:1 translation pairing;
6. translationGroupId may be emitted only when a deterministic source/layout signal proves pairing;
7. uncertainty -> preserve independent streams with provenance.
```

Language detection is advisory metadata, not a prerequisite for column detection. Layout must remain correct even when both columns use the same language.

---

## 12. Coordinate model

For paged visual formats, parser adapters SHALL normalize source geometry where available:

```text
pageNumber: 1-based
bbox: x0,y0,x1,y1
pageWidth
pageHeight
coordinateSpace
```

Canonical normalized visual coordinates SHALL use a documented top-left-origin page coordinate space inside AstraIndexator adapters. Library-native coordinate systems MUST be converted at the adapter boundary.

Rules:

- no coordinates are fabricated where unavailable;
- page/region provenance SHALL survive OCR enrichment and downstream citation mapping;
- char offsets remain nullable/optional because zero can be a real offset;
- bbox absence is distinct from zero coordinates;
- coordinate normalization implementation is parser-version significant.

---

## 13. Heading reconstruction

Heading reconstruction is evidence-scored, not based on one formatting flag.

Evidence priority:

```text
native semantic heading/style/outline metadata
→ source outline/bookmark correlation
→ deterministic typography/layout/numbering evidence
```

### 13.1 PDF heading scoring model

The implementation SHALL use a versioned score composed from configurable evidence features such as:

```text
font size relative to local/body baseline
font weight/style
numbering pattern
preceding/following whitespace
line length
alignment
repeated style signature
outline/bookmark correlation
```

Conceptual score:

```text
headingScore = Σ(weight_i * normalized_feature_i)
```

Promotion to `HEADING` occurs only when:

```text
headingScore >= configured threshold
AND no table/list/caption ownership conflict exists
```

Exact numeric weights/thresholds are `layoutProfile` values, not permanent TZ constants. They SHALL be frozen by profile version and verified on a golden corpus before production rollout.

The parser SHALL NOT classify every bold or large span as a heading.

---

## 14. Paragraph reconstruction

### 14.1 PDF line-to-paragraph assembly v1

Line/span blocks MAY be joined when all applicable conditions hold:

```text
same reading-order column/band
compatible font/style class
horizontal indentation compatible with paragraph profile
vertical gap <= configured local line-gap threshold
no heading/list/table/caption/page-furniture boundary
no explicit block/container boundary contradicts the merge
```

Thresholds SHALL be expressed relative to local font/line metrics where possible instead of absolute points only.

The parser SHALL avoid combining across columns, headings, list boundaries, table cells, headers/footers, captions and unrelated text boxes.

Source spans used to build a paragraph SHALL remain recoverable through provenance.

Dehyphenation across visual line wraps is finalized by TZ-07, not irreversibly guessed here.

---

## 15. Repeated page furniture detection

TZ-05 SHALL identify candidates but SHALL NOT irreversibly discard uncertain content.

Candidate roles:

```text
PAGE_HEADER
PAGE_FOOTER
PAGE_NUMBER
WATERMARK_CANDIDATE
```

### 15.1 Furniture candidate v1

A page element becomes a candidate using versioned evidence such as:

```text
normalized vertical position band near top/bottom
text/style repetition across multiple pages
stable bbox/style signature
page-number lexical pattern
low-content repeated visual/image signature
```

A repeated-text threshold SHALL be configurable and corpus-validated. Position alone is insufficient for removal.

The parser emits role/confidence/provenance only. TZ-07 remains responsible for deterministic suppression, so false positives remain recoverable.

---

## 16. Lists

Native list semantics SHALL be preserved where available.

Canonical hierarchy:

```text
LIST
  ├── LIST_ITEM
  ├── LIST_ITEM
  └── LIST_ITEM
```

Useful metadata:

```text
ordered/unordered
nesting level
marker text
item ordinal
```

### 16.1 PDF list recognition v1

A candidate list item requires a combination of:

```text
recognized bullet/number marker
indentation consistency
repeated marker/indentation pattern across adjacent items
compatible vertical spacing
same reading-order column
```

One isolated numeric/bullet-like prefix SHALL NOT automatically create a list. Ambiguous candidates remain paragraphs with bounded diagnostic metadata rather than fabricated hierarchy.

---

## 17. Tables

Tables are structural data and MUST NOT be flattened immediately into arbitrary prose.

Parser output SHOULD preserve:

```text
tableId
caption optional
rowCount
columnCount
header rows
cells
row/column spans where available
page/region provenance
reading order
confidence
```

### 17.1 PDF table policy

The parser SHALL use a layered strategy:

```text
1. native/source table semantics when available;
2. explicit ruled-line/geometric cell structure when reliable;
3. text alignment/whitespace table inference only when profile confidence threshold is met;
4. otherwise preserve region/layout evidence and optional OCR/image candidate instead of inventing cells.
```

For an inferred table, the parser SHALL retain extraction method/profile and confidence. Header inference and row/column span inference SHALL be conservative.

A bad inferred table is worse than preserved raw layout evidence.

No specific third-party table library is a protocol requirement. Backend selection is an implementation profile decision subject to golden-corpus quality tests.

TZ-08 owns later logical splitting of large tables.

---

## 18. Images and figures

Every useful embedded image/figure that survives parser policy SHALL be represented as an `IMAGE` element.

Useful fields:

```text
origin object reference
page/slide
bbox
width/height
mime type if known
content hash optional
caption linkage
ocrCandidate
role hint
```

Role hints may include:

```text
SCANNED_PAGE
SCREENSHOT_CANDIDATE
TABLE_IMAGE_CANDIDATE
DIAGRAM_CANDIDATE
DECORATIVE_CANDIDATE
UNKNOWN
```

These are hints to TZ-06, not final OCR decisions.

Repeated logos/decorative assets MAY be identified through stable image/hash/layout signals. Uncertain assets are preserved.

---

## 19. Captions and image associations

Caption association SHALL be based on versioned spatial/structural evidence such as adjacency, alignment, source object relationship and reading order.

Conceptually:

```text
IMAGE/TABLE element
  ↔ CAPTION element
```

A caption remains separately addressable text with explicit link metadata. Caption text SHALL NOT be irreversibly baked into image OCR output.

Ambiguous caption association remains unlinked with diagnostic metadata rather than choosing an arbitrary target.

---

## 20. PDF processing model

PDF is the primary Tier-1 rich document format.

The parser SHALL evaluate each page independently and support:

```text
NATIVE_TEXT
SCANNED_IMAGE
MIXED
LOW_SIGNAL
EMPTY
FAILED_PAGE
```

This is a page processing classification, not a document MIME type.

One PDF may contain different modes page by page. A single failed page does not automatically invalidate the whole document when partial policy allows safe continuation.

---

## 21. PDF native-text page

For a page with sufficient trustworthy native text:

```text
extract text blocks/spans
→ reconstruct layout
→ classify elements
→ preserve images separately
→ OCR only selected image regions if TZ-06 requires
```

A native page SHALL NOT be rasterized and fully OCRed by default.

---

## 22. PDF scanned page

For a page whose useful content is primarily raster imagery and native text signal is absent/insufficient:

```text
emit page/image evidence
→ mark OCR_REQUIRED / OCR candidate
→ TZ-06 renders/recognizes
```

TZ-05 SHALL preserve page geometry sufficient to place OCR text back into canonical reading order.

---

## 23. PDF mixed page

Mixed pages are critical.

Example:

```text
native heading
native paragraph
embedded screenshot/table image
native caption
```

The parser SHALL preserve native text and image regions as separate elements.

It SHALL NOT choose one destructive mode such as whole-page OCR that discards trustworthy native text, nor discard meaningful visual regions simply because some native text exists.

---

## 24. PDF quality classification

The parser SHALL expose measurable page/document diagnostics:

```text
nativeTextChars
textBlockCount
nonWhitespaceChars
printableCharRatio
textAreaCoverage optional
imageAreaCoverage optional
failedPageCount
ocrCandidateCount
garbledTextIndicators
```

### 24.1 Quality state rules

Quality status SHALL be deterministic under one `qualityProfile`.

Document-level vocabulary:

```text
GOOD
LOW_SIGNAL
OCR_REQUIRED
PARTIAL
FAILED
```

Normative state precedence:

```text
FAILED
  -> no usable structural/native result can be produced safely

PARTIAL
  -> at least one page/section failed but usable canonical elements remain

OCR_REQUIRED
  -> parser produced valid structure and one or more mandatory OCR candidates

LOW_SIGNAL
  -> parse is structurally valid but native text quality/coverage falls below profile threshold
     and OCR is not yet mandatory for every low-signal region

GOOD
  -> none of the higher-precedence states apply and quality thresholds are met
```

Exact numeric thresholds are typed `qualityProfile` values. A production profile SHALL define and freeze at least:

```text
min_native_chars_per_text_page
min_printable_char_ratio
min_text_area_coverage when available
max_failed_page_ratio
low_signal_page_ratio
mandatory_ocr_candidate rules
```

These values MUST be calibrated on the real RU/KK/EN document corpus. Arbitrary constants such as `1000 chars` or `70% coverage` are not protocol semantics unless corpus evidence later promotes them into a named profile.

---

## 25. Native/OCR duplication boundary

TZ-05 records source geometry needed to later detect overlap between native extraction and OCR regions.

It SHALL NOT independently duplicate OCR text into native output.

Canonical later flow:

```text
native elements
+
OCR_TEXT elements with source regions
→ TZ-07 overlap/deduplication
```

The system SHALL retain enough page/region geometry and source-locator provenance to determine whether two representations originate from the same source region.

---

## 26. DOCX parsing

DOCX SHALL preserve native OOXML structure where available:

```text
paragraphs
heading styles / outline levels
lists and numbering
section boundaries
tables and cells
inline/anchored images
captions when structurally detectable
hyperlinks where provenance permits
```

DOCX parser SHALL NOT fabricate page numbers because pagination depends on rendering environment and is not intrinsic to OOXML document semantics.

Ordering follows document XML/body order augmented by explicit drawing/table relationships where required.

---

## 27. TXT and Markdown parsing

TXT:

- input decoding has already passed TZ-04 admission;
- paragraph/blank-line structure is preserved;
- no heading hierarchy is invented absent deterministic syntax/profile rules.

Markdown:

- ATX/setext headings;
- lists;
- fenced code blocks;
- block quotes where represented in canonical metadata;
- tables only when syntax is unambiguous under active Markdown profile;
- source order is authoritative.

Markdown parser SHALL preserve code blocks without prose normalization.

---

## 28. Image-only source shell

JPEG/PNG/TIFF admitted by M3 produce parser structural shells rather than invented text:

```text
ParsedDocument
  └── IMAGE / page-image element(s)
      └── OCR candidate metadata
```

For multi-frame TIFF, each frame/page is independently addressable and bounded by the M3/TZ-06 limits.

---

## 29. OCR candidate contract

TZ-05 SHALL emit structured OCR candidates sufficient for TZ-06 without requiring TZ-06 to rediscover parser layout.

Conceptual fields:

```text
candidateId
candidateType: PAGE | EMBEDDED_IMAGE | REGION
sourceElementId optional
pageNumber optional
bbox optional
sourceLocator
reasonCode
required: bool
parserConfidence optional
readingOrderAnchor optional
```

Reason vocabulary SHOULD include bounded canonical values such as:

```text
SCANNED_PAGE
LOW_NATIVE_SIGNAL
MEANINGFUL_IMAGE
TABLE_IMAGE
UNREADABLE_REGION
```

TZ-06 remains authoritative for whether/how OCR is actually executed and for OCR model confidence.

---

## 30. Parser library/backend policy

TZ-05 intentionally does NOT make a specific third-party library part of the public canonical contract.

Implementation SHALL define an explicit backend profile per format, for example conceptually:

```text
pdfBackend
pdfLayoutBackend optional
pdfTableBackend optional
docxBackend
markdownBackend
```

A backend is acceptable only when it satisfies:

```text
license approved
Python 3.12 compatibility
bounded-memory/page-wise processing where required
source-coordinate access sufficient for canonical provenance
deterministic behavior under pinned version
no implicit network/model download
real-document golden-corpus quality evidence
```

Candidate libraries MAY include established Python PDF/OOXML parsers, but choosing one is an implementation decision verified during M4 spike/benchmark, not a protocol constant.

Backend name/version SHALL be recorded in parser provenance. Changing a backend or major version is fingerprint-significant unless equivalence is proven by golden fixtures.

---

## 31. Parser quality report

Every parse SHALL produce a bounded quality summary containing at least:

```text
qualityStatus
pageCount
failedPageCount
nativeTextChars
elementCount
headingCount
paragraphCount
listCount
tableCount
imageCount
ocrCandidateCount
warnings/error summaries
```

Quality reporting SHALL not include full source text in logs/metrics.

Per-page diagnostics MAY be persisted in prepared artifacts where bounded and operationally useful.

---

## 32. Error taxonomy and partial behavior

Canonical parser errors SHALL map into TZ-13 classes.

Suggested stable codes:

```text
PARSER_UNSUPPORTED_FORMAT
PARSER_CONFIGURATION_ERROR
PARSER_MALFORMED_DOCUMENT
PARSER_ENCRYPTED_DOCUMENT
PARSER_RESOURCE_LIMIT
PARSER_TIMEOUT
PARSER_BACKEND_ERROR
PARSER_READING_ORDER_AMBIGUOUS
PARSER_TABLE_EXTRACTION_FAILED
PARSER_PAGE_FAILED
PARSER_OUTPUT_LIMIT
PARSER_INTERNAL_BUG
```

Rules:

- deterministic malformed/encrypted/unsupported input -> permanent input/policy class;
- dependency/backend temporary failure -> transient/dependency class where justified;
- resource limit -> `RESOURCE_LIMIT`;
- parser bug/invariant violation -> `INTERNAL_BUG`;
- page-local failure MAY yield `PARTIAL` only if page isolation is safe and enough provenance remains;
- silent page omission is forbidden.

---

## 33. Large-document processing and bounded emission

Large-document support is a MUST, not a SHOULD.

### 33.1 Incremental parser contract

```text
AcquiredSource
  ↓
format parser
  ↓
page/section ParseUnit
  ↓
canonical DocumentElement records
  ↓
bounded ElementSink
  ↓
spool/prepared staging owned by orchestration/TZ-03
```

Requirements:

1. parser processes natural units incrementally where format permits (PDF page, TIFF frame, DOCX structural block batches);
2. no more than `maxBufferedElements` canonical elements are retained solely for downstream emission;
3. cumulative page/element/char/table/image limits are checked during processing;
4. emitted elements are deterministic and append-only for one attempt/profile;
5. an element emitted for a completed parse unit SHALL NOT later be silently mutated; required cross-page corrections use explicit deferred metadata/finalization or force unit buffering within configured bounds;
6. local spool is attempt-owned and non-authoritative until durable publication/fenced checkpoint;
7. M4 does not invent per-page PostgreSQL status strings such as `PARSED_PAGE_123`; durable resumability is coordinated through M7/TZ-03 prepared artifact checkpoints, not unbounded dynamic job stages.

### 33.2 Recovery boundary

M4 baseline SHALL support bounded restart from immutable source. Fine-grained resume from an intermediate parser unit is permitted only once an M7/TZ-03 durable prepared checkpoint proves compatibility.

Thus a 5000-page PDF MUST be processable without OOM, but M4 alone does not promise arbitrary page-level resume before prepared-artifact publication exists.

---

## 34. Parser workspace and M3 integration

M4 consumes `source.validated` inside the attempt-owned M3 workspace.

Rules:

```text
M3 workspace policy remains authoritative for capacity/free-space
M4 may create bounded parse/ derived intermediates under same attempt root
M4 SHALL account additional workspace usage against attempt budget
M4 SHALL NOT cleanup source.validated while downstream M5/M6 may still need it
attempt orchestration owns final cleanup
```

If parser expansion would exceed workspace/resource policy:

```text
PARSER_RESOURCE_LIMIT
```

No parser library may bypass M3.1 path/capacity controls by extracting to arbitrary system temp directories unless explicitly redirected into the controlled attempt workspace.

---

## 35. Parser versioning, fingerprint and reindex impact

Parser provenance SHALL include enough data to reproduce or explain canonical output:

```text
canonicalSchemaVersion
parser implementation name/version
format backend name/version
parserProfile
layoutProfile/version
readingOrderProfile/version
qualityProfile/version
relevant output-affecting configuration digest
```

These fields feed the TZ-03/TZ-09 `processingFingerprint` composition together with OCR/normalizer/splitter identities downstream.

A material change to structure, reading order, table/list/heading recognition or coordinates requires either:

```text
new parser/layout/reading-order profile version
or demonstrated golden-fixture equivalence
```

Reindex decision follows TZ-12. The parser itself does not mutate an existing document version in place.

---

## 36. Determinism and canonical IDs

For identical source bytes and identical fingerprint-significant parser configuration:

```text
same parse-unit traversal
same element ordering
same element structural content
same source locators
same deterministic element IDs
```

Deterministic IDs SHOULD be derived from canonical document/version + stable source locator/structural ordinal, not random UUID generation. Exact shared identity rule remains governed by TZ-09.

Any detector using probabilistic/ML behavior must pin model/version/runtime determinism constraints and remain outside baseline M4 unless explicitly specified.

---

## 37. Known PDF limitations

The following are explicit 1.0 limitations, not hidden defects:

- PDF is visual presentation data; perfect semantic reconstruction is impossible for all files;
- borderless tables may remain ambiguous and should be preserved rather than over-inferred;
- malformed producer ordering/z-order can make reading order ambiguous;
- decorative shapes can mimic table borders or bullets;
- scans require TZ-06 OCR;
- bilingual parallel-text pairing may be impossible to prove;
- unusual fonts/encodings may yield low-signal native extraction;
- native text preservation and provenance take priority over speculative structural perfection.

Ambiguity SHALL be observable through warnings/confidence/quality states.

---

## 38. Observability

M4 SHALL expose low-cardinality instrumentation hooks. Exporter wiring remains TZ-14/M10.

Metrics SHOULD include:

```text
parser_documents_total{format,result}
parser_pages_total{format,page_mode}
parser_duration_seconds{format,result}
parser_elements_total{format,type}
parser_failures_total{format,error_code}
parser_ocr_candidates_total{format,reason}
parser_partial_documents_total{format}
```

High-cardinality labels such as `documentId`, `jobId`, file name, elementId or source URI are forbidden in metrics and belong in traces/logs.

Logs SHALL NOT contain raw document text by default.

---

## 39. Verification and golden corpus

M4 SHALL have an executable golden corpus covering at least:

```text
native single-column PDF
multi-column PDF
RU/KK bilingual columns
mixed native+image PDF
scanned PDF shell/OCR candidates
PDF with headings/lists
ruled table PDF
borderless/ambiguous table PDF
repeated headers/footers/page numbers
DOCX headings/lists/tables/images
TXT
Markdown with code/list/table syntax
image-only JPEG/PNG/TIFF
large multi-page synthetic/real PDF
malformed/page-local failure cases
```

For each fixture, tests SHALL assert relevant invariants:

```text
deterministic element order
stable IDs/locators
no cross-column interleaving
no fabricated text
expected table/list/heading structure where evidence is deterministic
OCR candidates preserve coordinates
quality state deterministic under profile
resource limits enforced
bounded memory/spooling behavior
```

Changes to output-affecting parser profiles/backends require golden-fixture comparison before merge.

---

## 40. M4 implementation acceptance criteria

M4 is complete only when all of the following are demonstrated:

1. exact handler resolution by TZ-04 `detectedFormat` with duplicate/missing handler tests;
2. TXT and Markdown canonical handlers pass golden tests;
3. DOCX preserves heading/list/table/image structure without fabricated page numbers;
4. native PDF emits text/layout elements with normalized coordinates;
5. reading-order-v1 passes single/multi-column and bilingual-layout fixtures;
6. scanned/mixed/low-signal pages generate correct page modes and OCR candidates;
7. heading/paragraph/list/table detectors are deterministic under versioned profile;
8. page-furniture candidates are tagged but not irreversibly dropped;
9. quality states are deterministic under named `qualityProfile`;
10. large-document fixture respects `maxBufferedElements`, workspace and cumulative limits;
11. parser backend/version/profile are present in provenance/fingerprint inputs;
12. repeated run of same fixture/profile yields equivalent canonical output;
13. parser errors map to TZ-13 taxonomy without silent page loss;
14. metrics hooks have no high-cardinality identifiers;
15. CI and golden corpus are green before M4 is marked CLOSED.

---

## 41. Decisions from pre-implementation review

The M4 review identified several valid gaps. Their resolution in this baseline is:

| Review concern | Decision |
|---|---|
| Handler selection ambiguous | **Accepted and hardened:** exact `detectedFormat` registry resolution; extension/MIME fallback explicitly forbidden |
| PDF layout algorithms unspecified | **Accepted:** versioned evidence/scoring algorithms defined; arbitrary numeric constants remain profile configuration pending corpus evidence |
| Multi-column reading order unspecified | **Accepted:** `reading-order-v1` deterministic band/column algorithm added |
| Large document strategy unclear | **Accepted:** bounded incremental `ElementSink` contract added; page-level DB stage explosion explicitly rejected |
| Bilingual columns unclear | **Accepted with correction:** spatial reconstruction first; language is advisory; no fabricated translation pairs |
| Quality thresholds unclear | **Accepted:** deterministic state precedence + required profile thresholds; no unsupported hard-coded numbers |
| Page furniture unspecified | **Accepted:** position + repetition + style evidence model; tagging only, suppression remains TZ-07 |
| Concrete parser libraries absent | **Partially accepted:** backend-selection contract and acceptance requirements added; no library is frozen before benchmark/golden-corpus proof |
| Nested M3 workspace/resource relationship | **Accepted:** M4 must use controlled attempt workspace and account against M3.1 limits |
| Processing fingerprint mechanism vague | **Accepted:** parser/backend/layout/reading-order/quality identities explicitly feed processing fingerprint |

---

## 42. Final implementation rule

AstraIndexator M4 SHALL optimize for:

```text
correct provenance
+ deterministic structure
+ bounded execution
+ recoverable ambiguity
```

rather than speculative semantic perfection.

When evidence is insufficient, preserving a lower-level `PARAGRAPH`, `IMAGE`, `OTHER` or ambiguity diagnostic is preferable to inventing a heading, table, translation pair or reading order that cannot be justified.
