# TZ-05 — Document Parser

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-05
- **Title:** Document Parser
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-03, TZ-04, TZ-06, TZ-07, TZ-08, TZ-09, TZ-13, TZ-14, TZ-15, TZ-16, TZ-17, TZ-18
- **Input boundary:** TZ-04 `AcquiredSource`
- **Output boundary:** TZ-09 `ParsedDocument` / `DocumentElement[]`
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

Page count, element count, extracted chars, tables, images and parser runtime SHALL be bounded by configuration.

### DP-10 — No embedding chunking

The parser SHALL NOT use BGE-M3 tokenizer or generate AstraVector `PARENT/SUB_*` chunks.

### DP-11 — Partial quality is explicit

If parsing succeeds structurally but text signal is insufficient, the result SHALL record a quality state and OCR candidates rather than pretending extraction is complete.

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
- `ParsedDocument` production.

### 5.2 TZ-04 owns

- source admission;
- safe local source;
- SHA-256;
- MIME/signature validation;
- container/image safety preflight.

### 5.3 TZ-06 owns

- OCR policy and decision thresholds;
- OCR model lifecycle;
- model loading from approved internal supply, including Nexus where configured;
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

## 6. Parser architecture

Recommended structure:

```text
DocumentParserService
    ↓
FileTypeHandlerRegistry
    ├── PdfDocumentHandler
    ├── DocxDocumentHandler
    ├── TextDocumentHandler
    ├── MarkdownDocumentHandler
    └── ImageDocumentHandler
```

Conceptual interface:

```python
class DocumentHandler(Protocol):
    def supports(self, source: AcquiredSource) -> bool: ...
    def parse(self, source: AcquiredSource, context: ParseContext) -> ParsedDocument: ...
```

Rules:

- exactly one handler SHALL match a Tier-1 admitted format;
- routing SHALL use `detectedFormat`, not producer extension;
- third-party library DTOs SHALL not escape into the AstraIndexator domain model;
- parser implementation/version/profile SHALL be recorded in provenance;
- parser adapters SHALL be replaceable without changing TZ-09 canonical semantics.

---

## 7. ParseContext

Conceptual context:

```json
{
  "jobId": "...",
  "processingAttemptId": "...",
  "documentId": "...",
  "documentVersion": 3,
  "sourceSha256": "...",
  "parserProfile": "default-v1",
  "limits": {
    "maxPages": 5000,
    "maxElements": 200000,
    "maxExtractedChars": 50000000,
    "maxEmbeddedImages": 10000,
    "maxTables": 10000
  }
}
```

Values above are illustrative only. Production limits are configuration and MUST be startup-validated.

---

## 8. Canonical parser output

TZ-05 SHALL produce a `ParsedDocument` conforming to TZ-09.

Conceptual summary:

