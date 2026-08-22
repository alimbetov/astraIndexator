# TZ-09 — Canonical Document Model

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-09
- **Title:** Canonical Document Model
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-03, TZ-04, TZ-05, TZ-06, TZ-07, TZ-08, TZ-10, TZ-11, TZ-12, TZ-13, TZ-14, TZ-17
- **Primary downstream integration:** AstraVector `RegisterDocumentVersion` + `CreateMultiGranularityChunks`

---

## 2. Purpose

This specification defines the canonical data model used inside AstraIndexator between acquisition/parsing/OCR/normalization/logical fragmentation and downstream AstraVector ingestion.

The model SHALL provide a stable, versioned semantic boundary independent of source file format and independent of AstraVector's internal chunk topology.

The canonical flow is:

```text
IndexationJob
   -> SourceObject
   -> ParsedDocument
   -> DocumentElement[]
   -> normalized canonical document
   -> LogicalFragment[]
   -> AstraVector source-group ingestion
```

AstraIndexator owns `ParsedDocument`, `DocumentElement` and `LogicalFragment` semantics.

AstraVector owns `SOURCE`, `PARENT`, `SUB_180`, `SUB_260`, embeddings, vector projection and retrieval internals.

---

## 3. Architectural goals

The canonical model MUST:

1. preserve stable business document identity end-to-end;
2. preserve exact document version identity;
3. preserve source provenance sufficient for citation and diagnostics;
4. normalize heterogeneous source formats into one representation;
5. preserve document structure and reading order;
6. preserve multilingual content without translation/transliteration;
7. preserve image/OCR origin relationships;
8. preserve tables and lists as structured elements;
9. produce deterministic logical fragment identities for repeatable reindexing;
10. distinguish original normalized text from contextualized embedding text;
11. permit prepared artifacts to be serialized and recovered without reparsing the source;
12. map cleanly to AstraVector without exposing AstraVector internal chunk IDs as AstraIndexator identities;
13. support future schema evolution with explicit contract versioning.

---

## 4. Non-goals

TZ-09 SHALL NOT define:

- PostgreSQL claim/lease mechanics;
- SeaweedFS object lifecycle;
- parser implementation libraries;
- OCR model choice;
- logical boundary scoring details beyond the fields needed to represent decisions;
- BGE-M3 tokenization;
- AstraVector `PARENT/SUB_*` chunk generation;
- Qdrant payload implementation;
- retrieve ranking.

Those concerns belong to their respective specifications.

---

## 5. Canonical entity hierarchy

```text
IndexationJob
  |
  `-- DocumentIdentity
      |
      +-- SourceObject
      |
      `-- ParsedDocument
           |
           +-- DocumentMetadata
           +-- LanguageContext
           +-- ProcessingProvenance
           +-- DocumentElement[]
           |     +-- HEADING
           |     +-- PARAGRAPH
           |     +-- LIST
           |     +-- LIST_ITEM
           |     +-- TABLE
           |     +-- IMAGE
           |     +-- OCR_TEXT
           |     +-- CAPTION
           |     +-- CODE_BLOCK
           |     +-- PAGE_BREAK
           |     `-- OTHER
           |
           `-- LogicalFragment[]
                 +-- stable fragmentId
                 +-- originalText
                 +-- contextPrefix
                 +-- embeddingText
                 +-- hierarchy
                 +-- language context
                 +-- provenance range
                 +-- statistics
                 `-- split decision
```

---

## 6. Identity model

The following identifiers MUST remain semantically distinct:

```text
documentId
  != documentVersion
  != jobId
  != processingAttemptId
  != elementId
  != fragmentId
  != AstraVector root_chunk_id
  != AstraVector parent/sub chunk IDs
