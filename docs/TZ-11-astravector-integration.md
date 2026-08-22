# TZ-11 — AstraVector Integration

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-11
- **Title:** AstraVector Integration
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-02, TZ-08, TZ-09, TZ-10, TZ-12, TZ-13, TZ-14, TZ-16, TZ-17, TZ-18
- **Authoritative downstream wire contract:** `astravector.embedding.v1.AstraVectorIngestionFacade` from `alimbetov/llm2/main`
- **Consumer references:** `agent-astradeployment-portable-local-1.0/docs/integration/*`

---

## 2. Purpose

This specification defines the integration contract between AstraIndexator and AstraVector after AstraIndexator has acquired, parsed, OCR-processed, normalized and structurally fragmented a source document.

The canonical integration boundary is the existing AstraVector public gRPC facade:

```text
astravector.embedding.v1.AstraVectorIngestionFacade
```

AstraIndexator SHALL NOT create a duplicate custom REST or gRPC ingestion protocol unless a separate architecture decision explicitly replaces the facade contract.

The canonical boundary is:

```text
AstraIndexator domain model
        |
        | anti-corruption mapping + local validation
        v
Generated AstraVector protobuf DTOs
        |
        | gRPC
        v
AstraVectorIngestionFacade
        |
        +--> tokenizer-aware chunking
        +--> BGE-M3 dense/sparse embedding
        +--> canonical PostgreSQL state
        +--> outbox/Qdrant projection
        +--> activation/reconciliation
        `--> retrieval readiness
```

---

## 3. Responsibility boundary

### 3.1 AstraIndexator owns

- source acquisition from SeaweedFS;
- parser/OCR/normalization;
- document structure reconstruction;
- deterministic canonical IDs;
- multilingual logical fragmentation;
- conversion to AstraVector `LogicalBlock[]`;
- source locations and source links;
- local structural validation;
- ingestion-path selection;
- session orchestration;
- deterministic idempotency identity;
- retry/reconciliation decisions at the client boundary;
- durable delivery checkpoints in PostgreSQL;
- mapping downstream state into AstraIndexator job progress.

### 3.2 AstraVector owns

- tokenization;
- tokenizer-aware chunking;
- BGE-M3 model execution;
- dense/sparse representation generation;
- canonical document/vector state;
- outbox and Qdrant projection;
- vector synchronization;
- activation/reconciliation;
- access-zone registry resolution;
- effective TTL lifecycle;
- searchability/readiness determination;
- retrieval.

### 3.3 Explicit non-goal

AstraIndexator MUST NOT create `PARENT`, `SUB_180`, `SUB_260`, embeddings or Qdrant points directly.

---

## 4. Canonical gRPC services used

AstraIndexator integrates with:

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

Delete/reindex lifecycle semantics are completed in TZ-12; this document only defines the integration surface required by indexing.

---

## 5. Transport and adapter rule

AstraIndexator SHALL keep three DTO layers distinct:

```text
Canonical/domain DTO
        |
        v
AstraVector adapter DTO/mapping layer
        |
        v
Generated protobuf classes
```

Generated protobuf classes MUST NOT leak through the entire AstraIndexator domain model.

The adapter SHALL be the only component allowed to depend directly on generated AstraVector protobuf classes.

---

## 6. Document identity mapping

| AstraIndexator | AstraVector | Rule |
|---|---|---|
| `documentId` | `document_id` | Stable logical document UUID/string policy defined by platform; unchanged across retries |
| `documentVersion` | `document_version` | Must map to AstraVector `uint64 > 0`; mapping strategy MUST be deterministic and persisted if producer version is non-numeric |
| upstream business ID | `external_document_id` | Optional; does not replace canonical document identity |
| source title | `title` | Optional display/provenance field |
| source URI/reference | `source_uri` | Provenance/navigation, not primary identity |
| actual MIME type | `mime_type` | From validated acquisition result |
| source/content fingerprint | `content_hash` | Stable hash defined by source/canonical contract |
| source links | `source_links` | Navigation/provenance; no credentials/secrets |

### 6.1 `documentVersion` compatibility requirement

TZ-01/TZ-09 currently allow producer-visible opaque versions. AstraVector facade uses numeric `uint64 document_version`.

Therefore implementation SHALL choose one of the following before production:

1. require producer versions to be positive numeric values; or
2. maintain an authoritative persisted mapping from producer version string to AstraVector numeric version.

The mapping MUST be stable across retries/restarts and MUST NOT be recomputed from process-local state.

This is a P0 integration decision for implementation.

---

## 7. Canonical document → LogicalBlock mapping

AstraVector public ingestion accepts ordered `LogicalBlock[]`.

AstraIndexator SHALL map its canonical document model into the AstraVector block model without flattening useful structure.

AstraVector block contract:

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

### 7.1 Canonical hierarchy

The preferred mapping is:

```text
DOCUMENT root
  |- SECTION
  |    |- SUBSECTION
  |    |    |- PARAGRAPH
  |    |    |- LIST
  |    |    |    `- LIST_ITEM
  |    |    `- TABLE
  |    |         `- TABLE_ROW
  |    `- PARAGRAPH
  `- SECTION
```