```json
{
  "schemaVersion": "astra-indexator-document-v1",
  "documentId": "DOC-100",
  "documentVersion": 3,
  "sourceSha256": "...",
  "detectedFormat": "PDF",
  "parser": {
    "name": "pdf-parser",
    "version": "1.0.0",
    "profile": "default-v1"
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

Exact schema remains governed by TZ-09.

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

Each element SHOULD retain where meaningful:

```text
elementId
parentElementId
type
orderIndex
text
level
pageNumber / slideNumber / sheetName
bbox
sectionPath
sourceLocator
styleHints
languageHint optional
role optional
bounded metadata
```

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

For multi-column pages, the parser MUST avoid naïve interleaving such as:

```text
left line 1
right line 1
left line 2
right line 2
```

when columns are independent reading flows.

Reading-order algorithm/profile SHALL be versioned because changing it can alter downstream fragments and IDs.

---

## 11. Multilingual and bilingual layout

Language switching alone is not a parser boundary.

The parser SHALL preserve original text and layout for:

```text
RU
KK
EN
DE
mixed-language paragraphs
bilingual columns
parallel translations
```

A bilingual two-column page MUST first be reconstructed spatially before TZ-08 sees it.

The parser MAY attach language hints but SHALL NOT translate or transliterate.

Parallel translation pairing MAY be represented through structural metadata when reliably detected, but uncertain pairing MUST NOT be fabricated.

---

## 12. Coordinate model

For paged visual formats, parser adapters SHOULD emit normalized source geometry:

```text
pageNumber: 1-based
bbox: x0,y0,x1,y1
pageWidth
pageHeight
coordinateSpace
```

Rules:

- coordinate representation SHALL be consistent within one parser profile;
- no coordinates are fabricated where unavailable;
- page/region provenance SHALL survive OCR enrichment and downstream citation mapping;
- original library coordinate conventions SHALL be normalized by adapter code.

---

## 13. Heading reconstruction

Priority:

```text
native semantic heading metadata
→ outline/bookmark/style information
→ deterministic layout/typography heuristics
```

Examples:

- DOCX heading styles / outline levels;
- Markdown `#` hierarchy;
- PDF outline/bookmarks where trustworthy;
- PDF typography/spacing/numbering heuristics.

PDF heading heuristics MAY use:

```text
font-size delta
font weight
numbering pattern
whitespace
alignment
repeated style pattern
outline correlation
```

Heuristic confidence SHOULD be retained when practical.

The parser SHALL NOT simply classify every bold span as `HEADING`.

---

## 14. Paragraph reconstruction

PDF line/spans MAY be combined when geometric and typography evidence supports one paragraph.

The parser SHALL avoid combining across:

```text
columns
headings
list boundaries
table cells
headers/footers
captions
unrelated text boxes
```

Source line/span ranges SHOULD remain recoverable through provenance.

Dehyphenation across visual line wraps is finalized by TZ-07, not irreversibly guessed here.

---

## 15. Repeated page furniture

Repeated headers, footers, page numbers and watermarks can severely pollute retrieval.

TZ-05 SHOULD identify candidates but SHOULD NOT irreversibly discard uncertain content.

Suggested role metadata:

```text
PAGE_HEADER
PAGE_FOOTER
PAGE_NUMBER
WATERMARK_CANDIDATE
```

TZ-07 decides deterministic suppression/normalization.

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

Useful metadata MAY include:

```text
ordered/unordered
nesting level
marker text
item ordinal
```

PDF list recognition may use numbering/bullets/indentation heuristics and SHALL preserve uncertainty when ambiguous.

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
```

A table SHOULD be represented as a `TABLE` element with structured payload/source references rather than only `"a | b | c"` text.

For PDF table extraction:

- native geometric table detection is preferred when reliable;
- uncertain regions MAY be emitted as `IMAGE`/OCR candidates;
- a bad inferred table is worse than preserved raw layout evidence;
- table confidence SHOULD be recorded when heuristic extraction is used.

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

Parser SHOULD classify lightweight role hints when deterministic enough:

```text
SCANNED_PAGE
SCREENSHOT_CANDIDATE
TABLE_IMAGE_CANDIDATE
DIAGRAM_CANDIDATE
DECORATIVE_CANDIDATE
UNKNOWN
```

These are hints to TZ-06, not final OCR decisions.

Repeated logos/decorative assets SHOULD be identified through stable image/hash/layout signals where possible, but uncertain assets are preserved rather than silently discarded.

---

## 19. Captions and image associations

Caption association SHOULD preserve proximity and reading order.

Conceptually:

```text
IMAGE element
  ↔ CAPTION element
```

A caption SHALL remain separately addressable text with link metadata to its visual object.

The parser SHALL NOT bake caption text irreversibly into image OCR output.

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
```

This is a page processing classification, not a document MIME type.

One PDF may therefore contain:

```text
page 1 → NATIVE_TEXT
page 2 → NATIVE_TEXT
page 3 → SCANNED_IMAGE
page 4 → MIXED
page 5 → LOW_SIGNAL
```

