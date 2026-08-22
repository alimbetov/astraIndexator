# TZ-11 — AstraVector Integration

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-11
- **Title:** AstraVector Integration
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-02, TZ-08, TZ-09, TZ-10, TZ-12, TZ-13, TZ-14, TZ-16, TZ-17, TZ-18
- **Authoritative wire source:** `alimbetov/llm2/main/proto/astravector_embedding.proto`
- **Approved consumer mapping:** `agent-astradeployment-portable-local-1.0/docs/integration/ASTRAINDEXATOR_PROTO_MAPPING.md` and `EXTERNAL_DTO_REFERENCE.md`

---

## 2. Integration authority and boundary

The only normative ingestion boundary for AstraIndexator 1.0 is:

```text
astravector.embedding.v1.AstraVectorIngestionFacade
```

Source-of-truth hierarchy:

```text
1. llm2 proto                            -> wire field/type authority
2. agent-astradeployment integration docs -> approved application/consumer mapping
3. AstraIndexator TZ-09/TZ-11           -> implementation specification
```

AstraIndexator SHALL NOT create a competing REST/gRPC ingestion protocol or manually duplicate protobuf wire DTOs.

Canonical path:

```text
AstraIndexator canonical/domain DTOs
        ↓
AstraVector adapter + validation
        ↓
generated protobuf DTOs from pinned llm2 revision
        ↓
AstraVectorIngestionFacade
        ↓
tokenizer-aware chunking / BGE-M3 / vector lifecycle
```

---

## 3. Responsibility split

### AstraIndexator owns

- source acquisition/validation;
- parsing/OCR/normalization;
- structural reconstruction;
- logical semantic fragmentation;
- deterministic canonical IDs;
- `LogicalBlock[]` mapping;
- source provenance/links;
- one effective ingestion-zone selection;
- path selection (single-call/session);
- idempotency/session orchestration;
- local wire-range validation;
- retry/reconciliation;
- durable delivery checkpoints;
- mapping downstream status into local lifecycle/Knowledge Inventory.

### AstraVector owns

- access-zone registry resolution;
- tokenizer-aware final chunking;
- BGE-M3 model execution;
- dense/sparse generation;
- canonical vector/document state;
- outbox/Qdrant projection;
- activation/reconciliation;
- effective TTL lifecycle;
- searchability/readiness;
- retrieval.

AstraIndexator MUST NOT create internal AstraVector `PARENT/SUB_*` chunks, embeddings or Qdrant points.

---

## 4. Public gRPC surface

Current `llm2` facade operations used by AstraIndexator:

```proto
service AstraVectorIngestionFacade {
  rpc IndexLogicalDocument(IndexLogicalDocumentRequest)
      returns (IndexLogicalDocumentResponse);
  rpc StartLogicalDocumentIngestion(StartLogicalDocumentIngestionRequest)
      returns (StartLogicalDocumentIngestionResponse);
  rpc AppendLogicalDocumentBlocks(AppendLogicalDocumentBlocksRequest)
      returns (AppendLogicalDocumentBlocksResponse);
  rpc FinalizeLogicalDocumentIngestion(FinalizeLogicalDocumentIngestionRequest)
      returns (IndexLogicalDocumentResponse);
  rpc AbortLogicalDocumentIngestion(AbortLogicalDocumentIngestionRequest)
      returns (AbortLogicalDocumentIngestionResponse);
  rpc GetLogicalDocumentIngestionStatus(GetLogicalDocumentIngestionStatusRequest)
      returns (GetLogicalDocumentIngestionStatusResponse);
  rpc GetDocumentVectorStatus(GetDocumentVectorStatusRequest)
      returns (GetDocumentVectorStatusResponse);
  rpc DeleteDocumentVectorsFacade(DeleteDocumentVectorsFacadeRequest)
      returns (DeleteDocumentVectorsFacadeResponse);
}
```

---

## 5. DTO-layer rule

Keep three layers separate:

```text
Domain/Application DTO
        ↓
AstraVector adapter mapper
        ↓
Generated protobuf DTO
```

Generated proto classes belong only in the adapter/transport module.

---

## 6. Approved application DTOs

The application DTO shape follows the already-approved deployment mapping.