AstraIndexator SHALL create exactly one root `DOCUMENT` block per indexed document version.

### 7.2 Block type mapping

| AstraIndexator element/fragment | AstraVector `BlockType` |
|---|---|
| document root | `DOCUMENT` |
| chapter/section | `SECTION` |
| subsection | `SUBSECTION` |
| paragraph/logical prose block | `PARAGRAPH` |
| table | `TABLE` |
| table row | `TABLE_ROW` |
| list | `LIST` |
| list item | `LIST_ITEM` |
| FAQ pair/item | `FAQ_ITEM` |
| code block | `CODE_BLOCK` |
| caption | `CAPTION` |

`BLOCK_TYPE_UNSPECIFIED` MUST NEVER be emitted.

### 7.3 Images/OCR

The AstraVector facade does not expose a dedicated image block type in the current contract.

Therefore:

- raw image identity/provenance remains in AstraIndexator canonical artifacts;
- OCR-derived useful text maps to the nearest valid logical text block type, normally `PARAGRAPH`, `TABLE`, `TABLE_ROW` or `CAPTION` depending on structure;
- `originElementId`, OCR provenance/model/version and image/page linkage SHALL be propagated through metadata/source location where safe and bounded;
- raw binary image content MUST NOT be embedded into LogicalBlock metadata.

### 7.4 LogicalFragment relationship

`LogicalFragment` remains an AstraIndexator internal semantic container. AstraVector `LogicalBlock` is the public ingestion representation.

Mapping MAY be one-to-one for simple prose, but is not required to be one-to-one for structured sections, tables or lists.

AstraIndexator SHALL NOT equate `fragmentId` with AstraVector generated chunk IDs.

---

## 8. Local structural validation

Before any network mutation, AstraIndexator MUST validate:

1. block collection is non-empty;
2. exactly one root `DOCUMENT` block exists;
3. every `block_id` is non-blank;
4. block IDs are unique within document version;
5. every non-root block references an existing parent;
6. hierarchy is acyclic;
7. block type is not `UNSPECIFIED`;
8. text is non-blank for block types that require text;
9. `order_index` is deterministic;
10. parent/child ordering is deterministic;
11. source locations are internally consistent when supplied;
12. metadata size/count limits are respected;
13. access-zone input resolves to one effective ingestion zone according to TZ-10.

Server validation remains authoritative.

Local validation exists to reject deterministic client defects before creating an external partial operation.

---

## 9. SourceLocation mapping

AstraIndexator SHALL preserve source provenance into:

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

when those values are available and meaningful.

Rules:

- unknown values MAY remain unset/zero according to protobuf semantics;
- ranges MUST NOT be fabricated;
- OCR-derived blocks SHOULD preserve source page/region context;
- table rows SHOULD retain `table_id` and `row_index`;
- citations later returned by AstraVector depend on this provenance.

---

## 10. SourceLink mapping

Supported source links MAY include:

```text
ORIGINAL_DOCUMENT
PREVIEW
DOWNLOAD
PAGE
SECTION
CHUNK
EXTERNAL_SYSTEM
```

AstraIndexator SHALL NOT place:

