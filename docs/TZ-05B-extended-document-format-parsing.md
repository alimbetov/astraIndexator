# TZ-05B — Extended Document Format Parsing

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-05B
- **Title:** Extended Document Format Parsing
- **Status:** Planned implementation baseline
- **Parent specification:** `TZ-05-document-parser.md`
- **Related specifications:** TZ-03, TZ-04, TZ-04A, TZ-05, TZ-06, TZ-07, TZ-08, TZ-09, TZ-13, TZ-14, TZ-17, TZ-18
- **Implementation milestones:** M4.1 and M4.2
- **Canonical output:** TZ-09 `ParsedDocument` / `DocumentElement[]`

---

## 2. Purpose

TZ-05B defines how AstraIndexator extends the canonical parser beyond the M4 baseline formats without creating format-specific downstream pipelines.

The extension formats are:

```text
M4.1 / Tier 2:
XLSX
PPTX
CSV
HTML

M4.2 / Tier 3:
ODT
RTF
EPUB
```

All supported formats MUST converge to the same canonical pipeline:

```text
AcquiredSource
  -> exact format handler
  -> ParsedDocument + DocumentElement[]
  -> TZ-06 OCR enrichment when required
  -> TZ-07 normalization
  -> TZ-08 logical fragmentation
  -> TZ-09 LogicalBlock mapping
```

A format extension MUST NOT introduce a parallel embedding, chunking, retrieval or vector-storage path.

---

## 3. Architectural invariants

### EF-01 — Canonical output is shared

Every extended handler emits TZ-09 canonical elements. Third-party library objects MUST NOT cross the adapter boundary.

### EF-02 — Native structure first

When a source format exposes real structure, AstraIndexator MUST preserve that structure instead of immediately flattening it to prose.

### EF-03 — Provenance is format-native

The parser MUST retain the strongest source locator naturally available from the source format.

Examples:

```text
XLSX -> workbook/sheet/cell/range/table
PPTX -> slide/shape/table/image/notes
CSV  -> row/column/header position
HTML -> DOM path/element/id/heading hierarchy
RTF  -> section/paragraph/table/object position when recoverable
ODT  -> section/paragraph/list/table/image path
EPUB -> spine item/chapter/section/DOM path
```

Physical page numbers MUST NOT be fabricated for non-paginated formats.

### EF-04 — Admission precedes parsing

A parser handler MUST NOT be activated until TZ-04/TZ-04A can safely identify and admit its source format.

`file extension` alone is never sufficient for handler routing.

### EF-05 — Bounded execution

Every handler MUST obey M3.1 workspace/capacity policy and parser limits for source size, expanded container bytes, record count, element count and runtime.

### EF-06 — No active content execution

Macros, scripts, formulas, embedded executables, external resources and active document features MUST NOT be executed.

### EF-07 — Deterministic ordering

The same source bytes + parser backend/profile version MUST reproduce equivalent canonical ordering and provenance.

### EF-08 — RAG structure over visual imitation

The parser should preserve semantic relationships required for retrieval, not attempt to recreate a full office/browser rendering engine.

---

## 4. Milestone split

### 4.1 M4.1 — High-value enterprise formats

```text
XLSX
PPTX
CSV
HTML
```

These have the highest expected value for enterprise/internal RAG and should be implemented first after the M4 baseline stabilizes.

### 4.2 M4.2 — Interchange/publication formats

```text
ODT
RTF
EPUB
```

These reuse the same canonical model but add additional container/markup semantics and should not block M4.1.

---

# 5. XLSX contract

## 5.1 Canonical structure

An XLSX document is not a sequence of paragraphs. The canonical hierarchy is conceptually:

```text
WORKBOOK
  -> SHEET
      -> TABLE / RANGE
          -> ROW
              -> CELL
      -> IMAGE / CHART_REFERENCE when useful
```

TZ-09 may represent workbook/sheet/range hierarchy through existing structural element types plus bounded metadata/source locators; a new canonical element type requires explicit TZ-09 change control.

## 5.2 Required provenance

Where available, each emitted table/cell-derived element SHOULD retain:

```text
workbookName optional
sheetName
sheetIndex
cellAddress
rowIndex
columnIndex
rangeAddress
tableName optional
mergedRange optional
formulaPresent
formulaText optional according to policy
numberFormat optional
```

Example locator:

```text
sheet = "Revenue"
range = "B12:F38"
cell = "D17"
```

## 5.3 Formula policy

AstraIndexator MUST NOT execute spreadsheet formulas.

