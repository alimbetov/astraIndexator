# TZ-09 — Canonical Document Model

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-09
- **Title:** Canonical Document Model
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-03, TZ-04, TZ-05, TZ-06, TZ-07, TZ-08, TZ-10, TZ-11, TZ-12, TZ-13, TZ-14, TZ-17
- **Authoritative downstream wire contract:** `astravector.embedding.v1.AstraVectorIngestionFacade` from `alimbetov/llm2/main`
- **Approved consumer mapping:** `agent-astradeployment-portable-local-1.0/docs/integration/ASTRAINDEXATOR_PROTO_MAPPING.md` and `EXTERNAL_DTO_REFERENCE.md`

---

## 2. Purpose

This specification defines AstraIndexator's canonical, storage-independent document model between acquisition/parsing/OCR/normalization/logical fragmentation and the AstraVector adapter.

The canonical flow is:

```text
IndexationJob
  -> AcquiredSource
  -> ParsedDocument
  -> DocumentElement[]
  -> normalized canonical document
  -> LogicalFragment[]
  -> LogicalBlockMapper
  -> AstraVector LogicalBlock[]
  -> AstraVectorIngestionFacade
```

AstraIndexator owns `ParsedDocument`, `DocumentElement`, `LogicalFragment`, provenance and deterministic source identities.

AstraVector owns tokenizer-aware chunking, BGE-M3 execution, generated searchable chunk identities, embeddings, canonical vector/document state, outbox/Qdrant projection, activation/reconciliation, effective TTL and retrieval.

Legacy v004 control-plane concepts such as `RegisterDocumentVersion`, `CreateMultiGranularityChunks`, `SOURCE`, `PARENT`, `SUB_180`, `SUB_260` and `root_chunk_id` are **not** the AstraIndexator integration boundary and MUST NOT appear in AstraIndexator domain DTOs.

---

## 3. Canonical design goals

The canonical model MUST:

1. preserve stable document identity and immutable version identity;
2. preserve source provenance sufficient for citation and diagnostics;
3. normalize heterogeneous file formats into one semantic representation;
4. preserve reading order and document hierarchy;
5. preserve multilingual RU/KK/EN content without translation/transliteration;
6. preserve image/OCR origin relationships;
7. preserve tables and lists as structured data;
8. produce deterministic element/fragment identities under the same processing fingerprint;
9. distinguish source/original text, normalized text and contextualized downstream text;
10. permit prepared artifacts to be serialized/replayed without reparsing the source;
11. map deterministically to AstraVector `LogicalBlock[]`;
12. never expose AstraVector-generated chunk IDs as AstraIndexator canonical identities;
13. support explicit schema evolution.

---

## 4. Non-goals

TZ-09 does not define:

- PostgreSQL claim/lease/fencing mechanics;
- SeaweedFS retention/publication mechanics;
- parser implementation libraries;
- OCR model delivery;
- logical split scoring algorithms;
- BGE-M3 tokenization or embedding generation;
- AstraVector generated parent/child/atomic chunk topology;
- Qdrant payload implementation;
- retrieval ranking.

---

## 5. Identity model

These identities are semantically distinct:

```text
documentId
!= documentVersion
!= jobId
!= processingAttemptId
!= elementId
!= fragmentId
!= LogicalBlock.blockId
!= AstraVector generated chunk IDs
!= ingestionSessionId
```

### 5.1 `documentId`

Baseline AstraIndexator 1.0 domain type:

```text
UUID-compatible stable identifier
```

Rules:

- stable across retries and reindex attempts of the same logical document;
- stable across document versions;
- never derived from temporary file paths or worker identity;
- supplied downstream explicitly whenever the AstraVector facade allows it.

### 5.2 `documentVersion`

AstraIndexator 1.0 canonical version is a **positive numeric version**:

```text
long / uint64 semantic domain
value > 0
```

The producer contract SHOULD supply a positive numeric version. A business/source-system revision that is not numeric belongs in metadata such as `externalRevision` and MUST NOT replace the canonical numeric version.

Important wire constraint from current `llm2`:

```text
IndexLogicalDocument.DocumentIdentity.document_version = uint64
DocumentRef.document_version                           = uint64
StartLogicalDocumentIngestionRequest.document_version  = uint32
```