```

### 6.1 documentId

Stable business/platform document identifier supplied before AstraIndexator processing.

Requirements:

- MUST remain unchanged across worker retries;
- SHOULD remain unchanged across revisions of the same logical document;
- MUST be propagated into all fragments and downstream AstraVector requests;
- MUST NOT be derived from temporary file paths, worker IDs or parsing attempts;
- MUST be usable for later retrieve/document lifecycle operations.

### 6.2 documentVersion

Producer-visible immutable version identifier for the submitted content revision.

AstraIndexator SHALL treat the value as opaque unless the downstream contract requires a canonical numeric mapping.

If AstraVector requires numeric `document_version`, TZ-11 SHALL define the authoritative mapping and persistence strategy; TZ-09 SHALL retain the original producer version string as canonical source metadata.

### 6.3 elementId

Stable identifier of one canonical source element within one parsed document version.

Recommended deterministic construction input:

```text
documentId
+ documentVersion
+ canonical reading-order position
+ element type
+ stable source locator
```

The implementation MAY use a namespaced hash/UUID, but generation MUST be deterministic for identical normalized parser output under the same canonical schema/parser profile.

### 6.4 fragmentId

Stable identifier of one AstraIndexator logical fragment.

`fragmentId` identifies the logical source container delivered to AstraVector. It is NOT an AstraVector chunk ID.

Recommended conceptual identity:

```text
fragmentId = deterministic_id(
  documentId,
  documentVersion,
  splitterProfileVersion,
  canonical element range,
  canonical fragment text hash
)
```

A repeated reprocessing of unchanged content with the same relevant parser/normalizer/splitter contract SHOULD reproduce the same `fragmentId`.

A material logical split change MAY legitimately change fragment IDs and MUST be visible through splitter/profile version metadata.

---

## 7. Canonical schema versioning

Every prepared canonical artifact MUST declare a schema version.

Baseline:

```text
schemaVersion = astra-indexator-document-v1
```

Rules:

- backward-compatible additive changes MAY retain major version `v1`;
- semantic/incompatible changes require a new major canonical schema version;
- deserializers SHOULD reject unsupported major versions;
- prepared artifacts MUST retain the exact schema version used to create them;
- schema version MUST participate in verification evidence and replay diagnostics.

---

## 8. ParsedDocument

Conceptual DTO:

```json
{
  "schemaVersion": "astra-indexator-document-v1",
  "document": {
    "documentId": "DOC-100",
    "documentVersion": "3"
  },
  "source": {
    "storage": "SEAWEEDFS",
    "bucket": "documents",
    "objectKey": "original/DOC-100/v3/source.pdf",
    "fileName": "contract.pdf",
    "declaredContentType": "application/pdf",
    "detectedContentType": "application/pdf",
    "contentHash": "sha256:...",
    "sizeBytes": 1827341
  },
  "language": {
    "primary": "ru",
    "detected": ["ru", "kk"],
    "mixed": true
  },
  "metadata": {},
  "processing": {},
  "elements": []
}
```

`ParsedDocument` SHALL represent one immutable processing view of one source document version.

---

## 9. SourceObject

Required/expected source fields:

| Field | Required | Meaning |
|---|---:|---|
| `storage` | yes | Source storage type, initially `SEAWEEDFS` |
| `bucket` | conditional | Logical bucket/collection where applicable |
| `objectKey` | yes | Physical source object reference |
| `fileName` | no | Original/display file name |
| `declaredContentType` | no | Producer-provided MIME hint |
| `detectedContentType` | yes after validation | Actual detected MIME/type |
| `contentHash` | yes after acquisition | Canonical source content hash |
| `sizeBytes` | yes after acquisition | Source size |
| `etag` | no | Storage version hint |
| `versionId` | no | Storage-native object version if available |

`documentId` MUST NOT be inferred from `objectKey`.

---

## 10. DocumentMetadata

Business metadata is allowed but bounded.

Recommended representation:

```json
{
  "title": "Договор поставки",
  "sourceSystem": "document-service",
  "businessEntityType": "CONTRACT",
  "businessEntityId": "C-123"
}
```

Rules:

- metadata MUST NOT alter document identity semantics;
- metadata MUST NOT contain secrets or credentials;
- arbitrary deeply nested objects SHOULD be rejected in v1;
- scalar values and bounded arrays of scalars are preferred;
- metadata propagated to AstraVector MUST be explicitly mapped in TZ-11 rather than blindly forwarded.

---

## 11. LanguageContext

Canonical representation:

```json
{
  "primary": "kk",
  "detected": ["kk", "ru", "en"],
  "mixed": true,
  "script": "CYRILLIC",
  "confidence": 0.86
}
```

Rules:

- language codes SHOULD use BCP 47/ISO-compatible stable codes such as `ru`, `kk`, `en`, `de`;
- `und` SHALL represent unknown/undetermined language;
- mixed-language content is valid;
- language detection is metadata, not a processing admission gate;
- language changes MUST NOT automatically split a fragment;
- original script and lexical distinctions MUST be preserved;
- translation/transliteration is outside AstraIndexator 1.0.

Language context MAY exist at document, fragment and element levels.

---

## 12. ProcessingProvenance

Every canonical artifact MUST record enough processing provenance to reproduce/diagnose the result.

Recommended fields:

```json
{
  "parser": {
    "name": "pdf-parser",
    "version": "1.0.0",
    "profile": "default"
  },
  "ocr": {
    "engine": "...",
    "modelVersion": "...",
    "profile": "multilingual-v1"
  },
  "normalizer": {
    "version": "normalizer-v1",
    "unicodeForm": "NFC"
  },
  "splitter": {
    "version": "logical-v1",
    "profile": "multilingual-general-v1"
  },
  "contract": {
    "schemaVersion": "astra-indexator-document-v1"
  }
}
```

Only applicable components need to be populated. For example, OCR metadata may be absent when OCR was not used.

---

## 13. DocumentElement base contract

All canonical elements SHALL share a common envelope.

Conceptual DTO:

```json
{
  "elementId": "el-000213",
  "type": "PARAGRAPH",
  "sequence": 213,
  "text": "...",
  "language": {},
  "source": {},
  "attributes": {}
}
```

Required semantics:

- `elementId`: deterministic element identity;
- `type`: canonical element type;
- `sequence`: total reading-order position within the document;
- `text`: normalized textual representation when applicable;
- `source`: source provenance/locator;
- `attributes`: bounded type-specific attributes.

`sequence` MUST provide a deterministic total order for elements participating in reading order.

---

## 14. Canonical DocumentElement types

Baseline v1 types:

```text
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