Handler policy distinguishes:

```text
stored/cached displayed value
formula expression
```

When both are available, provenance MAY preserve both, but retrieval text SHOULD prefer the displayed/cached value unless a versioned parser profile explicitly exposes formulas as separate technical evidence.

External workbook links MUST NOT be followed.

## 5.4 Sparse and huge sheets

The handler MUST NOT iterate blindly over an XLSX theoretical maximum grid.

It MUST derive bounded used regions from workbook metadata plus actual cells and enforce configurable limits such as:

```text
max_sheets
max_non_empty_cells
max_rows_per_sheet
max_columns_per_sheet
max_merged_ranges
max_formula_cells
max_tables
```

## 5.5 Merged cells

Merged regions MUST preserve the anchor cell and range. Duplicate synthetic text MUST NOT be emitted for every physical cell in the merged area.

## 5.6 Hidden content

Hidden sheets/rows/columns are source evidence. Default policy SHOULD preserve their existence but MAY exclude their content from retrieval text according to an explicit parser profile. The decision MUST be observable and versioned.

## 5.7 Images/charts

Embedded images MAY become `IMAGE` elements and OCR candidates under TZ-06.

Charts SHOULD preserve title/series/category metadata when reliably available; chart rasterization or visual interpretation is outside M4.1 baseline.

---

# 6. PPTX contract

## 6.1 Canonical structure

```text
PRESENTATION
  -> SLIDE
      -> TITLE
      -> TEXT_BOX / PARAGRAPH
      -> LIST
      -> TABLE
      -> IMAGE
      -> CAPTION
      -> SPEAKER_NOTES optional
```

## 6.2 Required provenance

Elements SHOULD retain where available:

```text
slideNumber (1-based)
slideId
shapeId
shapeType
bbox
zOrder or stable source order
placeholderType
notesScope
```

## 6.3 Reading order

PPTX XML/object order is not automatically human reading order.

`pptx-reading-order-v1` MUST be versioned and deterministic. Baseline policy:

1. title placeholders first;
2. major body placeholders by visual region;
3. remaining text/table/image shapes using stable spatial ordering;
4. grouped shapes remain structurally related where possible;
5. speaker notes are emitted in a separate notes scope and MUST NOT be silently interleaved into slide body text.

## 6.4 Images and OCR

Image shapes are first-class `IMAGE` elements. OCR candidates are generated through the shared TZ-05/TZ-06 contract.

## 6.5 Active/external content

Macros, embedded executables, external media retrieval, linked objects and scripts are never executed or fetched by the parser.

---

# 7. CSV contract

## 7.1 Canonical model

CSV is treated as tabular structured data, not as a large plain-text paragraph.

```text
CSV DOCUMENT
  -> TABLE
      -> HEADER optional
      -> ROWS
          -> CELLS
```

## 7.2 Detection requirements

CSV has no reliable magic signature. Admission therefore requires a versioned detection profile combining:

```text
text/binary validation
encoding policy
bounded delimiter sampling
stable field-count evidence
quote/escape validation
producer extension/MIME only as secondary hint
```

Ambiguous delimited text MUST NOT be silently promoted to CSV.

## 7.3 Delimiter and encoding

Detected delimiter, quote character and encoding MUST be retained in provenance. Supported delimiter policy SHOULD include at least comma, semicolon and tab when confidence is sufficient.

## 7.4 Resource limits

Streaming parse is REQUIRED. Handler MUST bound:

```text
max_rows
max_columns
max_cell_chars
max_record_bytes
max_total_chars
```

The implementation MUST NOT load an arbitrary CSV fully into memory.

## 7.5 Retrieval representation

Rows remain structurally associated with headers. Later logical fragmentation MAY form row groups, but M4.1 MUST NOT convert the entire table into arbitrary pipe-delimited prose as its only representation.

---

# 8. HTML contract

## 8.1 HTML is parsed as a document tree

Canonical structural evidence SHOULD include:

```text
TITLE
HEADING
PARAGRAPH
LIST / LIST_ITEM
TABLE
CODE_BLOCK
IMAGE
CAPTION when determinable
LINK metadata
```

## 8.2 Security/network boundary

HTML parsing is offline.

The handler MUST NOT:

```text
execute JavaScript
load external stylesheets
load remote images
follow links
fetch iframes
resolve arbitrary external resources
```

Network dereferencing remains outside parser ownership.

## 8.3 Noise handling

DOM elements such as script/style/noscript/template are excluded from retrieval content by deterministic policy.