Therefore the session-ingestion adapter SHALL reject a canonical version outside the current `uint32` Start range instead of truncating or wrapping it.

### 5.3 `elementId`

Deterministic identity for one canonical source element within one document version.

Recommended deterministic inputs:

```text
documentId
+ documentVersion
+ canonical reading-order position
+ element type
+ stable source locator
+ canonical schema/parser profile
```

### 5.4 `fragmentId`

Deterministic AstraIndexator logical-fragment identity.

Conceptual formula:

```text
fragmentId = deterministic_id(
  documentId,
  documentVersion,
  splitterProfileVersion,
  canonical element range,
  canonical fragment text hash
)
```

`fragmentId` is never an AstraVector generated chunk ID.

### 5.5 `LogicalBlock.blockId`

`blockId` is the public ingestion identity of one block in the deterministic LogicalBlock tree. It MAY reuse a canonical element/fragment identity when semantics are one-to-one, but this is a mapper decision. The mapping MUST remain deterministic for the same canonical input and adapter contract version.

---

## 6. Canonical schema versioning

Every prepared canonical artifact declares:

```text
schemaVersion = astra-indexator-document-v1
```

Rules:

- additive compatible changes may retain major version `v1`;
- incompatible semantic changes require a new major version;
- unsupported major versions are rejected during replay;
- schema version participates in processing/replay compatibility diagnostics.

---

## 7. ParsedDocument

Conceptual DTO:

```json
{
  "schemaVersion": "astra-indexator-document-v1",
  "document": {
    "documentId": "20fd6906-cf10-4d2a-bdbf-31ae32316716",
    "documentVersion": 3,
    "externalRevision": "optional-source-revision"
  },
  "source": {
    "storage": "SEAWEEDFS",
    "bucket": "documents",
    "objectKey": "original/.../v3/source.pdf",
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

`ParsedDocument` represents one immutable processing view of one source document version.

---

## 8. SourceObject

Expected fields:

| Field | Required | Meaning |
|---|---:|---|
| `storage` | yes | storage type, initially SeaweedFS |
| `bucket` | conditional | logical bucket/collection |
| `objectKey` | yes | source object reference |
| `fileName` | no | original/display name |
| `declaredContentType` | no | producer MIME hint |
| `detectedContentType` | yes after TZ-04 | detected format/MIME |
| `contentHash` | yes after acquisition | SHA-256 source identity |
| `sizeBytes` | yes | actual acquired bytes |
| `etag` | no | storage evidence |
| `versionId` | no | storage-native version |

`documentId` MUST NOT be inferred from the object key.

---

## 9. ProcessingProvenance

Prepared canonical data MUST retain processing identity sufficient for deterministic replay and audit:

```json
{
  "parser": {"name":"pdf-parser","version":"...","profile":"..."},
  "ocr": {"engine":"...","modelId":"...","artifactRevision":"...","profile":"..."},
  "normalizer": {"version":"...","profile":"...","unicodeForm":"NFC"},
  "splitter": {"version":"...","profile":"..."},
  "adapter": {"contract":"astravector.embedding.v1","mappingVersion":"..."},
  "contract": {"schemaVersion":"astra-indexator-document-v1"}
}
```

Only executed stages are populated. OCR metadata may be absent when OCR was not used.

---

## 10. LanguageContext

Canonical fields MAY include:

```text
primary
detected[]
mixed
script
confidence
```

Rules:

- use stable language tags such as `ru`, `kk`, `en`, `und`;
- mixed-language content is valid;
- original script is preserved;
- language changes alone do not force fragment boundaries;
- translation/transliteration is outside AstraIndexator 1.0.

---

## 11. DocumentElement

Common envelope:

```json
{
  "elementId": "el-000213",
  "type": "PARAGRAPH",
  "sequence": 213,
  "originalText": "...",
  "normalizedText": "...",
  "language": {},
  "source": {},
  "attributes": {}
}
```

Baseline canonical element types:

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

The canonical model may be richer than AstraVector `BlockType`; the adapter performs the lossy/structural mapping explicitly rather than forcing parser semantics to equal wire semantics.

### Structured requirements

- lists preserve item order, nesting and governing introduction;
- tables preserve headers/rows/cells and source ranges;
- images preserve identity/provenance even when not mapped directly to a LogicalBlock;
- OCR_TEXT retains origin element/image/page/region and OCR model/confidence evidence;
- code blocks preserve line/whitespace structure;
- page breaks are provenance, not searchable text by themselves.

---

## 12. SourceProvenance

AstraIndexator canonical provenance is richer than the current wire `SourceLocation` and MAY contain:

```text
page/pageFrom/pageTo
slide
sheet/sheetRange
paragraphIndex
bbox
sourcePart
sourceObjectRef
charStart/charEnd
sectionPath
heading
tableId
rowIndex/columnIndex
```

Unavailable locators are absent/null and MUST NOT be fabricated.

### Wire mapping caveat

Current `llm2` `SourceLocation` uses non-optional proto3 `uint32` scalar fields:

```text
page_start/page_end
char_start/char_end
row_index/column_index
```

AstraIndexator domain DTOs SHALL preserve nullability/presence internally. The adapter SHALL apply one documented convention for absent wire values. Until AstraVector publishes explicit optional presence, baseline convention is:

```text
0 = unknown / not applicable
1+ = actual 1-based page/row/column where applicable
```

Character offsets MAY legitimately start at zero; therefore adapter metadata SHOULD include explicit presence/provenance when ambiguity matters. TZ-17 contract tests SHALL verify the agreed convention against current `llm2` behavior.

---

## 13. LogicalFragment

`LogicalFragment` is the canonical AstraIndexator semantic unit prior to the AstraVector adapter.

Conceptual DTO:

```json
{
  "schemaVersion": "astra-indexator-document-v1",
  "fragmentId": "...",
  "documentId": "20fd6906-cf10-4d2a-bdbf-31ae32316716",
  "documentVersion": 3,
  "sequence": 42,
  "fragmentType": "SECTION",
  "language": {"primary":"kk","detected":["kk","ru"],"mixed":true},
  "hierarchy": ["3. Жеткізу", "3.2 Жеткізу мерзімі"],
  "originalText": "...",
  "normalizedText": "...",
  "contextPrefix": "...",
  "embeddingText": "...",
  "elementIds": ["..."],
  "source": {},
  "statistics": {},
  "splitDecision": {}
}
```

`embeddingText` is an AstraIndexator derived representation and is not evidence that AstraIndexator owns BGE-M3 tokenization or final searchable chunking.

---

## 14. Canonical -> AstraVector LogicalBlock mapping

The approved wire DTO is current `llm2`:

```proto
message LogicalBlock {
  string block_id = 1;
  string parent_block_id = 2;
  BlockType block_type = 3;
  string text = 10;
  uint32 order_index = 20;
  SourceLocation source_location = 30;
  repeated SourceLink source_links = 31;
  map<string,string> metadata = 40;
}
```

Supported current block types:

```text
DOCUMENT
SECTION
SUBSECTION
PARAGRAPH
TABLE
TABLE_ROW
LIST
LIST_ITEM
FAQ_ITEM
CODE_BLOCK
CAPTION
```

Mapping rules:

- exactly one `DOCUMENT` root per document version;
- `HEADING`/hierarchy may create `SECTION`/`SUBSECTION` blocks;
- ordinary prose maps to `PARAGRAPH`;
- structured tables map to `TABLE` plus `TABLE_ROW` where useful;
- lists map to `LIST` plus `LIST_ITEM`;
- OCR text maps according to recovered structure, not to a nonexistent `OCR` block type;
- raw `IMAGE` has no dedicated current AstraVector block type and remains canonical provenance unless useful text/caption/table structure is derived;
- `PAGE_BREAK` is not emitted as a searchable block;
- `BLOCK_TYPE_UNSPECIFIED` is never emitted.

`order_index` is `uint32` on the wire. The adapter MUST validate range before conversion.

---

## 15. SourceLink mapping

Current wire fields:

```text
type
url
label
mime_type
requires_auth
expires_at
attributes
```

Current wire link types include:

```text
ORIGINAL_DOCUMENT
PREVIEW
DOWNLOAD
PAGE
SECTION
CHUNK
EXTERNAL_SYSTEM
```

Domain code MAY use typed `Instant` for expiry and map it to the wire string representation. Source-link expiry is not document TTL expiry.

No credential/token may be embedded in links or attributes.

---

## 16. Access-zone relationship

Access-zone fields are not embedded into every canonical fragment.

The accepted job retains producer compatibility selectors:

```text
accessZoneId
accessZoneIds[]
accessZoneCode
accessZoneCodes[]
```

Before ingestion these normalize to one effective `AccessZoneRef`, per TZ-10/TZ-11.

AstraVector may resolve a code to the canonical UUID `access_zone_id`; AstraIndexator Knowledge Inventory SHOULD retain both the requested code (when supplied) and the resolved ID returned through downstream `DocumentRef`/status evidence.

---

## 17. Prepared artifact representation

Canonical prepared artifacts use the TZ-03 manifest + sharded JSONL model.

At minimum the prepared manifest identifies:

```text
schemaVersion
documentId
documentVersion
sourceSha256
processingFingerprint
parts[] + hashes/counts/order
createdAt
```

Prepared artifacts are AstraIndexator-internal durable replay data. They are not AstraVector wire DTOs.

Ordering across shards and records MUST be deterministic and explicit in the manifest/part ordering contract.

---

## 18. Structural validation before adapter mapping

Before network mutation, AstraIndexator validates:

1. canonical document identity present and version > 0;
2. deterministic element/fragment ordering;
3. exactly one downstream `DOCUMENT` root;
4. non-blank unique block IDs;
5. valid parent references;
6. acyclic hierarchy;
7. non-UNSPECIFIED block type;
8. non-blank block text where required;
9. `order_index` fits current `uint32` wire range;
10. source-location conversion respects presence/zero convention;
11. source-link/metadata bounds;
12. one effective ingestion access zone.

Server validation remains authoritative.

---

## 19. Determinism and processing fingerprint

The following materially affect canonical replay identity and therefore participate in processing fingerprint/profile provenance as appropriate:

```text
canonical schema version
parser version/profile
OCR engine/model artifact revision/profile
normalizer version/profile
splitter version/profile
LogicalBlock mapper version
```

Changing any behavior that changes canonical elements/fragments/blocks requires new evidence and may require reindexing.

---

## 20. DTO boundary rule

AstraIndexator keeps three layers distinct:

```text
canonical/domain DTOs
       ↓
