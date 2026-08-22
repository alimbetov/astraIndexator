# TZ-11 — AstraVector Integration

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-11
- **Title:** AstraVector Integration
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-02, TZ-08, TZ-09, TZ-10, TZ-12, TZ-13, TZ-14, TZ-16, TZ-17, TZ-18
- **Wire authority:** `alimbetov/llm2/main` → `proto/astravector_embedding.proto`
- **Approved consumer mapping:** `agent-astradeployment-portable-local-1.0/docs/integration/ASTRAINDEXATOR_PROTO_MAPPING.md` and `EXTERNAL_DTO_REFERENCE.md`

---

## 2. Contract authority and purpose

This specification defines the AstraIndexator → AstraVector adapter contract after acquisition, parsing, OCR, normalization and logical fragmentation.

Authority order is normative:

```text
1. llm2/proto/astravector_embedding.proto
   -> actual wire contract

2. agent-astradeployment-portable-local-1.0/docs/integration/*
   -> approved consumer/application mapping

3. AstraIndexator TZ-09/TZ-11
   -> implementation/domain specification
```

AstraIndexator SHALL NOT create a third incompatible interpretation of the DTOs.

Canonical integration boundary:

```text
AstraIndexator domain/canonical DTO
        ↓
anti-corruption mapper + local validation
        ↓
generated astravector.embedding.v1 protobuf DTO
        ↓
gRPC AstraVectorIngestionFacade
        ↓
AstraVector tokenizer-aware chunking / BGE-M3 / PostgreSQL / outbox / Qdrant / retrieval
```

Generated protobuf classes are wire DTOs only and MUST NOT leak through the entire AstraIndexator domain model.

---

## 3. Responsibility boundary

AstraIndexator owns:

- acquisition / parser / OCR / normalization;
- canonical `DocumentElement[]` and `LogicalFragment[]`;
- `LogicalFragment` → `LogicalBlock[]` mapping;
- provenance and source links;
- structural and wire-range validation;
- access-zone intent normalization to one ingestion zone;
- Start/Append/Finalize orchestration;
- client idempotency and replay checkpoints;
- downstream status reconciliation.

AstraVector owns:

- access-zone registry resolution;
- effective TTL policy/lifecycle;
- tokenizer-aware searchable chunking;
- BGE-M3 dense/sparse execution;
- canonical vector/document state;
- outbox/Qdrant projection;
- operation state and searchability;
- retrieval.

AstraIndexator MUST NOT create `PARENT`, `SUB_180`, `SUB_260`, embeddings or Qdrant points directly.

---

## 4. Public wire services

Canonical service:

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

AstraIndexator SHALL use generated clients for this facade rather than hand-maintained transport DTOs.

---

## 5. Application DTO layers

Keep three layers distinct:

```text
Domain DTO
  -> adapter/mapping DTO
  -> generated protobuf DTO
```

Recommended application command shapes follow the approved deployment mapping.

```text
StartIndexingCommand
LogicalBlockBatch
FinalizeIndexingCommand
AbortIndexingCommand
IngestionSessionStatus
DocumentVectorStatus
VectorSyncStatus
```

These are application concepts; field numbers and exact wire scalar widths come from generated protobuf.

---

## 6. Document identity

Canonical AstraIndexator identity:

```text
documentId      = stable UUID/string identity of one logical document
documentVersion = positive numeric version
```

A changed source revision receives a new positive numeric `documentVersion`.

If an upstream system also has an opaque/string revision, preserve it as metadata such as `externalRevision`; it SHALL NOT become the AstraVector wire `document_version`.

### 6.1 Actual llm2 wire widths

The current wire contract is intentionally not uniform:

```text
DocumentIdentity.document_version                   = uint64
DocumentRef.document_version                        = uint64
StartLogicalDocumentIngestionRequest.document_version = uint32
```

Therefore the AstraIndexator domain MAY represent `documentVersion` as a positive signed 64-bit integer, but session Start adapter MUST validate:

```text
1 <= documentVersion <= 4_294_967_295
```