- credentials;
- API keys;
- bearer tokens;
- database secrets;
- permanent privileged signed URLs

inside source links or metadata.

If a source URL requires authorization, use the existing descriptor semantics (`requires_auth`) and external resolver/gateway policy.

---

## 11. Access-zone mapping

TZ-10 is normative.

For ingestion, one document version belongs to exactly one effective access zone.

AstraIndexator MAY receive compatibility fields:

```text
accessZoneId
accessZoneIds[]
accessZoneCode
accessZoneCodes[]
```

but before ingestion they MUST normalize to exactly one effective zone selector.

The adapter sends one of:

```text
access_zone_id
access_zone_code
```

or both only when intentionally performing consistency validation and when both represent the same registry zone.

AstraIndexator MUST NOT fan out one document version into multiple access zones automatically.

---

## 12. TTL mapping

TZ-10 is normative.

### 12.1 Session ingestion

`StartLogicalDocumentIngestion` uses:

```text
ttl_days = 0  -> inherit AstraVector zone/platform TTL policy
ttl_days > 0  -> explicit finite relative TTL in days
```

`0` MUST NOT be interpreted as `never expire` by AstraIndexator.

### 12.2 Single-call ingestion

`IndexLogicalDocument` exposes `TtlPolicy` with:

```text
TTL_MODE_NONE
TTL_MODE_RELATIVE + ttl_seconds
TTL_MODE_ABSOLUTE + expires_at
```

However current integration documentation states that absolute TTL support is not yet stable enough for external production guarantees.

AstraIndexator 1.0 SHALL NOT promise exact `TTL_MODE_ABSOLUTE` semantics until AstraVector publishes/implements a stable cross-service contract for it.

### 12.3 No code→TTL duplication

AstraIndexator SHALL NOT calculate effective TTL by duplicating AstraVector access-zone code ranges. Effective policy remains registry-owned.

---

## 13. Ingestion path selection

AstraIndexator supports two AstraVector public ingestion paths.

### 13.1 Single-call path

Use:

```text
IndexLogicalDocument
```

for documents whose serialized request comfortably fits AstraVector configured request limits and operational policy.

The selection threshold MUST be configurable and lower than server hard maxima.

The client MUST NOT target exactly the server maximum because protobuf overhead, metadata and future additive fields consume additional bytes.

### 13.2 Session path

Use session ingestion for large documents and as the production-oriented scalable path:

```text
StartLogicalDocumentIngestion
    -> AppendLogicalDocumentBlocks x N
    -> FinalizeLogicalDocumentIngestion
    -> GetLogicalDocumentIngestionStatus
    -> GetDocumentVectorStatus
```

Session ingestion is REQUIRED whenever the single-call serialized payload exceeds the configured safe client threshold or when large-document policy requires chunked ingestion.

### 13.3 Current AstraVector server limits to respect dynamically

Current deployment defaults include approximately:

```text
single_request_max_bytes          = 2 MiB
chunked max batch bytes           = 1 MiB
chunked max blocks per batch      = 500
session TTL                       = 3600 s
max concurrent ingestion sessions= 1000
max sessions per access zone      = 100
max sessions per document         = 3
max blocks per document           = 100000
max chunks per document           = 50000
```

These are deployment/configuration values, NOT permanent protocol constants.

AstraIndexator SHOULD configure conservative client targets and MUST handle `RESOURCE_EXHAUSTED`/limit errors without assuming hard-coded defaults remain unchanged.

---

## 14. Single-call ingestion flow

Canonical flow:

```text
validate canonical blocks
        |
        v
build IndexLogicalDocumentRequest
        |
        v
IndexLogicalDocument
        |
        v
IndexLogicalDocumentResponse
        |
        v
GetDocumentVectorStatus as required by completion policy
```

The request includes:

- request context/correlation/idempotency;
- one access-zone selector;
- document identity;
- ordered `LogicalBlock[]`;
- AstraVector chunking options only when explicitly configured;
- indexing options/TTL policy;
- bounded metadata.

A successful mutation response MUST NOT automatically be treated as proof that Qdrant projection is fully searchable unless the response state explicitly guarantees that condition.

---

## 15. Session ingestion flow