### 14.1 HEADING

Additional attributes MAY include:

```text
level
numbering
hierarchyPath
```

A heading SHOULD remain attachable to following body content.

### 14.2 PARAGRAPH

Represents a coherent textual paragraph in reading order.

### 14.3 LIST / LIST_ITEM

Lists SHALL preserve:

- governing introduction where available;
- list ordering;
- nesting level;
- item marker/numbering;
- parent list relationship.

### 14.4 TABLE

Table representation MUST preserve tabular semantics and MUST NOT degrade immediately into an unstructured paragraph stream.

Canonical table data SHOULD retain:

```text
caption/title
column headers
rows
row/column counts
source range
```

Large-table continuation semantics are defined by TZ-08.

### 14.5 IMAGE

Image elements SHALL remain representable even when OCR is skipped.

Recommended attributes:

```text
mimeType
width
height
bbox
page/slide
embeddedObjectRef
contentHash
oCRCandidate
```

### 14.6 OCR_TEXT

OCR text SHALL point back to the originating image/page/region via `originElementId` or equivalent provenance relation.

Recommended attributes:

```text
originElementId
ocrConfidence
ocrEngine
ocrModelVersion
region/bbox
```

### 14.7 CAPTION

Captions SHOULD retain a relation to the associated image/table/figure when resolvable.

### 14.8 CODE_BLOCK

Code blocks SHALL preserve whitespace/line structure where parser output permits.

### 14.9 PAGE_BREAK

Used as structural provenance; it SHOULD NOT independently become searchable content.

---

## 15. SourceProvenance

Every textual or structural element SHOULD carry the strongest source locator available for its file type.

Canonical source provenance MAY include:

```json
{
  "page": 14,
  "pageFrom": 14,
  "pageTo": 15,
  "slide": null,
  "sheet": null,
  "sheetRange": null,
  "paragraphIndex": 27,
  "bbox": [100.0, 230.0, 812.0, 460.0],
  "sourcePart": "word/document.xml",
  "sourceObjectRef": null
}
```