The document is not forced into one global text-vs-OCR mode.

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

A native page SHOULD NOT be rasterized and fully OCRed by default.

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

The parser SHALL preserve native text and the image as separate elements.

It SHALL NOT choose one destructive mode:

```text
OCR whole page and discard native text
```

or:

```text
keep native text and discard meaningful image
```

TZ-06 decides whether the image/region requires OCR.

---

## 24. PDF low-signal diagnostics

The parser SHALL expose measurable native extraction diagnostics such as:

```text
nativeTextChars
textBlocks
pageArea coverage optional
imageArea coverage optional
empty/garbled text indicators
```

A low-signal decision may be based on configurable policy, but the parser MUST NOT hard-code one historical threshold as permanent protocol semantics.

Useful state vocabulary:

```text
GOOD
LOW_SIGNAL
OCR_REQUIRED
PARTIAL
FAILED
```

This adapts the practical low-signal/OCR-required idea from the predecessor implementation while keeping the threshold versioned/configurable.

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

The system SHALL retain enough provenance to determine whether two text representations originate from the same page/region.

---

## 26. DOCX parsing

DOCX is a structured OOXML format and SHALL not be reduced immediately to concatenated paragraphs plus pipe-delimited table rows.

Parser SHOULD preserve:

```text
paragraph order
heading styles
outline levels
lists and nesting
tables
hyperlinks
sections
headers/footers with role metadata
embedded images
captions where recognizable
```

Images remain first-class elements and MAY become OCR candidates.

DOCX pagination is not authoritative unless generated by a layout engine; therefore page numbers SHOULD NOT be fabricated from paragraph order.

---

## 27. TXT parsing

TXT parsing SHALL produce structured paragraphs/blocks after TZ-04 has resolved encoding.

It SHOULD preserve:

```text
line offsets
character offsets
blank-line paragraph boundaries
original encoding provenance
```

TXT parser SHALL NOT infer complex heading hierarchy solely from capitalization without profile evidence.

---

## 28. Markdown parsing

Markdown has native structural cues and SHOULD preserve:

```text
headings
paragraphs
lists
code blocks
block quotes
tables when supported
links
```

Fenced code SHALL map to `CODE_BLOCK` rather than prose.

Inline markup MAY be normalized later, but structural meaning must not be lost.

---

## 29. Image source parsing

For JPEG/PNG/TIFF source documents, parser output creates document/page/image structure but does not itself perform recognition.

Typical flow:

```text
AcquiredSource IMAGE
→ IMAGE element(s)
→ page/frame provenance
→ OCR_REQUIRED candidate(s)
→ TZ-06
```

Multi-page TIFF SHALL create ordered page/frame elements.

---

## 30. Future XLSX/PPTX/HTML contract

Tier-2 formats are not mandatory in AstraIndexator 1.0 baseline, but the parser architecture SHALL allow them without changing TZ-09.

Expected mapping:

```text
XLSX
  workbook → sheet → table/rows/cells

PPTX
  presentation → slide → title/text/image/table/notes

HTML
  semantic DOM/main content → headings/paragraphs/lists/tables/code
```

Future handlers SHALL not flatten these formats simply because the first implementation is easier.

---

## 31. Parser quality report

Every parse SHOULD produce a bounded quality summary.

Conceptual fields:

```text
pagesProcessed
nativeTextChars
elementCount
headingCount
paragraphCount
tableCount
imageCount
ocrCandidateCount
emptyPageCount
lowSignalPageCount
warnings[]
qualityStatus
```

Quality metrics are diagnostics and routing evidence, not the final RAG quality score.

---

## 32. Parser limits

Configuration SHALL include explicit bounds such as:

```text
max_pages
max_elements
max_extracted_chars
max_images
max_tables
max_table_cells
max_parse_seconds
max_metadata_bytes_per_element
```