### 15.1 Start

Request fields currently include:

```text
access_zone_id
access_zone_code
document_id
document_version
source_uri
file_name
content_hash
idempotency_key
total_bytes_estimate
total_blocks_estimate
total_pages_estimate
metadata
ttl_days
```

AstraIndexator SHOULD supply realistic estimates when known.

### 15.2 Append

Each batch contains:

```text
ingestion_session_id
blocks[]
batch_index
is_last_batch
batch_content_hash
```

Rules:

- `batch_index` is deterministic and monotonic from zero or one chosen convention; implementation MUST use one convention consistently;
- same batch index + same content hash is the intended replay identity;
- same batch index + different hash is an integrity conflict;
- `is_last_batch` is informational in current implementation;
- `FinalizeLogicalDocumentIngestion` remains mandatory.

### 15.3 Finalize

Request:

```text
ingestion_session_id
final_content_hash
```

Finalize acceptance does not equal confirmed searchability.

### 15.4 Status

AstraIndexator uses:

```text
GetLogicalDocumentIngestionStatus
```

to reconcile session state and:

```text
GetDocumentVectorStatus
```

to determine document/vector readiness and searchability.

---

## 16. Session state machine

Current server-visible strings:

```text
ACTIVE
FINALIZING
COMPLETED
FAILED
ABORTED
EXPIRED
```

AstraIndexator SHALL model them defensively:

```text
ACTIVE
FINALIZING
COMPLETED
FAILED
ABORTED
EXPIRED
UNKNOWN
```

Unknown future state values MUST NOT crash deserialization or be silently interpreted as success.

Canonical state flow:

```text
Start
  |
  v
ACTIVE
  |\
  | \-- Abort --> ABORTED
  |
  |---- session expiry --> EXPIRED
  |
 Append x N
  |
 Finalize
  |
  v
FINALIZING
  |        \
  v         v
COMPLETED  FAILED
```

Session expiry is NOT document TTL expiry.

---

## 17. Idempotency model

The reliability model is:

```text
at-least-once client execution
+
idempotent/reconcilable AstraVector mutations
```

### 17.1 Start idempotency

Recommended logical key:

```text
astraindexator:{documentId}:{documentVersion}:{contentHash}
```

The exact serialized format MAY change, but the key MUST be deterministic for one logical indexing operation.

Rules:

- same operation retry reuses the same key;
- timeout MUST NOT cause generation of a fresh key;
- same key + different logical request fingerprint is a conflict.

### 17.2 Append idempotency

Replay identity:

```text
ingestion_session_id + batch_index + batch_content_hash
```

Timeout on Append MAY be retried only with the same batch index and same canonical batch hash.

### 17.3 Finalize idempotency/reconciliation

After ambiguous Finalize timeout:

```text
GetLogicalDocumentIngestionStatus
```

MUST be called before blind recreation of the operation.

Do not create a new document version solely because Finalize acknowledgement was lost.

---

## 18. P0 contract gap — canonical hashing

Session ingestion currently requires:

```text
batch_content_hash
final_content_hash
```

The exact cross-language canonical byte representation is not yet sufficiently published for independent client reimplementation.

AstraIndexator production session ingestion SHALL NOT freeze a guessed Python serialization algorithm.

Before strict production interoperability is declared, AstraVector MUST publish or expose:

1. exact included fields;
2. exact field ordering;
3. text normalization rules;
4. metadata ordering rules;
5. UTF-8 byte representation;
6. hash algorithm (expected SHA-256);
7. lowercase/uppercase hex representation;
8. golden fixtures with input and expected digest;
9. parity tests for Rust and Python clients.

Until this artifact exists, `batch_content_hash`/`final_content_hash` handling remains a **P0 integration blocker** for independently implemented strict session hashing.

TZ-17 SHALL include golden-vector parity tests once the contract is published.

---

## 19. Durable delivery checkpoint model

TZ-02 defines durable coordination. TZ-11 refines downstream checkpoints.

AstraIndexator SHALL persist enough state to resume without reparsing/re-uploading blindly.

Recommended logical fields for job delivery state:

```text
ingestion_mode                SINGLE | SESSION
ingestion_idempotency_key
ingestion_session_id
next_batch_index
last_accepted_batch_index
final_content_hash
session_status_raw
vector_state_raw
searchable
last_downstream_error_code
last_downstream_error_message
last_downstream_check_at
```

For session mode, batch-level persistence SHOULD include at least:

```text
job_id
batch_index
batch_content_hash
block_count
serialized_bytes
status
attempt_count
accepted_at
last_error
```

This can be implemented as a dedicated delivery table or equivalent durable checkpoint model in TZ-02/TZ-13 migrations.

---

## 20. Batching algorithm

AstraIndexator SHALL create deterministic batches from ordered LogicalBlocks.

Batching constraints:

```text
max_blocks_per_client_batch
max_serialized_bytes_per_client_batch
```

A batch closes before adding a block that would exceed either configured client guard.

Requirements:

- preserve logical block order;
- do not split one LogicalBlock merely to satisfy batch byte size; if one block alone exceeds admissible request limits, treat it as an upstream fragmentation/validation defect requiring deterministic handling;
- batch boundaries MUST be deterministic for identical block stream and configuration;
- retries MUST reconstruct identical batch boundaries and hashes;
- client batch size SHOULD stay comfortably below server maxima.

Example baseline policy MAY start near half of server maxima, but production values remain configurable and are not normative constants.

---

## 21. Backpressure and concurrency

AstraIndexator MUST bound downstream concurrency.

Recommended independent limits:

```text
max_concurrent_sessions_per_worker
max_in_flight_append_requests
max_in_flight_single_ingestions
max_in_flight_status_requests
```

The client MUST respect AstraVector capacity signals such as `RESOURCE_EXHAUSTED`.

Do not allow one huge document to monopolize all downstream slots indefinitely; fairness policy belongs to runtime configuration/TZ-18.

---

## 22. gRPC deadlines

AstraIndexator SHALL configure operation-specific deadlines rather than one global timeout.

Suggested classes:

```text
Start/Append          bounded mutation deadline
Finalize              longer mutation deadline
GetSessionStatus      short read deadline
GetDocumentVectorStatus short/medium read deadline
Single document index longer mutation deadline
```

Actual values SHALL be configurable and aligned with AstraVector deployment settings.

A deadline expiry on a mutation is an **ambiguous outcome**, not proof of failure.

---

## 23. Retry classification

### 23.1 Retryable transport/capacity errors

Typically retry/reconcile:

```text
UNAVAILABLE
DEADLINE_EXCEEDED
RESOURCE_EXHAUSTED (capacity/transient)
```

using bounded exponential backoff + jitter.

### 23.2 Permanent request/security errors

Do not blindly retry:

```text
INVALID_ARGUMENT
OUT_OF_RANGE
PERMISSION_DENIED
UNAUTHENTICATED
```

### 23.3 State/integrity errors

Require reconciliation or correction:

```text
FAILED_PRECONDITION
ABORTED
ALREADY_EXISTS
NOT_FOUND
```

The adapter SHOULD retain raw gRPC status and structured server details when available.

---

## 24. Mutation-specific recovery matrix

| Failure point | Required behavior |
|---|---|
| Start timeout | retry same idempotency key or reconcile known session; never generate new logical operation blindly |
| Append timeout | resend same `batch_index` + same hash |
| Append hash conflict | permanent integrity failure until payload/hash mismatch is corrected |
| Finalize timeout | call session status first |
| Finalize returns `FINALIZING`/`ABORTED` state conflict | poll/reconcile; do not create another version |
| Session `COMPLETED` | proceed to vector status |
| Session `FAILED` | classify error; retry only if contract says recoverable |
| Session `EXPIRED` | recover according to TZ-13; do not assume staged blocks still exist |
| Vector status not searchable yet | continue bounded polling/reconciliation |
| AstraVector unavailable | keep job durable and move to retry according to TZ-02/TZ-13 |

---

## 25. Completion semantics

AstraIndexator SHALL distinguish at least these internal completion levels:

```text
DOWNSTREAM_SESSION_ACCEPTED
BLOCKS_STAGED
FINALIZE_ACCEPTED
VECTOR_READY
SEARCHABLE
```