Not every format supports every locator.

Rules:

- unavailable locators SHALL be absent/null, not fabricated;
- page/slide/sheet semantics MUST remain format-aware;
- bbox coordinates MUST declare a consistent coordinate system in TZ-05/TZ-06;
- citation-capable formats SHOULD retain page/region ranges.

---

## 16. Reading order and layout

The canonical model MUST preserve reconstructed reading order before logical splitting.

This is critical for:

- multi-column PDF;
- bilingual parallel columns;
- text surrounding images;
- captions;
- page headers/footers;
- tables.

Language detection SHALL NOT be used as a substitute for layout reconstruction.

For parallel bilingual columns, the parser SHOULD represent column regions separately rather than interleave alternating lines.

---

## 17. Native text and OCR coexistence

Native text and OCR may coexist in one document, but duplicate physical content MUST be detectable.

The model SHALL support provenance needed to determine that two textual elements originated from the same or overlapping physical region.

Example:

```text
PDF native text region R1
OCR text region R1
```

MUST NOT silently produce duplicate logical content without an explicit merge/deduplication rule.

The canonical model SHOULD retain extraction method metadata such as:

```text
NATIVE_TEXT
OCR_PAGE
OCR_IMAGE
```

---

## 18. LogicalFragment contract

`LogicalFragment` is the canonical AstraIndexator output unit for downstream source-group ingestion.

Conceptual DTO:

```json
{
  "schemaVersion": "astra-indexator-document-v1",
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
  "originalText": "Тауарды жеткізу мерзімі...",
  "contextPrefix": "3. Жеткізу\n3.2 Жеткізу мерзімі",
  "embeddingText": "3. Жеткізу\n3.2 Жеткізу мерзімі\n\nТауарды жеткізу мерзімі...",
  "elementIds": ["el-213", "el-214", "el-215"],
  "source": {
    "pageFrom": 14,
    "pageTo": 15,
    "elementFrom": "el-213",
    "elementTo": "el-215"
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
  },
  "contentHash": "sha256:..."
}
```

---

## 19. originalText, contextPrefix and embeddingText

These fields have different semantics and MUST NOT be conflated.

### 19.1 originalText

The normalized source-derived content represented by the fragment.

It is the authoritative text for source/citation semantics.

### 19.2 contextPrefix

Synthetic structural context added to improve retrievability, for example:

```text
Document title
Chapter
Section
Subsection
```

It MAY repeat hierarchy that exists elsewhere in the source.

### 19.3 embeddingText

Materialized downstream source text:

```text
embeddingText = contextPrefix + canonical separator + originalText
```

AstraVector receives this text as the logical source container unless TZ-11 defines an evolved explicit context contract.

Citation MUST NOT falsely claim that synthetic `contextPrefix` text occurred at the exact source location represented by `originalText`.

---

## 20. Fragment hierarchy

`hierarchy` SHALL represent semantic ancestor context, not arbitrary metadata.

Example:

```json
[
  "Глава 3. Поставка",
  "3.2 Сроки поставки"
]
```

Rules:

- order is outermost -> innermost;
- hierarchy MAY be empty for unstructured content;
- repeated hierarchy values SHOULD be normalized/deduplicated deterministically;
- hierarchy must preserve original language/script;
- hierarchy SHOULD be used to create `contextPrefix` according to TZ-08 profile rules.

---

## 21. Fragment source range

A fragment SHALL identify the set/range of canonical elements that produced it.

At minimum:

```text
elementIds[]
```

or a compact deterministic range plus sufficient replay information.

Recommended:

```text
elementFrom
elementTo
pageFrom
pageTo
```

For non-page formats, format-specific locators MAY supplement or replace page values.

---

## 22. Fragment statistics

Baseline fields:

```text
charCount
wordCount
sentenceCount
```

Optional useful fields:

```text
elementCount
lineCount
tableRowCount
imageOcrContributionCount
```

Statistics SHALL be calculated from the exact canonical text representation defined by the splitter profile, with field semantics documented to avoid ambiguity.