before mapping to the current `uint32` Start field.

The adapter MUST NOT truncate, wrap or unchecked-cast an out-of-range version.

If this wire mismatch is removed in a future llm2 contract revision, contract tests SHALL be updated before deleting the guard.

### 6.2 Other unsigned wire guards

Current session wire fields requiring explicit non-negative/range validation include:

```text
Start.document_version      -> uint32
Start.total_blocks_estimate -> uint32
Start.total_pages_estimate  -> uint32
Start.ttl_days              -> uint32
Append.batch_index          -> uint32
LogicalBlock.order_index    -> uint32
SourceLocation numeric fields -> uint32
```

`total_bytes_estimate`, `received_blocks`, `received_bytes` are wider `uint64` fields where defined by the proto.

Domain code SHALL use explicit mapper guards rather than rely on language-specific unsigned conversions.

---

## 7. LogicalBlock contract

Actual current AstraVector wire shape:

```text
block_id
parent_block_id
block_type
text
order_index
source_location
source_links
metadata
```

Supported external block types:

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

`UNSPECIFIED` MUST NOT be emitted.

Local structural validator SHALL require:

1. non-empty block set;
2. exactly one root `DOCUMENT` block;
3. unique non-blank block IDs;
4. every non-root parent exists;
5. no hierarchy cycles;
6. deterministic order indexes;
7. non-blank text where required;
8. supported parent/child structure;
9. metadata/source links inside configured bounds.

Server validation remains authoritative.

---

## 8. TZ-09 mapping boundary

Canonical conversion:

```text
ParsedDocument / DocumentElement[] / LogicalFragment[]
        ↓
LogicalBlockMapper
        ↓
LogicalBlock[]
```

Simple prose may map one fragment to one block. Structured content may create multiple blocks, for example TABLE + TABLE_ROW or LIST + LIST_ITEM.

`fragmentId` MAY be used as or contribute to a deterministic `block_id` when mapping rules require it, but it MUST NOT be confused with AstraVector-generated searchable chunk IDs.

AstraVector internal/source/chunk topology is outside this DTO boundary.

---

## 9. SourceLocation contract

Current wire fields:

```text
page_start
page_end
char_start
char_end
section_path
heading
table_id
row_index
column_index
```

Current proto3 numeric scalar fields are non-`optional`. Therefore AstraIndexator domain provenance SHOULD preserve nullable/optional semantics internally, and the adapter SHALL use the documented compatibility convention:

```text
0 = unknown / unavailable / not applicable on the current wire
1+ = actual one-based page/row/column value where that locator is defined
```

Character offsets follow the canonical parser/normalizer convention documented by TZ-05/TZ-07 and MUST be contract-tested.

The adapter SHALL NOT fabricate a page, row or column merely to avoid zero.

A future llm2 proto may replace this convention with `optional` scalar presence; until then, the zero convention is a compatibility rule and test requirement.

---

## 10. SourceLink contract

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

Domain code MAY use typed values such as `Instant` for expiration; adapter serializes the format expected by the wire contract.

`SourceLink.expires_at` is source-link lifetime and MUST NOT be confused with document TTL.

Source links/attributes SHALL NOT contain credentials, API keys or bearer tokens.

---

## 11. Access-zone application contract

The approved application abstraction remains:

```text
AccessZoneRef
  |- AccessZoneId(UUID)
  `- AccessZoneCode(String)