Job `COMPLETED` SHALL NOT be derived solely from successful Start or Append.

Baseline AstraIndexator 1.0 completion criterion:

> A job may transition to `COMPLETED` only after AstraVector reports the document vector state as searchable/active according to `GetDocumentVectorStatus`, unless a future explicit business policy defines a weaker completion level.

Any weaker policy MUST be explicitly documented and must not be named `COMPLETED` without qualification.

---

## 26. GetDocumentVectorStatus authority

`GetDocumentVectorStatus` is the authoritative consumer-facing readiness check.

Relevant fields include:

```text
state
progress_percent
searchable
message
ready_to_activate
sync
```

AstraIndexator SHALL persist raw state for diagnostics and map known values into its own processing stage without assuming unknown future values are success.

---

## 27. Activation semantics

AstraVector internally owns activation and synchronization.

Current integration documentation notes that session finalize/auto-activation behavior is not fully identical to the low-level manual activation flow.

Therefore AstraIndexator:

- SHALL NOT call low-level v004 activation APIs as part of the public-facade baseline;
- SHALL rely on public facade state/readiness;
- SHALL not report searchable before `GetDocumentVectorStatus` indicates it;
- SHALL treat future facade activation changes as AstraVector contract evolution, not duplicate lifecycle logic locally.

---

## 28. Large-document behavior

Large documents MUST be processed in bounded memory.

Canonical path:

```text
prepared fragments/elements JSONL
        |
        v streaming iterator
LogicalBlock mapper
        |
        v deterministic bounded batches
AppendLogicalDocumentBlocks
```

AstraIndexator MUST NOT require loading all blocks into RAM solely to perform session delivery.

Prepared artifacts from TZ-09 SHOULD allow delivery replay without rerunning parser/OCR when their schema/version integrity is valid.

---

## 29. Metadata propagation

Only an allowlisted subset of AstraIndexator metadata SHOULD be forwarded to AstraVector.

Recommended classes:

- parser/splitter version;
- fragment/source element IDs needed for traceability;
- language metadata;
- source business type/reference;
- OCR provenance identifiers;
- section hierarchy hints.

Do not blindly copy arbitrary producer metadata into AstraVector.

Metadata limits and security constraints belong to TZ-16/TZ-18.

---

## 30. Correlation and tracing

Every downstream operation SHOULD propagate:

```text
correlation_id
idempotency_key
caller_service = astra-indexator
```

Logs SHALL correlate at least:

```text
jobId
documentId
documentVersion
processingAttemptId
workerId
ingestionSessionId (when present)
batchIndex (when present)
correlationId
```

Do not expose high-cardinality identifiers as unrestricted metrics labels.

---

## 31. Security boundary

AstraIndexator obtains AstraVector connection/security configuration from runtime secrets/configuration.

Requirements:

- no API key in job payload;
- no credentials in source links;
- TLS/mTLS/gateway trust policy externalized;
- authentication metadata injected through a gRPC client interceptor or equivalent adapter layer;
- `callerAccessLevel` and trusted identity headers must not be derived directly from untrusted end-user input;
- network policy SHOULD prevent bypassing the intended trusted service path in production.

Detailed security design belongs to TZ-16.

---

## 32. Configuration baseline

AstraIndexator adapter configuration SHOULD include:

```text
astravector.endpoint
astravector.security.*
astravector.deadlines.*
astravector.retry.*
astravector.ingestion.single_call_safe_max_bytes
astravector.ingestion.session.max_batch_bytes
astravector.ingestion.session.max_blocks_per_batch
astravector.ingestion.max_in_flight_append
astravector.status.poll_interval
astravector.status.max_wait
```

Client values MUST be deployment-configurable.

No server deployment default shall be treated as an eternal protocol constant.

---

## 33. Version compatibility

The generated protobuf contract is authoritative.

AstraIndexator SHALL pin a known compatible AstraVector proto/version during build/deployment and provide contract tests against that version.

Backward-compatible additive protobuf fields MUST NOT break the client.

Unknown enum/string states MUST have safe handling.

Breaking protobuf or semantic changes require explicit compatibility review and TZ-11 update before deployment.

---