They support:

- size guards;
- calibration;
- observability;
- regression testing.

---

## 23. SplitDecision

Every fragment SHOULD record why it ended where it did.

Baseline fields:

```json
{
  "reason": "SECTION_BOUNDARY",
  "forced": false,
  "profile": "multilingual-general-v1",
  "splitterVersion": "logical-v1"
}
```

Recommended reason enum:

```text
DOCUMENT_END
CHAPTER_BOUNDARY
SECTION_BOUNDARY
SUBSECTION_BOUNDARY
ARTICLE_BOUNDARY
CLAUSE_BOUNDARY
PARAGRAPH_BOUNDARY
LIST_GROUP_BOUNDARY
TABLE_BOUNDARY
SENTENCE_BOUNDARY
HARD_LIMIT_FORCED
```

This metadata is important for quality debugging and deterministic regression evidence.

---

## 24. Fragment types

Baseline logical fragment types:

```text
SECTION
CLAUSE_GROUP
PARAGRAPH_GROUP
LIST_GROUP
TABLE
OCR_REGION
CODE_SECTION
UNSTRUCTURED
```

The fragment type describes the dominant semantic container, not AstraVector granularity.

No fragment type named `PARENT`, `SUB_180`, `SUB_260` SHALL be introduced into AstraIndexator canonical model because those are downstream AstraVector concepts.

---

## 25. Tables in LogicalFragment

When a table fits within one fragment, the fragment SHOULD retain a deterministic textual rendering suitable for AstraVector while preserving canonical structured table data in prepared artifacts.

For oversized tables:

- repeat table title/caption where necessary;
- repeat column headers;
- split by row groups;
- retain row range metadata;
- do not split one logical row unless unavoidable.

The serialized canonical document SHOULD preserve the structured table independently from the text rendering used for embedding.

---

## 26. Images and OCR in LogicalFragment

Images are not separate business documents by default.

An OCR-derived fragment SHALL keep the same `documentId/documentVersion` and SHALL reference its source image element.

Example relationship:

```text
DOC-100
  IMAGE el-500
    -> OCR_TEXT el-501
       -> LogicalFragment DOC-100:F-0088
```

Recommended fragment metadata:

```text
originElementIds
extractionMethod = OCR_IMAGE | OCR_PAGE
page/region
ocrModelVersion
```

A later retrieve by `documentId` therefore covers native and OCR-derived content of the same business document.

---

## 27. Parallel translations and multilingual documents

Separate source regions containing parallel translations SHOULD remain separate fragments when that preserves layout and provenance.

Optional future linkage:

```text
translationGroupId
```

Example:

```text
frag-100-ru -> translationGroupId TG-100
frag-100-kk -> translationGroupId TG-100
```

AstraIndexator 1.0 SHALL NOT automatically translate or semantically collapse parallel translations.

---

## 28. Content hashing

Hashes SHALL distinguish at least:

1. source document content hash;
2. canonical fragment content hash;
3. downstream ingestion fingerprint/idempotency key.

Recommended fragment content hash input:

```text
canonical UTF-8 embeddingText
+ relevant schema/profile version markers where required
```

Exact downstream idempotency fingerprint belongs to TZ-11.

Hash algorithms MUST be explicit and versioned; SHA-256 is the recommended baseline.

---

## 29. Determinism requirements

For identical:

```text
source bytes
+ parser version/profile
+ OCR outputs/version where applicable
+ normalization version/profile
+ splitter version/profile
+ canonical schema version
```

AstraIndexator SHOULD reproduce:

- equivalent reading order;
- equivalent canonical element sequence;
- equivalent normalized text;
- equivalent fragment boundaries;
- equivalent element IDs;
- equivalent fragment IDs;
- equivalent fragment content hashes.

Nondeterministic model-based operations MUST NOT silently affect identity without their version/output fingerprint being part of provenance.

---

## 30. Prepared artifact serialization

The canonical model SHALL support durable prepared artifacts in SeaweedFS.

Recommended layout from TZ-00:

```text
prepared/<documentId>/<processingVersion>/manifest.json
prepared/<documentId>/<processingVersion>/elements.jsonl
prepared/<documentId>/<processingVersion>/fragments.jsonl
```

Recommended `manifest.json` contents:

```text
schemaVersion
documentId
documentVersion
source contentHash
jobId
processing versions
language summary
element count
fragment count
artifact hashes
createdAt
```

JSONL is preferred for potentially large element/fragment collections so consumers can stream without loading the entire document into memory.

---

## 31. Memory and size constraints

The canonical model MUST be stream-friendly for large documents.

Implementation requirements:

- avoid requiring all binary source data in memory;
- allow element/fragments JSONL streaming;
- bound per-element metadata;
- bound arbitrary business metadata;
- avoid base64 embedding of large images into canonical JSON;
- represent large image/table artifacts by references when needed.

Image binaries SHALL remain in source/prepared object storage rather than being embedded directly into fragment JSON.

---

## 32. Access-zone and lifecycle context

Access-zone and lifecycle fields are cross-cutting document context, not text semantics.

Canonical document/fragment processing MUST preserve the normalized values defined in TZ-10, including where applicable:

```text
accessZoneIds[]
accessZoneCodes[]
accessLevel
expiresAt
legalHold/lifecycle metadata when later supported
```

Fragments SHALL NOT independently invent different access-zone values from their source job unless a future explicitly supported sub-document security model is introduced.

Mapping from potentially plural AstraIndexator zones to AstraVector's actual ingestion contract is defined in TZ-10/TZ-11.

---

## 33. AstraVector mapping boundary

Current AstraVector ingestion exposes `RegisterDocumentVersion` and `CreateMultiGranularityChunks` with document identity, source text, access/lifecycle metadata, profile, metadata, idempotency and correlation fields.

The canonical mapping SHALL follow this semantic model:

```text
AstraIndexator document
  -> RegisterDocumentVersion once per downstream access-zone/version scope

LogicalFragment #1
  -> CreateMultiGranularityChunks(source_text = embeddingText)
  -> AstraVector root_chunk_id A

LogicalFragment #2
  -> CreateMultiGranularityChunks(source_text = embeddingText)
  -> AstraVector root_chunk_id B
```

`fragmentId` SHALL be retained as caller metadata/idempotency identity and SHOULD be mapped explicitly in TZ-11.

AstraVector-generated `root_chunk_id` MUST be stored as a downstream delivery result/reference; it MUST NOT replace `fragmentId`.

---

## 34. Downstream idempotency relationship

Each fragment delivery MUST have a deterministic downstream idempotency identity.

Conceptual baseline:

```text
ingestionKey = SHA256(
  documentId
  + canonical downstream documentVersion
  + fragmentId
  + fragmentContentHash
  + AstraVector ingestion profile/version
)
```

The exact formula is owned by TZ-11, but TZ-09 requires all ingredients needed to construct a stable key.

This protects the crash window:

```text
Indexator sends fragment
AstraVector commits
ACK is lost
worker dies
another worker retries
```

The retry MUST resolve to the same logical downstream ingestion rather than create duplicate chunks.

---

## 35. Document version registration versus fragment ingestion

The model SHALL distinguish:

```text
DOCUMENT VERSION registration
```

from:

```text
LOGICAL FRAGMENT ingestion
```

A document version is registered once per relevant AstraVector scope, then zero or more logical fragment source groups are created, then document activation occurs according to TZ-11/TZ-12.

A document with no successfully indexed fragments MUST NOT be considered fully indexed merely because its version registration succeeded.

---

## 36. Mapping to retrieve/citation

The canonical model MUST preserve enough metadata so downstream retrieve results can ultimately be traced to:

```text
documentId
-> documentVersion
-> fragmentId
-> canonical element range
-> source object
-> page/slide/sheet/region
```

This traceability is required for:

- citation rendering;
- audit;
- debugging bad retrieval;
- selective reindexing;
- OCR/parser quality analysis.

---

## 37. Error handling for canonicalization

Canonical model construction SHOULD use machine-readable error categories.

Recommended baseline:

```text
CANONICAL_SCHEMA_ERROR
READING_ORDER_ERROR
UNSUPPORTED_ELEMENT_MAPPING
PROVENANCE_INCOMPLETE
DUPLICATE_ELEMENT_ID
DUPLICATE_FRAGMENT_ID
INVALID_FRAGMENT_RANGE
EMPTY_FRAGMENT
INVALID_LANGUAGE_METADATA
SERIALIZATION_ERROR
```

A recoverable optional metadata defect SHOULD NOT fail the entire document when safe degradation is possible; identity/provenance corruption that could misattribute content MUST fail or quarantine the job.

---

## 38. Validation invariants

A canonical document is valid only if all applicable invariants hold:

1. `documentId` is non-empty;
2. `documentVersion` is non-empty;
3. source content hash exists after acquisition;
4. element IDs are unique within document version;
5. element sequences are deterministic and non-conflicting;
6. fragment IDs are unique within document version/profile;
7. every fragment references valid element IDs/ranges;
8. `originalText` is non-empty for searchable textual fragments;
9. `embeddingText` is non-empty before AstraVector delivery;
10. fragment source range is ordered;
11. synthetic context is distinguishable from source-derived content;
12. OCR-derived text can be traced to source image/page where available;
13. schema/process versions are present;
14. no AstraVector internal chunk ID is used as AstraIndexator canonical identity.

---

## 39. Compatibility rules

Consumers of prepared canonical artifacts SHALL:

- validate `schemaVersion`;
- ignore explicitly allowed unknown additive fields when forward-compatible mode is enabled;
- reject unknown enum values when semantics cannot be safely inferred;
- never reinterpret an existing field with different semantics without major schema version change.

Canonical field naming SHOULD use lower camelCase in JSON unless a project-wide serialization standard chooses otherwise.

---

## 40. Observability requirements

Metrics/logs SHOULD expose aggregate canonicalization quality without using high-cardinality IDs as metric labels.

Useful metrics:

```text
canonical_elements_total{type}
logical_fragments_total{fragment_type}
fragment_forced_split_total
fragment_size_chars histogram
fragment_size_words histogram
ocr_elements_total
mixed_language_fragments_total
canonical_validation_failures_total{code}
prepared_artifact_bytes
```

Logs MAY include `jobId`, `documentId`, `fragmentId` for diagnostics, subject to TZ-14 logging/privacy rules.

---

## 41. Security and data minimization

Canonical/prepared artifacts contain extracted business text and SHALL be protected equivalently to the source document.

Requirements:

- no secrets/tokens in arbitrary metadata;
- no unrestricted binary embedding in JSON;
- access-zone/lifecycle context must accompany prepared artifacts according to TZ-10/TZ-16;
- temporary/debug dumps must not bypass retention/security controls;
- diagnostic logging must not emit full document text by default.

---

## 42. Pydantic/implementation contract

The eventual Python implementation SHOULD represent the canonical schema using strict typed models, preferably Pydantic v2 or an equivalent typed validation layer.

Required characteristics:

- enum-backed element/fragment types;
- strict validation for required identities;
- bounded strings/collections where practical;
- explicit UTC datetime handling;
- JSON schema generation for contract verification;
- deterministic serialization for hashing/idempotency;
- unit tests for round-trip serialization.

The generated Python schema SHALL follow this TZ rather than inventing a divergent implementation-only DTO.

---

## 43. Required verification fixtures

TZ-09 implementation tests SHALL include at least:

- native Russian PDF;
- Kazakh document;
- mixed RU/KK/EN technical document;
- scanned PDF with OCR;
- mixed native/OCR PDF;
- DOCX with headings, list, table and embedded image;
- image-only source;
- bilingual two-column PDF;
- large table;
- oversized logical section requiring forced split;
- unknown language;
- duplicate native/OCR region case.

For each fixture, expected canonical elements/fragments/provenance SHOULD be snapshot-tested where deterministic.

---

## 44. Acceptance criteria

### AC-01 — Format independence

After parsing, logical fragmentation operates on canonical elements and does not depend on PDF/DOCX-specific parser classes.