If a deterministic limit is exceeded:

```text
PARSER_RESOURCE_LIMIT
```

or a more specific typed error SHALL be emitted.

Blind retry under unchanged limits is prohibited.

---

## 33. Large-document processing

Parser architecture SHOULD support page/section incremental processing and avoid building unnecessary duplicate representations in memory.

Baseline requirement:

- no second full binary copy in heap;
- page-level temporary objects released when possible;
- emitted canonical elements may be streamed/spooled toward TZ-03 prepared artifacts;
- parser SHOULD support bounded batching/checkpoint-friendly output for very large documents.

The final durable sharding contract remains TZ-03.

---

## 34. Deterministic IDs

TZ-09 defines identity semantics.

TZ-05 SHALL provide stable ingredients for `elementId`, including:

```text
documentId
version
canonical order
source locator
page/region
canonical element type
```

A parser library's transient internal object ID MUST NOT become the canonical element ID.

---

## 35. Parser versioning and reindex impact

The following SHALL be persisted:

```text
parserName
parserVersion
parserProfileVersion
readingOrderVersion
layoutRulesVersion where separate
```

A change that materially alters canonical structure or reading order MAY require reindexing under TZ-12.

Such a change MUST participate in TZ-03 `processingFingerprint` compatibility.

---

## 36. Failure taxonomy

Canonical parser errors SHOULD include:

```text
PARSER_UNSUPPORTED_FORMAT
PARSER_CORRUPT_DOCUMENT
PARSER_ENCRYPTED_DOCUMENT
PARSER_INTERNAL_ERROR
PARSER_TIMEOUT
PARSER_RESOURCE_LIMIT
PARSER_PAGE_LIMIT_EXCEEDED
PARSER_ELEMENT_LIMIT_EXCEEDED
PARSER_TEXT_LIMIT_EXCEEDED
PARSER_TABLE_LIMIT_EXCEEDED
PARSER_IMAGE_LIMIT_EXCEEDED
PARSER_LAYOUT_FAILED
PARSER_READING_ORDER_UNCERTAIN
PARSER_OUTPUT_INVALID
OCR_REQUIRED
```

Some conditions are warnings/quality states rather than terminal errors. `OCR_REQUIRED` is normally a routing result, not failure.

Retry classification follows TZ-13.

---

## 37. Security rules

1. Parser receives only TZ-04 admitted sources.
2. Parser SHALL NOT execute macros/scripts/embedded active content.
3. External links/references SHALL NOT be fetched automatically.
4. Embedded file attachments SHALL NOT be recursively processed without an explicit future contract.
5. Third-party parsers SHOULD run with bounded resources; process isolation/sandbox policy is completed in TZ-16/TZ-18.
6. Parser logs SHALL NOT dump whole document text.
7. Temporary extracted assets use safe internal paths only.

---

## 38. Observability

Metrics SHOULD include:

```text
parser_documents_total{format,status}
parser_duration_seconds{format}
parser_pages_total{format}
parser_elements_total{type}
parser_native_text_chars_total
parser_tables_total
parser_images_total
parser_ocr_candidates_total
parser_low_signal_pages_total
parser_failures_total{reason}
```

Structured logs SHOULD carry:

```text
jobId
documentId/documentVersion
processingAttemptId
parser/version/profile
format
page count
element count
quality status
warning/error codes
```

Raw content MUST NOT be normal-log payload.

---

## 39. OCR/model delivery note

Parser-core SHALL NOT depend on OCR model installation or model registry protocols.

TZ-06/TZ-15 may configure OCR model acquisition from the internal repository:

```text
https://nexus.astrabase.asia
```

The parser only emits deterministic OCR candidates and source geometry. This keeps parser availability independent from temporary model-registry/network issues after models are provisioned/cached.

---

## 40. Verification corpus