## 34. Error model persisted by AstraIndexator

For downstream failures AstraIndexator SHOULD preserve:

```text
operation
wire_status_code
server_error_code (when available)
server_message
retryable classification
session_id
batch_index
attempt_no
occurred_at
correlation_id
```

Secrets and full document text MUST NOT be logged in errors by default.

---

## 35. End-to-end sequence — session path

```text
AstraIndexator                 AstraVector
      |                            |
      | Start(same idem key)       |
      |--------------------------->|
      |<---------------------------| sessionId ACTIVE
      |                            |
      | Append batch 0 + hash      |
      |--------------------------->|
      |<---------------------------| accepted
      |                            |
      | Append batch 1 + hash      |
      |--------------------------->|
      |<---------------------------| accepted
      |                            |
      | ...                        |
      |                            |
      | Finalize + final hash      |
      |--------------------------->|
      |<---------------------------| accepted/finalizing
      |                            |
      | GetSessionStatus           |
      |--------------------------->|
      |<---------------------------| COMPLETED
      |                            |
      | GetDocumentVectorStatus    |
      |--------------------------->|
      |<---------------------------| searchable=true
      |                            |
      | mark job COMPLETED         |
```

---

## 36. Golden ambiguous-failure scenario

TZ-11 implementation MUST prove:

```text
1. Start succeeds server-side.
2. Client loses Start response.
3. Client retries with SAME idempotency key.
4. Same logical session is recovered/reused.
5. Append batch N succeeds server-side.
6. Client loses Append ACK.
7. Client replays same batch index + same hash.
8. Server does not duplicate staged logical content.
9. Finalize succeeds server-side.
10. Client loses Finalize response.
11. Client queries session status instead of creating a new version.
12. Session reaches COMPLETED.
13. Vector status eventually reports searchable=true.
14. AstraIndexator marks job COMPLETED exactly once in durable state.
```

---

## 37. Acceptance criteria

### AC-01 — Public facade only
AstraIndexator ingestion uses generated `AstraVectorIngestionFacade` client, not a duplicated custom ingestion API.

### AC-02 — Stable document identity
Retries preserve the same `document_id`, `document_version`, content identity and logical idempotency key.

### AC-03 — One-zone ingestion
Each indexed document version is submitted into exactly one effective access zone according to TZ-10.

### AC-04 — Structural validation
Malformed LogicalBlock trees are rejected locally before network mutation.

### AC-05 — Root invariant
Exactly one `DOCUMENT` root is emitted.

### AC-06 — Provenance preservation
Page/section/table provenance survives mapping and is visible in retrieval citations for supported fixtures.

### AC-07 — Deterministic batching
Identical canonical block stream + same batching configuration yields identical batch boundaries and indexes.

### AC-08 — Start replay
A lost Start ACK is recovered with the same idempotency key without creating a second logical session/document operation.

### AC-09 — Append replay
A lost Append ACK is recovered by replaying the same batch index/hash without duplicate staging.

### AC-10 — Append conflict
Same batch index with different content/hash is treated as an integrity failure.

### AC-11 — Finalize ambiguity
Finalize timeout triggers status reconciliation before retry/new operation decisions.

### AC-12 — Searchability gate
AstraIndexator does not mark job COMPLETED before AstraVector reports agreed searchable/readiness state.

### AC-13 — Large document bounded memory
A large prepared document can be delivered via streaming/batching without loading all blocks in memory.

### AC-14 — TTL inheritance
`ttl_days=0` is sent/treated as policy inheritance and not `never expire`.

### AC-15 — Explicit finite TTL
A supported positive finite TTL reaches AstraVector session Start unchanged in days.

### AC-16 — No tokenizer leakage
No BGE-M3 tokenizer/model dependency is introduced into AstraIndexator delivery adapter.

### AC-17 — Capacity handling
Transient `RESOURCE_EXHAUSTED`/`UNAVAILABLE` follows bounded retry/backpressure; permanent size/validation failures do not blind-retry.

### AC-18 — Session state forward compatibility
Unknown session status strings do not crash the client and are not interpreted as success.

### AC-19 — Status reconciliation
Restarted/reclaimed AstraIndexator worker can resume from persisted session/batch checkpoints.