### 6.1 Access zone

```text
sealed AccessZoneRef
  -> AccessZoneId(UUID)
  -> AccessZoneCode(String)
```

Producer compatibility may contain singular/plural selectors, but ingestion normalization SHALL produce exactly one `AccessZoneRef`.

### 6.2 Start session command

Conceptual application DTO:

```text
StartIndexingCommand {
  accessZone: AccessZoneRef,
  documentId: UUID,
  documentVersion: long,
  sourceUri: URI/string,
  fileName: string,
  contentHash: string,
  idempotencyKey: string,
  totalBytesEstimate: long,
  totalBlocksEstimate: long,
  totalPagesEstimate: long,
  metadata: map<string,string>,
  ttlDays: long
}
```

### 6.3 Append batch

```text
LogicalBlockBatch {
  sessionId: UUID/string,
  batchIndex: long,
  blocks: LogicalBlock[],
  lastBatch: boolean,
  batchContentHash: string
}
```

### 6.4 Finalize

```text
FinalizeIndexingCommand {
  sessionId,
  finalContentHash
}
```

### 6.5 Session status

```text
IngestionSessionStatus {
  sessionId,
  mappedState,
  rawState,
  receivedBatches,
  receivedBlocks,
  receivedBytes,
  sessionExpiresAt,
  errorCode,
  errorMessage
}
```

`sessionExpiresAt` is ingestion-session lifetime, not document TTL expiry.

---

## 7. Actual wire-width contract from llm2

AstraIndexator domain `long` does not imply every wire field is `uint64`.

Current proto uses:

| Concept | Proto field | Wire type |
|---|---|---|
| single-call document version | `DocumentIdentity.document_version` | `uint64` |
| status/delete document version | `DocumentRef.document_version` | `uint64` |
| session Start document version | `StartLogicalDocumentIngestionRequest.document_version` | `uint32` |
| Start `total_bytes_estimate` | same | `uint64` |
| Start `total_blocks_estimate` | same | `uint32` |
| Start `total_pages_estimate` | same | `uint32` |
| Start `ttl_days` | same | `uint32` |
| LogicalBlock `order_index` | same | `uint32` |
| Append `batch_index` | same | `uint32` |
| status `received_batches` | same | `uint32` |
| status `received_blocks` | same | `uint64` |
| status `received_bytes` | same | `uint64` |

The adapter MUST range-check before conversion. It MUST NOT truncate, wrap or cast unchecked.

For current session Start:

```text
1 <= documentVersion <= 4_294_967_295
```

If a future `llm2` proto widens the field, the adapter contract may be revised after contract tests.

---

## 8. Document identity mapping

| AstraIndexator | llm2 | Rule |
|---|---|---|
| `documentId` | `document_id` | stable logical document identity |
| `documentVersion` | `document_version` | positive numeric; wire-width guards apply |
| upstream business id | `external_document_id` | optional metadata/business identity |
| title | `title` | optional |
| source reference | `source_uri` | provenance/navigation |
| actual MIME | `mime_type` | from validated acquisition |
| content hash | `content_hash` | deterministic documented source/content identity |
| source links | `source_links` | no credentials/secrets |

AstraIndexator 1.0 baseline does not require an opaque string-to-number version mapping. Non-numeric upstream revision belongs in metadata such as `externalRevision`.

---

## 9. LogicalBlock wire contract

Current proto:

```text
block_id: string
parent_block_id: string
block_type: enum
text: string
order_index: uint32
source_location: SourceLocation
source_links: SourceLink[]
metadata: map<string,string>
```

Current block types:

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

Local validation before any mutation:

1. collection non-empty;
2. exactly one root `DOCUMENT`;
3. block IDs non-blank and unique;
4. all non-root parent references resolve;
5. hierarchy is acyclic;
6. type is not UNSPECIFIED;
7. text is non-blank for searchable/structural text blocks;
8. deterministic order;
9. `order_index` fits `uint32`;
10. source-location and metadata bounds respected.

---

## 10. SourceLocation semantics

Current llm2 fields:

```text
page_start: uint32
page_end: uint32
char_start: uint32
char_end: uint32
section_path: string
heading: string
table_id: string
row_index: uint32
column_index: uint32
```