```

Producer compatibility may accept:

```text
accessZoneId
accessZoneIds[]
accessZoneCode
accessZoneCodes[]
```

but one ingestion operation MUST normalize to exactly one effective access zone before downstream mutation.

### 11.1 Critical `0000–0999` invariant

The actual `llm2` Access Zone Registry validates codes as exactly four ASCII digits:

```regex
^[0-9]{4}$
```

The full code space is `0000..9999`. The subrange **`0000–0999` is explicitly valid and MUST be preserved exactly as a four-character string**.

Valid examples include:

```text
0000
0001
0010
0100
0999
```

Therefore `AccessZoneCode` SHALL be represented as `String` end-to-end. Parsing it into an integer is forbidden because it destroys the contract:

```text
"0001" -> 1 -> "1"   // INVALID transformation
```

The real `llm2` implementation computes `default_ttl_days = 0` for codes whose numeric value is `<= 999`; when a zone is auto-created, `allow_never_expire` is set when that derived TTL is `0`. This is AstraVector registry policy and MUST NOT be reimplemented as AstraIndexator business logic.

### 11.2 Requested selector versus resolved identity

Current Start/Index requests may send one:

```text
access_zone_id
access_zone_code
```

(or both only when both identify the same zone).

Downstream `DocumentRef` carries:

```text
access_zone_id
document_id
document_version
```

Therefore AstraIndexator SHOULD preserve both:

```text
requestedAccessZoneCode / requestedAccessZoneId
resolvedAccessZoneId
```

and SHOULD retain normalized code in Knowledge Inventory when known.

Conceptual application read model:

```text
EffectiveAccessZone {
  accessZoneId
  accessZoneCode
}
```

`accessZoneId` is downstream canonical identity; `accessZoneCode` remains the operator/business semantic reference.

AstraIndexator MUST NOT derive the registry UUID from code itself as business logic.

### 11.3 Access level is separate

`AccessLevel` values in llm2 include:

```text
PUBLIC
INTERNAL
CONFIDENTIAL
RESTRICTED
```

Access level and access zone are different dimensions. AstraIndexator SHALL NOT derive one from the other.

---

## 12. TTL mapping

Session Start uses:

```text
ttl_days = 0 -> inherit resolved access-zone/platform policy
ttl_days > 0 -> request explicit relative finite lifetime in days
```

For `accessZoneCode` in `0000–0999`, `ttl_days = 0` means **inherit that resolved zone policy**. It does not mean the client itself declares `forever`.

Because the current AstraVector code matrix assigns default TTL `0` and permits never-expire for auto-created zones in this subrange, the effective result may be non-expiring when the resolved active zone policy permits it.

AstraIndexator MUST NOT rewrite this to a positive TTL, MUST NOT compute its own expiry from the code, and MUST NOT interpret request-level `0` as unconditional forever.

Single-call `IndexLogicalDocument` uses separate `TtlPolicy` semantics (`mode`, `ttl_seconds`, `expires_at`). The two contracts MUST NOT be conflated.

AstraVector registry/runtime remains authoritative for effective TTL and expiry.

---

## 13. RequestContext

Current public facade uses `RequestContext` for status/delete and single-call flows:

```text
correlation_id
idempotency_key
caller_service
caller_user_id
caller_access_level
```

Recommended AstraIndexator application concept:

```text
DownstreamRequestContext {
  correlationId
  idempotencyKey
  callerService
  callerAccessLevel
}
```

`callerUserId` may remain empty for internal worker operations unless the platform contract requires it.

`callerAccessLevel` is a visibility input and MUST NOT be confused with the ingestion access-zone selector.

---

## 14. Single-call ingestion DTO

Current wire request:

```text
IndexLogicalDocumentRequest {
  context
  access_zone_id
  access_zone_code
  document: DocumentIdentity
  blocks[]
  chunking_options
  indexing_options
  metadata
}
```

`DocumentIdentity` contains:

```text
external_document_id
document_id
document_version:uint64
title
source_uri
source_type
mime_type
content_hash
source_links[]
```

AstraIndexator SHALL not override AstraVector tokenizer/chunking policy unless an explicitly versioned client profile requires it. Default integration SHOULD favor server-approved chunking/indexing configuration.

---

## 15. Session Start DTO

Approved application shape remains equivalent to:

```text
StartIndexingCommand {
  accessZone: AccessZoneRef
  documentId
  documentVersion
  sourceUri
  fileName
  contentHash
  idempotencyKey
  totalBytesEstimate
  totalBlocksEstimate
  totalPagesEstimate
  metadata
  ttlDays
}
```

Actual wire fields:

```text
access_zone_id
access_zone_code
document_id
document_version:uint32
source_uri
file_name
content_hash
idempotency_key
total_bytes_estimate:uint64
total_blocks_estimate:uint32
total_pages_estimate:uint32
metadata
ttl_days:uint32
```

Recommended stable idempotency key:

```text
astraindexator:{documentId}:{documentVersion}:{contentHash}
```

A retry of the same logical Start MUST reuse the same key.

---

## 16. Append DTO

Approved application shape:

```text
LogicalBlockBatch {
  sessionId
  batchIndex
  blocks[]
  lastBatch
  batchContentHash
}
```

Wire:

```text
ingestion_session_id
blocks[]
batch_index:uint32
is_last_batch
batch_content_hash
```

Rules:

```text
same session + same batchIndex + same hash
-> safe replay identity