Navigation, menus, headers, footers and repeated boilerplate MAY be marked as role candidates but uncertain content should be preserved for TZ-07 rather than destructively removed.

## 8.4 Provenance

Useful locators include:

```text
DOM path
element id
class tokens bounded
heading ancestry
source line/offset when parser backend exposes it reliably
```

## 8.5 Malformed HTML

HTML recovery behavior depends on the selected backend/profile and MUST be deterministic. Catastrophically malformed or resource-hostile input fails closed according to parser limits.

---

# 9. ODT contract

ODT is a ZIP-based OpenDocument container and therefore extends TZ-04 container safety requirements before parsing.

Canonical preservation SHOULD cover:

```text
headings
paragraphs
lists
tables
images
captions/annotations when reliable
styles as bounded hints
```

Required provenance SHOULD use logical document/section/paragraph/table identifiers available from ODF structure. Fake page numbers are prohibited.

Embedded images MAY become OCR candidates.

Macros/scripts and external linked resources are not executed/fetched.

---

# 10. RTF contract

RTF parsing MUST be treated as structured rich text conversion, not arbitrary control-word stripping.

Baseline output SHOULD preserve when reliably recoverable:

```text
paragraph boundaries
heading/style hints
lists
tables
embedded image references
section boundaries
```

Parser MUST bound control-word depth, group nesting, decoded output size and embedded object sizes.

Embedded OLE/active objects are not executed. Unsupported binary/object content becomes bounded opaque evidence or is rejected according to profile.

---

# 11. EPUB contract

EPUB is a ZIP-based publication container with package metadata and ordered spine content.

## 11.1 Canonical hierarchy

```text
BOOK
  -> SPINE ITEM / CHAPTER
      -> SECTION
          -> HEADING
          -> PARAGRAPH
          -> LIST
          -> TABLE
          -> IMAGE
```

## 11.2 Reading order

EPUB package `spine` is authoritative for top-level reading sequence when valid. Individual XHTML documents use deterministic DOM reading order.

## 11.3 Provenance

Useful locator fields:

```text
spineIndex
manifestItemId
href/path inside package
chapter/section hierarchy
DOM path
```

## 11.4 Security

No JavaScript execution, network resource fetching or active content execution. Container traversal, compression limits and nesting policy inherit TZ-04/TZ-04A.

## 11.5 Metadata

Title, author, language and publication metadata MAY be preserved as document metadata but MUST NOT override Access Zone, TTL or business identity supplied by the job envelope.

---

# 12. Extended source locator model

TZ-09 canonical provenance MUST be capable of carrying format-native locators without forcing every format into PDF page coordinates.

Conceptual bounded metadata examples:

```json
{"format":"XLSX","sheet":"Revenue","range":"B12:F38"}
{"format":"PPTX","slide":7,"shapeId":"42"}
{"format":"CSV","rowStart":120,"rowEnd":180}
{"format":"HTML","domPath":"/html/body/main/article[1]/section[2]"}
{"format":"EPUB","spineIndex":4,"href":"chapter-05.xhtml"}
```

If TZ-09 current fields are insufficient, M4.1 MUST introduce a versioned, bounded extension before implementation rather than overloading unrelated fields.

---

# 13. M3 acquisition/admission changes required

Adding a parser handler does not make a format supported. Before activation, M3/TZ-04 MUST admit the format safely.

Required extension matrix:

| Format | Admission requirement |
|---|---|
| XLSX | ZIP/OOXML container + workbook content-types/required parts + M3.1 container limits |
| PPTX | ZIP/OOXML container + presentation content-types/required parts + M3.1 container limits |
| CSV | validated text + deterministic delimiter/record sampling profile |
| HTML | validated text/HTML signature/structure profile; offline parsing only |
| ODT | ZIP/ODF mimetype/manifest/content structure + container limits |
| RTF | RTF signature/control-structure preflight + decoded-size limits |
| EPUB | ZIP/EPUB mimetype/container.xml/package structure + container limits |

Generic ZIP remains unsupported.

A ZIP file that is not confidently classified as one explicitly supported container MUST fail as unsupported/ambiguous rather than being recursively inspected as an arbitrary archive.

---

# 14. Parser backend selection

Third-party parser libraries are implementation dependencies, not protocol semantics.

For each handler, an implementation decision record MUST capture:

```text
library/backend name and version
license compatibility
Python 3.12 support
streaming/memory characteristics
structure/provenance quality
known security limitations
golden-corpus results
```