The canonical AstraIndexator model preserves presence/nullability independently of proto3 scalar presence.

Baseline adapter convention until llm2 publishes explicit optional presence:

```text
page/row/column: 0 = unknown/not-applicable, 1+ = actual 1-based value
```

Character offsets can validly start at 0, therefore consumers MUST NOT infer presence solely from `char_start == 0`; the canonical model/provenance metadata remains the stronger source of truth.

TZ-17 SHALL pin this convention with contract fixtures against the selected llm2 revision.

---

## 11. SourceLink semantics

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

Current wire types include:

```text
ORIGINAL_DOCUMENT
PREVIEW
DOWNLOAD
PAGE
SECTION
CHUNK
EXTERNAL_SYSTEM
```

AstraIndexator domain may use typed time values and map to the wire string. SourceLink expiry is unrelated to document TTL.

Credentials/tokens are forbidden in URLs/attributes.

---

## 12. RequestContext

Current single-call/status/delete facade requests use:

```text
RequestContext {
  correlation_id,
  idempotency_key,
  caller_service,
  caller_user_id,
  caller_access_level
}
```

AstraIndexator SHOULD model an internal downstream request context with at least:

```text
correlationId
idempotencyKey
callerService
callerAccessLevel
```

`callerUserId` may be absent for a service worker unless platform policy requires it.

`AccessLevel` is a separate concept from Access Zone and MUST NOT be derived from the access-zone code.

---

## 13. Access-zone mapping

For ingestion one document version belongs to exactly one effective zone.

Accepted producer compatibility forms may include:

```text
accessZoneId
accessZoneIds[]
accessZoneCode
accessZoneCodes[]
```

Before downstream mutation they normalize to one distinct zone.

Wire ingestion exposes singular:

```text
access_zone_id
access_zone_code
```

Retrieval may expose plural selectors; this does not imply multi-zone ingestion.

### Resolved identity

AstraVector `DocumentRef` contains canonical `access_zone_id`. When ingestion started by code, AstraIndexator SHALL retain:

```text
requestedAccessZoneCode
resolvedAccessZoneId
```

in lifecycle/Knowledge Inventory evidence when the resolved ID becomes available.

This does not authorize local code->ID policy duplication; AstraVector registry remains authoritative.

---

## 14. TTL mapping

### Session path

Actual Start field:

```text
ttl_days: uint32
```

Semantics:

```text
0  -> inherit effective zone/platform policy
>0 -> explicit finite relative lifetime in days
```

`0` is not `never expire`.

### Single-call path

`IndexLogicalDocument` uses a separate `TtlPolicy` with:

```text
TTL_MODE_NONE
TTL_MODE_RELATIVE + ttl_seconds
TTL_MODE_ABSOLUTE + expires_at
```

Do not conflate second-based single-call TTL with session `ttl_days`.

AstraIndexator does not duplicate the access-zone code->TTL runtime policy.

---

## 15. Single-call path

Use `IndexLogicalDocument` only when the serialized request is comfortably within configured client/server limits.

Request carries:

```text
RequestContext
access_zone_id/access_zone_code
DocumentIdentity
LogicalBlock[]
TokenAwareChunkingOptions
VectorIndexingOptions
metadata
```

AstraIndexator MUST NOT hard-code current server maxima as protocol constants.

A successful mutation response is not automatically proof of searchability; completion policy still follows downstream vector status when needed.

---

## 16. Session path

Preferred for large documents:

```text
StartLogicalDocumentIngestion
  -> AppendLogicalDocumentBlocks x N
  -> FinalizeLogicalDocumentIngestion
  -> GetLogicalDocumentIngestionStatus
  -> GetDocumentVectorStatus
```

### 16.1 Start wire fields

Current proto:

```text
access_zone_id           string
access_zone_code         string
document_id              string
document_version         uint32
source_uri               string
file_name                string
content_hash             string
idempotency_key          string
total_bytes_estimate     uint64
total_blocks_estimate    uint32
total_pages_estimate     uint32
metadata                 map<string,string>
ttl_days                 uint32
```

Recommended idempotency key:

```text
astraindexator:{documentId}:{documentVersion}:{contentHash}
```

Retries of the same logical Start reuse the same key.