same session + same batchIndex + different hash
-> integrity conflict
```

`is_last_batch` remains informational in the current llm2 contract. Explicit Finalize is still required.

---

## 17. Finalize and Abort DTOs

Finalize:

```text
FinalizeIndexingCommand {
  sessionId
  finalContentHash
}
```

Wire:

```text
ingestion_session_id
final_content_hash
```

Abort:

```text
AbortIndexingCommand {
  sessionId
  reason
}
```

Mutating timeout does not prove failure; recovery follows TZ-13.

---

## 18. Session status DTO

Session status is a session-lifecycle DTO and MUST remain distinct from document/vector status.

Approved application representation SHOULD include:

```text
IngestionSessionStatus {
  ingestionSessionId
  state
  rawStatus
  receivedBatches
  receivedBlocks
  receivedBytes
  expiresAt
  errorCode
  errorMessage
}
```

Known wire strings:

```text
ACTIVE
FINALIZING
COMPLETED
FAILED
ABORTED
EXPIRED
```

Application enum MUST add `UNKNOWN`; raw value is retained for forward-compatible diagnostics.

`expiresAt` here is ingestion-session expiration, NOT knowledge TTL expiry.

---

## 19. DocumentRef and resolved identity

AstraVector status/delete responses use:

```text
DocumentRef {
  access_zone_id
  document_id
  document_version:uint64
}
```

This is authoritative downstream reference for later status/delete/reconciliation operations.

AstraIndexator SHALL persist or deterministically recover the resolved `DocumentRef` once returned. A requested `accessZoneCode` alone is not sufficient for later lifecycle operations when the downstream contract requires `access_zone_id`.

---

## 20. DocumentVectorStatus DTO

Document vector/readiness status is separate from session status.

Application model SHOULD mirror the stable semantics of:

```text
DocumentVectorStatus {
  state
  progressPercent
  searchable
  message
  readyToActivate
  sync: VectorSyncStatus
}
```

Current `OperationState` includes:

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

Application adapters SHALL keep an UNKNOWN/UNSPECIFIED-safe path for future additive enum values.

`searchable=true` is the authoritative completion gate used by AstraIndexator job lifecycle.

---

## 21. VectorSyncStatus DTO

Knowledge Inventory/reconciliation SHOULD expose downstream sync evidence without querying Qdrant directly.

Application `VectorSyncStatus` should cover current wire evidence including:

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

This is downstream observability evidence, not permission for AstraIndexator to query or repair Qdrant directly.

---

## 22. Session state handling

Current session `status` is a wire string. Known values:

```text
ACTIVE
FINALIZING
COMPLETED
FAILED
ABORTED
EXPIRED
```

Map to an application enum with `UNKNOWN`, while retaining raw status.

A session `COMPLETED` is not sufficient proof of searchable knowledge.

---

## 23. Completion levels

AstraIndexator SHALL distinguish:

```text
SESSION_ACCEPTED
BLOCKS_STAGED
FINALIZED
VECTOR_READY
SEARCHABLE
FAILED
```

Only verified downstream:

```text
GetDocumentVectorStatus.status.searchable == true
```

allows the normal indexing job to transition to `COMPLETED`.

---

## 24. Hashing contract

Current session wire requires:

```text
batch_content_hash
final_content_hash
```

DTO placement is agreed. Byte-exact canonicalization remains a cross-service contract gate until llm2 publishes authoritative canonical representation and shared golden fixtures.

Required evidence:

```text
same LogicalBlock/batch content
-> same canonical bytes
-> same SHA-256
-> same lowercase hex
```

across the AstraVector implementation and AstraIndexator implementation.

AstraIndexator SHALL NOT reverse-engineer a private Rust serialization and treat it as permanent wire policy.

---

## 25. Retry and ambiguous outcomes

Mutation retry follows TZ-13:

```text
Start timeout
-> retry SAME idempotency key

