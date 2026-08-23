# M4 Extended Formats — Implementation Roadmap

## Status

Planned extension after M4 canonical parser baseline.

Normative specification: `TZ-05B-extended-document-format-parsing.md`.

## Scope

### M4 baseline

```text
PDF
DOCX
TXT
MARKDOWN
JPEG
PNG
TIFF
```

### M4.1 — High-value enterprise formats

```text
XLSX
PPTX
CSV
HTML
```

### M4.2 — Interchange/publication formats

```text
ODT
RTF
EPUB
```

## Dependency rule

No extended parser handler may be enabled until M3/TZ-04 admission can safely identify and validate the corresponding format.

```text
M3 admission extension
  -> AcquiredSource.detectedFormat
  -> exact handler ownership
  -> ParsedDocument / DocumentElement[]
```

Extension MUST reuse the M4 canonical DTO. New downstream pipelines are prohibited.

## M4.1 delivery order

1. Extend M3 detection/admission for XLSX/PPTX/CSV/HTML.
2. Validate whether TZ-09 source locator is sufficient for sheet/cell, slide/shape and DOM provenance.
3. Implement XLSX handler + corpus.
4. Implement CSV handler + streaming corpus.
5. Implement PPTX handler + visual-order corpus.
6. Implement HTML handler + offline/security corpus.
7. Run cross-format canonical DTO and M6 compatibility tests.
8. Freeze M4.1 parser profiles/fingerprint inputs.

## M4.2 delivery order

1. Extend M3 detection/admission for ODT/RTF/EPUB.
2. Implement ODT handler and safe ODF container tests.
3. Implement RTF handler and nesting/output-size guards.
4. Implement EPUB spine/chapter handler and safe EPUB container tests.
5. Run deterministic/golden/large-document tests.
6. Freeze M4.2 parser profiles/fingerprint inputs.

## Required proof per format

```text
safe admission
exact handler resolution
canonical DTO validation
deterministic output
format-native provenance
bounded execution
hostile-input rejection
no active/external content execution
large-document proof
golden structure corpus
OCR integration where visual assets exist
TZ-07/TZ-08 compatibility
```

## Priority rationale

XLSX/PPTX/CSV/HTML are M4.1 because they are common in enterprise knowledge bases and require materially different structural provenance. ODT/RTF/EPUB remain important but do not need to block the first extended-format release.

## Non-goals

- generic ZIP/RAR/7Z ingestion;
- macro or script execution;
- remote-resource fetching;
- spreadsheet formula calculation;
- pixel-perfect rendering;
- conversion of every format to PDF before parsing.