TZ-17 SHALL include real/synthetic documents covering at least:

1. native text PDF;
2. scanned PDF;
3. mixed PDF;
4. PDF with headings and multiple columns;
5. bilingual RU/KK two-column PDF;
6. PDF with repeated headers/footers;
7. PDF with embedded screenshot;
8. PDF image table;
9. PDF native table;
10. corrupt PDF;
11. DOCX headings/lists/tables/images;
12. TXT UTF-8 multilingual;
13. Markdown headings/lists/code/table;
14. JPEG/PNG source image;
15. multi-page TIFF;
16. very large page-count document;
17. element/resource-limit document;
18. deterministic repeat parse;
19. parser version change fixture;
20. downstream retrieval-quality fixture demonstrating correct reading order.

---

## 41. RAG-quality acceptance

Parser correctness SHALL not be measured only by extracted character count.

Verification SHOULD measure:

```text
heading preservation
paragraph boundary accuracy
reading-order correctness
table preservation
list preservation
image/OCR provenance
citation page accuracy
repeated-header noise rate
retrieval hit quality after TZ-08/AstraVector
```

A parser that extracts more characters but destroys logical order is considered lower quality for RAG.

---

## 42. Acceptance criteria

TZ-05 is satisfied when:

- **AC-01:** all Tier-1 admitted formats route through explicit handlers;
- **AC-02:** parser result conforms to TZ-09 canonical model;
- **AC-03:** PDF is processed page-wise and may be native/scanned/mixed within one document;
- **AC-04:** native text is not discarded merely because images exist;
- **AC-05:** embedded images survive as first-class elements;
- **AC-06:** OCR candidates preserve page/region provenance;
- **AC-07:** reading order is deterministic and multi-column aware;
- **AC-08:** bilingual parallel layout is not naïvely interleaved;
- **AC-09:** headings/paragraphs/lists/tables remain structurally distinct;
- **AC-10:** repeated page furniture is classified rather than blindly indexed as body text;
- **AC-11:** DOCX native structure is preserved beyond plain-text concatenation;
- **AC-12:** TXT/Markdown preserve meaningful structural/source offsets;
- **AC-13:** parser execution is bounded by explicit resource limits;
- **AC-14:** same source/profile reproduces equivalent canonical structure;
- **AC-15:** parser/library internal IDs do not become canonical identities;
- **AC-16:** parser version/profile participates in processing fingerprint/reindex decisions;
- **AC-17:** low-signal/OCR-required states are explicit;
- **AC-18:** parser does not perform embeddings/tokenizer-aware chunking;
- **AC-19:** raw source text is not leaked through normal logs;
- **AC-20:** RAG-quality verification includes structure and retrieval effects, not only character extraction.

---

## 43. Implementation decomposition

Recommended modules:

```text
DocumentParserService
FileTypeHandlerRegistry
ParseContext
ParseQualityReport
PdfDocumentHandler
PdfPageClassifier
PdfLayoutReconstructor
ReadingOrderResolver
HeadingDetector
ParagraphAssembler
ListDetector
TableExtractor
ImageExtractor
DocxDocumentHandler
TextDocumentHandler
MarkdownDocumentHandler
ImageDocumentHandler
ParsedDocumentValidator
```

This decomposition permits targeted replacement and benchmarking of PDF layout/table/reading-order components without changing canonical contracts.

---

## 44. Final invariant

The parser boundary is:

```text
AcquiredSource
  ↓
trusted format handler
  ↓
native extraction + structure/layout reconstruction
  ↓
explicit reading order
  ↓
text + tables + lists + images + captions + provenance
  ↓
quality/OCR-candidate diagnostics
  ↓
ParsedDocument
```

It is explicitly NOT:

```text
file
→ extract all text
→ concatenate
→ split every N characters/tokens
```

The quality of this structural representation is a direct upstream determinant of TZ-08 fragmentation and final RAG retrieval quality.