AstraVector adapter/mapping
       ↓
generated protobuf DTOs from llm2
```

Generated protobuf classes MUST NOT become AstraIndexator's canonical domain model, and AstraIndexator MUST NOT hand-maintain duplicate wire protobuf classes.

---

## 21. Acceptance criteria

TZ-09 is accepted when:

- **AC-01:** `documentId/documentVersion/jobId/attemptId/elementId/fragmentId/blockId/sessionId` are not conflated;
- **AC-02:** canonical `documentVersion` is positive numeric;
- **AC-03:** session adapter rejects versions that exceed current Start `uint32` range;
- **AC-04:** legacy v004 chunk-control DTOs do not appear in AstraIndexator canonical boundaries;
- **AC-05:** ParsedDocument preserves structure, language and source provenance;
- **AC-06:** canonical nullability is not lost merely because current proto scalars lack presence;
- **AC-07:** tables/lists/images/OCR relations remain representable;
- **AC-08:** LogicalFragment IDs are deterministic under the same processing fingerprint;
- **AC-09:** mapper creates exactly one root `DOCUMENT` block;
- **AC-10:** `LogicalBlock` fields/types match current `llm2` proto;
- **AC-11:** wire `uint32` fields are range-checked before conversion;
- **AC-12:** raw images/page breaks are not forced into nonexistent block types;
- **AC-13:** source links contain no secrets and their expiry is distinct from document TTL;
- **AC-14:** requested access-zone code and resolved zone ID can both be observed without creating multi-zone ingestion;
- **AC-15:** prepared artifacts remain replayable independent of protobuf implementation classes;
- **AC-16:** mapper/schema/model/profile changes are visible in processing identity;
- **AC-17:** TZ-17 contract tests prove canonical -> proto mapping against the current pinned `llm2` revision.

---

## 22. Final invariant

The canonical boundary is:

```text
source file
  -> ParsedDocument
  -> DocumentElement[]
  -> LogicalFragment[]
  -> versioned LogicalBlockMapper
  -> generated AstraVector LogicalBlock[]
  -> AstraVectorIngestionFacade
```

AstraIndexator preserves source semantics and provenance; AstraVector owns tokenizer-aware searchable chunk generation and vector/search lifecycle.