Candidate backend families may include maintained OOXML/ODF/HTML/EPUB/RTF libraries, but no library becomes normative until measured against the corresponding corpus.

Backend changes that alter canonical structure, reading order or provenance MUST change parser fingerprint/version and may require reindexing.

---

# 15. Failure taxonomy extensions

Extended handlers SHOULD classify at least:

```text
UNSUPPORTED_EXTENDED_FORMAT
MALFORMED_XLSX
MALFORMED_PPTX
MALFORMED_CSV
MALFORMED_HTML
MALFORMED_ODT
MALFORMED_RTF
MALFORMED_EPUB
TABULAR_RESOURCE_LIMIT
MARKUP_RESOURCE_LIMIT
PRESENTATION_RESOURCE_LIMIT
EXTENDED_CONTAINER_RESOURCE_LIMIT
AMBIGUOUS_TEXT_FORMAT
EXTERNAL_RESOURCE_BLOCKED
```

Existing TZ-04/TZ-04A container/integrity/resource failure classes remain authoritative where applicable.

---

# 16. Configuration

Extended format limits MUST be typed, startup-validated and included in parser profile identity.

Minimum categories:

```text
xlsx.*
pptx.*
csv.*
html.*
odt.*
rtf.*
epub.*
```

No values in this document are permanent protocol constants. Production defaults require corpus/performance evidence.

---

# 17. Observability

Low-cardinality metrics SHOULD include:

```text
parser_documents_total{format,result}
parser_duration_seconds{format}
parser_elements_total{format,type}
parser_resource_limit_failures_total{format,limit}
```

High-cardinality document, sheet, slide, path or file identifiers belong in structured logs/traces, not metric labels.

---

# 18. Golden corpus requirements

Each format MUST have positive, adversarial and large-document fixtures.

Minimum corpus matrix:

### XLSX
- multiple sheets;
- merged cells;
- formulas + cached values;
- hidden sheet/rows/columns;
- sparse large coordinates;
- tables/images;
- hostile compressed OOXML.

### PPTX
- title/body placeholders;
- multiple text boxes;
- table;
- embedded image;
- notes;
- overlapping/grouped shapes;
- hostile OOXML.

### CSV
- comma/semicolon/tab;
- quoted multiline field;
- UTF-8 RU/KK/EN;
- ragged/ambiguous records;
- extremely long row/cell;
- large streaming fixture.

### HTML
- semantic article;
- navigation/footer noise;
- tables/lists/code;
- malformed recoverable HTML;
- script/style/iframe/external resources;
- deeply nested DOM hostile case.

### ODT
- headings/lists/tables/images;
- malformed/hostile container.

### RTF
- formatting/lists/table/image;
- deep group nesting;
- malformed control stream.

### EPUB
- valid spine/chapter order;
- images/tables;
- multiple XHTML chapters;
- malformed package;
- traversal/compression hostile container.

---

# 19. Acceptance criteria

A format is considered supported only when all of the following pass:

1. TZ-04/TZ-04A safely admits and identifies the source;
2. exactly one handler owns the authoritative detected format;
3. canonical output validates against TZ-09;
4. deterministic reparse produces equivalent structure/order/IDs;
5. format-native provenance is retained;
6. no external or active content is executed/fetched;
7. hostile/resource-limit corpus fails safely;
8. large fixture respects bounded memory/workspace policy;
9. golden corpus passes semantic structure assertions;
10. parser/backend/profile identity is included in processing fingerprint;
11. OCR candidates integrate through TZ-06 where visual content exists;
12. downstream TZ-07/TZ-08 consume output without format-specific branches.

---

# 20. Explicit non-goals

TZ-05B does not require:

- pixel-perfect Office/HTML rendering;
- executing spreadsheet formulas;
- interpreting charts from pixels;
- running macros/scripts;
- following external links/resources;
- generic archive ingestion;
- converting every format to PDF as the canonical intermediate;
- format-specific vector databases or embedding pipelines.

---

# 21. Implementation sequence

```text
M4 baseline proves canonical parser contract
        ↓
M4.1 admission extension
        ↓
XLSX handler
PPTX handler
CSV handler
HTML handler
        ↓
M4.1 golden corpus gate
        ↓
M4.2 admission extension
        ↓
ODT handler
RTF handler
EPUB handler
        ↓
M4.2 golden corpus gate
```

M4.1/M4.2 MAY be implemented after M5/M6 depending on project priority, but they MUST reuse the already-proven canonical DTO and downstream pipeline.
