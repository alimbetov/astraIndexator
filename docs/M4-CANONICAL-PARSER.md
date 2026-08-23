# M4 — Canonical Parser

Status: **implemented baseline; verified by CI**.

Normative specification: `TZ-05-document-parser.md`.
Extended formats are governed separately by `TZ-05B-extended-document-format-parsing.md` and are not part of M4 baseline.

## Scope

Implemented Tier-1 parser routing:

```text
TXT      -> TextDocumentHandler
MARKDOWN -> MarkdownDocumentHandler
DOCX     -> DocxDocumentHandler
PDF      -> PdfDocumentHandler
JPEG     -> ImageDocumentHandler
PNG      -> ImageDocumentHandler
TIFF     -> ImageDocumentHandler
```

`AcquiredSource.detectedFormat` is the only handler-selection key. Extension/MIME fallback is prohibited inside M4.

## Canonical model

M4 introduces the parser-domain representation:

```text
ParsedDocument
  parser: ParserIdentity
  elements: DocumentElement[]
  ocrCandidates: OcrCandidate[]
  quality: ParseQuality
```

Element vocabulary:

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

Element identity is deterministic for the same document/version/source hash/format/source locator/element type.

## Format behavior

### TXT

Streaming UTF-8 paragraph reconstruction with bounded element/text limits.

### Markdown

Preserves headings, paragraphs, list items and fenced code blocks with deterministic source locators and section paths.

### DOCX

Preserves native heading styles, paragraphs, list-style paragraphs and tables without fabricating page coordinates. Structured table rows/cells are retained as metadata. Embedded image handling is represented as first-class IMAGE/OCR-candidate evidence.

### PDF

Uses `pdfplumber` behind the parser adapter boundary.

Implemented:

- page-wise extraction;
- normalized PDF-point geometry;
- page modes `NATIVE_TEXT`, `MIXED`, `SCANNED_IMAGE`, `LOW_SIGNAL`, `EMPTY`;
- deterministic `reading-order-v1`;
- two-column reconstruction guard against naive line interleaving;
- heading/list heuristics from typography/numbering evidence;
- page-header/footer/page-number candidate roles;
- embedded images as first-class elements;
- page/image OCR candidates;
- explicit page breaks.

### JPEG / PNG / TIFF

Standalone images are not fake-text parsed. They become first-class `IMAGE` elements with pixel-space provenance and PAGE OCR candidates. Multi-frame TIFF is represented page/frame-wise.

## Bounds and correctness

Parser context carries versioned limits for:

```text
maxPages
maxElements
maxExtractedChars
maxEmbeddedImages
maxTables
```

The parser verifies that `ParseContext.sourceSha256` matches the already acquired source hash before parsing.

Third-party parser DTOs do not escape into the canonical parser model.

## Verification

M4 tests cover:

- duplicate handler rejection;
- no format fallback;
- deterministic TXT element identity;
- RU/KK/EN text preservation;
- Markdown structure;
- DOCX heading/paragraph/table structure and absence of fabricated page geometry;
- standalone image OCR routing;
- native PDF page provenance/page breaks/page-mode output;
- source-hash mismatch rejection.

The full repository test suite remains the CI gate.

## Known limitations / follow-up hardening

M4 baseline is intentionally conservative. It does not claim perfect PDF semantic reconstruction. Borderless/complex tables, irregular magazine layouts, floating DOCX drawing anchors and advanced caption association require golden-corpus evidence before further heuristics are promoted into the versioned parser profile.

M5 owns OCR execution. M6 owns normalization/logical splitting. M4.1/M4.2 own XLSX/PPTX/CSV/HTML/ODT/RTF/EPUB.