### AC-02 — Stable document identity

All fragments preserve the original `documentId/documentVersion`.

### AC-03 — Deterministic element identity

Repeated processing with identical relevant inputs reproduces the same element IDs.

### AC-04 — Deterministic fragment identity

Repeated processing with identical relevant inputs reproduces the same fragment IDs and content hashes.

### AC-05 — Provenance traceability

Every searchable fragment can be traced back to canonical element(s) and source location information available from the parser.

### AC-06 — OCR provenance

OCR-derived text retains relation to its originating image/page/region and OCR model/version.

### AC-07 — Native/OCR duplicate safety

The model contains sufficient provenance to prevent or diagnose duplicate native/OCR representations of the same source region.

### AC-08 — Multilingual preservation

RU, KK, EN and mixed-language content survives canonicalization without automatic translation/transliteration or destructive character normalization.

### AC-09 — Structured content

Lists and tables remain representable as structured canonical elements.

### AC-10 — Original versus synthetic context

`originalText`, `contextPrefix` and `embeddingText` remain explicitly distinguishable.

### AC-11 — AstraVector boundary

No AstraVector `PARENT/SUB_*` chunk IDs/types are used as canonical AstraIndexator element/fragment identities.

### AC-12 — Source-group mapping

Each `LogicalFragment` can be mapped to one deterministic AstraVector source-group ingestion call and its resulting `root_chunk_id` can be stored as downstream delivery metadata.

### AC-13 — Idempotency ingredients

The model exposes all stable fields required by TZ-11 to construct deterministic downstream idempotency keys.

### AC-14 — Prepared artifact replay

A prepared canonical artifact can be deserialized and delivered downstream without reparsing the original source, provided the schema version is supported.

### AC-15 — Streaming viability

Large canonical element/fragment collections can be serialized/deserialized in a streaming-friendly form such as JSONL.

### AC-16 — Schema validation

Invalid duplicate IDs, invalid fragment ranges, missing required identity or unsupported schema major versions fail deterministic validation.

### AC-17 — Retrieve continuity

Downstream retrieval metadata can be traced from AstraVector result/document identity back to `fragmentId` and source provenance through the persisted integration mapping.

### AC-18 — Contract tests

Generated typed implementation schemas/JSON schemas are contract-tested against representative canonical fixtures.

---

## 45. Required implementation evidence

Before TZ-09 implementation is accepted, the repository SHALL contain evidence for:

1. canonical JSON schema or generated schema artifact;
2. typed model unit tests;
3. deterministic serialization/hash tests;
4. deterministic element/fragment ID tests;
5. multilingual round-trip fixtures;
6. OCR/native provenance fixtures;
7. table/list structured fixtures;
8. JSONL streaming test with a large synthetic document;
9. prepared artifact replay test;
10. AstraVector mapping contract test coordinated with TZ-11.

---

## 46. Decisions deferred to child specifications

The following are intentionally deferred:

- exact producer-string -> AstraVector numeric document version mapping: TZ-11/TZ-12;
- accessZoneId(s)/accessZoneCode(s) normalization and mapping to AstraVector `access_zone_id`: TZ-10/TZ-11;
- TTL `expiresAt` -> AstraVector `ttl_days` behavior and precision implications: TZ-10/TZ-11;
- exact fragment idempotency-key formula: TZ-11;
- downstream activation transaction/orchestration: TZ-11/TZ-12;
- deletion/reindex replacement: TZ-12;
- prepared object naming/retention: TZ-03.

These MUST NOT be guessed by implementation before the responsible TZ is finalized.

---

## 47. Final architecture invariant

The canonical data boundary for AstraIndexator 1.0 is:

```text
source file
  -> format-specific extraction
  -> canonical DocumentElement[]
  -> multilingual logical fragmentation
  -> stable LogicalFragment[]
  -> deterministic AstraVector source-group ingestion
```

`LogicalFragment` is a semantic source container, not a final vector-search chunk.

AstraVector remains the sole owner of tokenizer/model-aware `SOURCE/PARENT/SUB_*` projection and retrieval internals.