Append timeout
-> replay SAME sessionId + batchIndex + batchContentHash

Finalize timeout
-> query session status before unsafe replay/new version

UNAVAILABLE
-> bounded backoff

INVALID_ARGUMENT / permanent policy errors
-> no blind retry
```

AstraIndexator must reconcile ambiguous results rather than create duplicate document versions.

---

## 26. Contract pinning

AstraIndexator release evidence SHALL pin/record:

```text
llm2 protobuf revision / generated client version
supported block types
unsigned wire guards
access-zone/TTL semantics
hash canonicalization revision once published
configured server limits relevant to batching
```

Breaking semantic changes require an explicit contract revision and contract-test update.

---

## 27. Required contract tests

TZ-17 SHALL include at minimum:

1. `documentVersion` session range guard;
2. every current `uint32` adapter bound;
3. valid `LogicalBlock` tree;
4. duplicate block ID rejection;
5. missing parent/cycle rejection;
6. all supported block type mappings;
7. SourceLocation zero/presence convention;
8. source link mapping;
9. access zone by code;
10. **leading-zero Access Zone codes across the full `0000–0999` compatibility subrange, including `0000`, `0001`, `0010`, `0100`, `0999`;**
11. rejection of malformed code `1`, `999`, `10000`, non-digits;
12. code/id consistency failure;
13. one-zone ingestion normalization;
14. `ttlDays=0` inheritance for a `0000–0999` zone without client-side coercion;
15. explicit finite TTL;
16. Start retry same idempotency key;
17. Append replay same batch/hash;
18. same batch index different hash conflict;
19. Finalize ambiguity reconciliation;
20. session status UNKNOWN fallback;
21. session expiry not treated as document expiry;
22. requested zone code → resolved `DocumentRef.access_zone_id` persistence;
23. `DocumentVectorStatus` mapping;
24. `VectorSyncStatus` mapping;
25. searchable completion proof;
26. shared hash golden fixture once available.

---

## 28. Acceptance criteria

- **AC-01:** generated llm2 protobuf is the wire source of truth;
- **AC-02:** deployment integration docs are the approved application mapping;
- **AC-03:** AstraIndexator does not maintain an independent parallel wire DTO contract;
- **AC-04:** canonical `documentVersion` is positive numeric and current session `uint32` guard is explicit;
- **AC-05:** all unsigned wire conversions are validated;
- **AC-06:** canonical output maps to current `LogicalBlock[]` types only;
- **AC-07:** no legacy v004 `SOURCE/PARENT/SUB_*` API appears as AstraIndexator ingestion responsibility;
- **AC-08:** `accessZoneCode` remains a four-character string, and `0000–0999` is explicitly supported without loss of leading zeroes;
- **AC-09:** `ttl_days=0` remains inheritance, including for `0000–0999`; effective non-expiry is downstream policy, not a client declaration;
- **AC-10:** requested code and resolved downstream zone ID are both traceable;
- **AC-11:** SourceLocation presence convention is explicit and tested;
- **AC-12:** session status and document/vector status remain separate;
- **AC-13:** Knowledge Inventory can expose current downstream sync evidence without direct Qdrant access;
- **AC-14:** mutating timeout recovery is idempotency/reconciliation based;
- **AC-15:** job completion requires `searchable=true`;
- **AC-16:** exact session hashing remains blocked until authoritative golden vectors exist;
- **AC-17:** contract tests pin selected llm2 revision and all approved consumer mappings.