### 16.2 Append

Current wire fields:

```text
ingestion_session_id
blocks[]
batch_index: uint32
is_last_batch
batch_content_hash
```

Rules:

- deterministic monotonically increasing batch index using one documented origin convention;
- same index + same hash = intended safe replay;
- same index + different hash = integrity conflict;
- `is_last_batch` is informational;
- Finalize is still mandatory.

### 16.3 Finalize

```text
ingestion_session_id
final_content_hash
```

Finalize ACK is not proof of searchability.

### 16.4 Abort

Use Abort for an active session that must not complete. Reconcile before aborting a session that may already be finalizing/completed.

---

## 17. Session status DTO

Current wire response:

```text
ingestion_session_id: string
status: string
received_batches: uint32
received_blocks: uint64
received_bytes: uint64
expires_at: string
error_code: string
error_message: string
```

Known states:

```text
ACTIVE
FINALIZING
COMPLETED
FAILED
ABORTED
EXPIRED
```

Application mapping MUST include `UNKNOWN` and preserve the raw wire value.

Session state and session expiry are not document vector state/TTL.

---

## 18. DocumentRef and downstream canonical identity

Current llm2:

```text
DocumentRef {
  access_zone_id: string,
  document_id: string,
  document_version: uint64
}
```

This DTO is the canonical downstream reference for status/delete lifecycle operations.

AstraIndexator SHALL persist/observe enough data to reconstruct the same reference safely, especially the resolved `access_zone_id`.

---

## 19. DocumentVectorStatus application DTO

Session status MUST NOT be reused as document readiness status.

AstraIndexator SHALL model a separate application DTO conceptually equivalent to current llm2:

```text
DocumentVectorStatus {
  state: OperationState,
  progressPercent: float,
  searchable: boolean,
  message: string,
  readyToActivate: boolean,
  sync: VectorSyncStatus
}
```

Current operation states include:

```text
ACCEPTED
INDEXING
VECTORING
PUBLISHING
SYNCING
READY_TO_ACTIVATE
ACTIVE
FAILED
EXPIRED
DELETED
DELETE_SCHEDULED
DELETING
```

Unknown future enum values SHALL fail safely/map to UNKNOWN in the application layer where the language/runtime requires defensive mapping.

---

## 20. VectorSyncStatus application DTO

For Knowledge Inventory/reconciliation, model the current `GetVectorSyncStatusResponse` evidence separately:

```text
documentStatus
expectedBindings
syncedBindings
pendingBindings
failedBindings

denseVectorsExpected
denseVectorsFound
sparseVectorsExpected
sparseVectorsFound

outboxPending
outboxRetryPending
outboxCompleted
outboxFailed

qdrantCollection
qdrantCollectionExists
qdrantPointsExpected
qdrantPointsFound
qdrantPointsMissing
qdrantPointsExtra

readyToActivate
lastSyncAttemptAt
lastSyncErrorCode
lastSyncErrorMessage
warnings[]
```

This is read/reconciliation evidence. AstraIndexator still MUST NOT access Qdrant directly.

---

## 21. Completion levels

AstraIndexator distinguishes at least:

```text
SESSION_ACCEPTED
BLOCKS_STAGED
FINALIZED
VECTOR_READY
SEARCHABLE
FAILED
```

Top-level local job `COMPLETED` requires the completion policy defined by TZ-12/TZ-13, with authoritative downstream evidence including `searchable=true` for the baseline indexing success contract.

`session.status == COMPLETED` alone is insufficient.

---

## 22. Hash canonicalization P0 gap

The session protocol requires:

```text
batch_content_hash
final_content_hash
```

The DTO fields exist and are aligned. What remains unresolved is the byte-exact external canonicalization algorithm.

Before production interoperability is declared, AstraVector MUST publish/freeze:

```text
canonical input fields
field ordering
normalization
serialization/text representation
UTF-8 bytes
SHA-256
hex representation
shared golden fixtures
```

AstraIndexator SHALL NOT reverse-engineer current Rust serialization and treat it as a stable external contract.

This remains a **P0 external contract gate** for strict session ingestion.

---

## 23. Retry/reconciliation rules

Mutating RPC retry policy:

```text
Start timeout
 -> retry SAME idempotency key

Append timeout
 -> replay SAME session + batchIndex + batchContentHash

Finalize timeout
 -> query session/document status before unsafe replay/replacement

UNAVAILABLE
 -> bounded exponential backoff

validation/policy error
 -> no blind retry

ambiguous downstream state
 -> reconcile, do not create a new document version
```

Delivery semantics remain at-least-once with idempotent/reconcilable downstream operations.

---

## 24. Durable checkpoints

TZ-02/TZ-13 persistence for session delivery SHALL retain enough state for restart/reclaim:

```text
ingestionSessionId
idempotencyKey
nextBatchIndex
lastAcceptedBatchIndex
batchContentHash per accepted batch
finalContentHash
raw session state
DocumentRef / resolved accessZoneId when available
raw/vector state
searchable evidence
last reconciled timestamp/error
```

A stale worker cannot authoritatively update these checkpoints without current fencing ownership.

---

## 25. Contract pinning and generated code

AstraIndexator implementation SHALL pin:

```text
llm2 protobuf revision/contract revision
supported BlockType values
approved deployment mapping revision
hash canonicalization revision once published
```

Generated protobuf code is produced from the pinned proto. Handwritten duplicate wire classes are prohibited.

Additive unknown fields/statuses must be handled forward-compatibly where the generated runtime permits it.

---

## 26. Contract verification

TZ-17 SHALL provide executable tests for at least:

1. canonical LogicalBlock -> generated proto mapping;
2. all supported block types;
3. exactly one root and hierarchy validation;
4. `uint32` max/range failures for documentVersion/orderIndex/batchIndex/estimates/ttlDays;
5. session Start by zone code;
6. capture of resolved `access_zone_id` from downstream reference;
7. TTL inheritance (`ttlDays=0`);
8. explicit finite TTL;
9. Start retry with same idempotency key;
10. Append replay with same index/hash;
11. same index/different hash conflict;
12. Finalize ambiguity/status reconciliation;
13. session `UNKNOWN` raw-state compatibility;
14. SourceLocation zero/presence convention;
15. SourceLink mapping and secret absence;
16. separate session status vs DocumentVectorStatus;
17. VectorSyncStatus/Knowledge Inventory mapping;
18. `searchable=true` completion proof;
19. delete/status operations reconstructed from canonical `DocumentRef`;
20. shared hash golden fixtures once published.

---

## 27. Acceptance criteria

- **AC-01:** wire DTOs are generated from pinned current llm2 proto;
- **AC-02:** AstraIndexator domain/application DTOs follow the approved deployment mapping;
- **AC-03:** no legacy v004 ingestion DTO is used as the public boundary;
- **AC-04:** one effective ingestion access zone is enforced;
- **AC-05:** resolved `access_zone_id` is retained for lifecycle/status operations;
- **AC-06:** all current wire `uint32` fields are range-checked;
- **AC-07:** documentVersion is positive numeric and session-compatible;
- **AC-08:** LogicalBlock hierarchy and types match current llm2;
- **AC-09:** SourceLocation ambiguity is explicitly documented/tested;
- **AC-10:** session and document-vector status DTOs remain separate;
- **AC-11:** VectorSyncStatus is available to reconciliation/Knowledge Inventory without direct Qdrant access;
- **AC-12:** Start/Append/Finalize replay rules are deterministic;
- **AC-13:** `is_last_batch` is never treated as implicit finalize;
- **AC-14:** `session COMPLETED` is not equated to `searchable=true`;
- **AC-15:** `ttl_days=0` means inherit, not forever;
- **AC-16:** RequestContext/AccessLevel remain distinct from access-zone semantics;
- **AC-17:** hash canonicalization remains an explicit P0 gate until shared golden fixtures exist;
- **AC-18:** contract tests execute against the pinned llm2 facade revision.

---

## 28. Final invariant

The integration contract is:

```text
AstraIndexator canonical model
  -> approved application DTOs
  -> validated adapter mapping
  -> generated llm2 protobuf DTOs
  -> AstraVectorIngestionFacade
  -> authoritative downstream status
  -> searchable=true
```

No fourth DTO interpretation is permitted between the agreed deployment mapping and the actual llm2 wire contract.