### AC-20 — Hash parity gate
Production session hashing cannot be declared contract-complete until AstraVector publishes canonical hashing golden fixtures and Python/Rust parity passes.

### AC-21 — Metadata safety
Secrets or raw binary image data are not propagated through LogicalBlock metadata.

### AC-22 — Correlation
Downstream operations are traceable by job/document/session/correlation identifiers.

### AC-23 — Contract versioning
CI detects incompatible proto/schema changes against the pinned AstraVector contract.

### AC-24 — Retrieval proof
A real file fixture completes parse/OCR → LogicalBlock ingestion → searchable vector state → retrieval with citation back to original source location.

---

## 38. Required verification evidence

Implementation based on TZ-11 SHALL produce:

- generated protobuf client build proof;
- adapter unit tests for every supported block mapping;
- LogicalBlock structural validator tests;
- document version mapping tests;
- access-zone by code and ID tests;
- TTL inheritance and finite TTL tests;
- single-call integration test;
- session Start/Append/Finalize integration test;
- deterministic batching test;
- Start lost-ACK replay test;
- Append lost-ACK replay test;
- same-index/different-hash conflict test;
- Finalize timeout reconciliation test;
- session expiry handling test;
- AstraVector unavailable/recovery test;
- capacity/backpressure test;
- large-document streaming test;
- worker restart/reclaim continuation test;
- vector readiness polling test;
- end-to-end retrieval/citation test;
- canonical hashing golden-vector parity test once published by AstraVector.

---

## 39. Open contract gaps blocking full production closure

The following are explicitly tracked rather than guessed:

### GAP-01 — Session hash canonicalization — P0
AstraVector must publish byte-exact `batch_content_hash` and `final_content_hash` rules plus golden vectors.

### GAP-02 — Producer document version mapping — P0
AstraIndexator must finalize whether producer `documentVersion` is numeric-only or persist an opaque→uint64 mapping.

### GAP-03 — Session activation semantics — P1
AstraIndexator relies on `GetDocumentVectorStatus`; facade semantics should remain authoritative and be stabilized/documented by AstraVector.

### GAP-04 — Absolute TTL — P1
Do not promise exact `TTL_MODE_ABSOLUTE` interoperability until AstraVector contract support is stabilized.

### GAP-05 — Structured error reasons — P1
Typed server error detail would improve deterministic retry classification; until then preserve raw gRPC status/message and known contract semantics.

---

## 40. Architectural invariants established by TZ-11

1. AstraIndexator integrates with the existing public `AstraVectorIngestionFacade`.
2. Generated protobuf classes are wire DTOs only; domain model remains independent.
3. AstraIndexator sends logical document structure, not embedding chunks.
4. AstraVector owns tokenizer-aware chunking, embeddings, projection and activation/reconciliation.
5. One indexed document version belongs to one effective access zone.
6. Large documents use session ingestion with deterministic bounded batches.
7. Mutating RPC timeouts are ambiguous outcomes requiring idempotent replay/reconciliation.
8. Start retries reuse one stable idempotency key.
9. Append retries reuse the same batch index and canonical hash.
10. Finalize timeout triggers status reconciliation before any new operation.
11. `is_last_batch` does not replace explicit Finalize.
12. Session completion and document searchability are distinct.
13. `GetDocumentVectorStatus` is the readiness authority for AstraIndexator.
14. AstraIndexator does not duplicate access-zone TTL policy.
15. Session `ttl_days=0` means policy inheritance.
16. Delivery state is persisted so worker recovery does not restart blindly.
17. Large-document delivery is streaming/bounded-memory.
18. Session hash canonicalization remains a P0 contract gap until golden fixtures exist.

---

## 41. Next specification dependency

The next critical specification is **TZ-13 — Reliability & Recovery**, with TZ-12 lifecycle semantics developed in parallel/just before destructive operations.

TZ-13 must combine:

```text
TZ-02 lease/fencing
+
TZ-09 prepared artifacts
+
TZ-11 downstream session checkpoints
+
AstraVector idempotency/status reconciliation
```

and define deterministic recovery for crashes at every pipeline and downstream delivery stage.
