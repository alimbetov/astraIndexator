# TZ-14 — Observability & Knowledge Inventory

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-14
- **Title:** Observability & Knowledge Inventory
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-02, TZ-03, TZ-04, TZ-05, TZ-06, TZ-07, TZ-08, TZ-09, TZ-10, TZ-11, TZ-12, TZ-13, TZ-15, TZ-16, TZ-17, TZ-18
- **Authoritative downstream runtime:** AstraVector `alimbetov/llm2/main`

---

## 2. Purpose

TZ-14 defines how AstraIndexator exposes enough operational evidence to answer, without opening raw databases or Qdrant directly:

1. **What was accepted for indexing?**
2. **What is processing now?**
3. **What was successfully indexed?**
4. **Which document version is searchable?**
5. **How many source/logical/vector units were created?**
6. **Which access zone and processing/model versions were used?**
7. **What failed, where, and whether recovery is progressing?**
8. **How long will the indexed knowledge remain valid/searchable?**
9. **Has the knowledge already expired/deleted?**
10. **Can an operator correlate one Spring Boot request through PostgreSQL, SeaweedFS, AstraIndexator and AstraVector?**

Observability is therefore broader than logs and metrics. AstraIndexator 1.0 SHALL provide a queryable **Knowledge Inventory / Lifecycle Visibility** read model in addition to standard logs, metrics, traces and health endpoints.

Canonical visibility flow:

```text
Spring Boot request
      ↓ correlationId
PostgreSQL indexation_job
      ↓ jobId / attemptId / leaseGeneration
AstraIndexator pipeline
      ↓ processing evidence
prepared artifact
      ↓ manifest / blocks
AstraVector ingestion
      ↓ sessionId / document ref
AstraVector status
      ↓ searchable / operation state / sync evidence
Knowledge Inventory
      ↓
operator / support / dashboard / audit
```

---

## 3. Core invariants

### OBS-01 — One correlation chain

Every indexing operation SHALL be traceable using stable correlation fields from producer acceptance through downstream searchability.

### OBS-02 — Business identity and telemetry identity are distinct

`documentId`, `documentVersion`, `jobId`, `processingAttemptId`, `traceId`, `spanId`, `workerId`, `ingestionSessionId` and AstraVector operation/document identifiers MUST NOT be conflated.

### OBS-03 — Searchability is observable

A local `COMPLETED` job without evidence of downstream `searchable=true` is an inconsistency that MUST be visible.

### OBS-04 — Effective TTL is authoritative downstream state

AstraIndexator SHALL NOT claim an exact remaining knowledge lifetime by locally recalculating access-zone code policy.

### OBS-05 — Unknown lifetime is represented explicitly

If authoritative `effectiveExpiresAt`/never-expire state is unavailable, the result MUST be `UNKNOWN` or `INHERITED_UNRESOLVED`, never a guessed date.

### OBS-06 — Session expiry is not document expiry

`GetLogicalDocumentIngestionStatus.expires_at` describes ingestion-session lifetime and MUST NOT be shown as knowledge expiration.

### OBS-07 — No high-cardinality business IDs in Prometheus labels

`documentId`, `jobId`, hashes, source URI, session ID and error messages belong in logs/traces/read models, not unbounded metrics labels.

### OBS-08 — No document-content leakage

Normal telemetry SHALL NOT emit raw document text, OCR text, embedding text, signed URLs, credentials or unrestricted metadata.

### OBS-09 — Observability never becomes lifecycle authority

Dashboards/read models report state; they do not bypass TZ-02 coordinator, AstraVector lifecycle or TZ-13 reconciliation.

### OBS-10 — Time is UTC internally

All persisted/telemetry timestamps SHALL be UTC/offset-aware. UI/local presentation may convert timezone separately.

---

## 4. Existing AstraVector observability contract

The current AstraVector public facade already exposes useful status evidence.

`GetDocumentVectorStatus` returns conceptually:

```text
state
progress_percent
searchable
message
ready_to_activate
sync
```

The nested sync result exposes evidence such as:

```text
document_status
expected_bindings
synced_bindings
pending_bindings
failed_bindings

dense_vectors_expected
dense_vectors_found
sparse_vectors_expected
sparse_vectors_found

outbox_pending
outbox_retry_pending
outbox_completed
outbox_failed

qdrant_collection_exists
qdrant_points_expected
qdrant_points_found
qdrant_points_missing
qdrant_points_extra

ready_to_activate
last_sync_attempt_at
last_sync_error_code
last_sync_error_message
```

AstraIndexator SHALL consume these through the public facade defined in TZ-11 and SHALL NOT query Qdrant directly to determine success.

---

## 5. Important current contract gap — effective knowledge expiry

The current `GetDocumentVectorStatusResponse` does **not** expose the document's authoritative effective expiration timestamp.

The session-status contract does expose:

```text
GetLogicalDocumentIngestionStatus.expires_at
```

but that field is the expiration of the temporary ingestion session, not indexed knowledge TTL.

Therefore exact statements such as:

```text
"this knowledge has 73 days 4 hours remaining"
```

MUST NOT be generated from session expiry or from a locally duplicated `accessZoneCode -> default TTL` matrix.

### 5.1 Required downstream enhancement

Before AstraIndexator can guarantee exact remaining lifetime for inherited TTL, AstraVector SHOULD expose through a stable public/admin document-status contract at least:

```text
effective_ttl_mode
effective_expires_at
never_expires
expiry_source
lifecycle_status
expired_at
last_lifecycle_update_at
```

Preferred conceptual extension:

```proto
message EffectiveTtlStatus {
  string mode = 1;
  string effective_expires_at = 2;
  bool never_expires = 3;
  string source = 4;
}
```

attached to `DocumentVectorStatus` or a dedicated public lifecycle-status response.

This is a **P0 observability gap for exact remaining-lifetime display**, not a blocker for indexing itself.

### 5.2 No direct AstraVector DB coupling

AstraIndexator SHALL NOT solve this gap by directly reading AstraVector PostgreSQL tables. That would bypass the service boundary and couple Indexator to internal schema.

---

## 6. Correlation identity model

Every structured log/trace where applicable SHOULD carry the following stable context:

```text
correlationId
traceId
spanId

jobId
documentId
producerDocumentVersion
astraVectorDocumentVersion
processingAttemptId
leaseGeneration
workerId
processingStage

accessZoneId
accessZoneCode

ingestionMode
ingestionSessionId
batchIndex

sourceContentHash
processingFingerprint
preparedManifestRef
```

Not every field exists at every stage. Missing fields remain absent rather than fabricated.

### 6.1 Correlation propagation

Producer-supplied correlation ID SHOULD be retained if valid; otherwise AstraIndexator generates one at acceptance.

The same correlation context SHALL be propagated to AstraVector request context where the downstream contract supports it.

---

## 7. Structured logging

Production logs SHALL be structured JSON or an equivalent machine-queryable format.

Required baseline fields:

```text
timestamp
level
service
serviceVersion
environment
instanceId / podName
logger / component
eventCode
message
correlationId
traceId
jobId
documentId
documentVersion
attemptId
leaseGeneration
processingStage
errorClass
errorCode
retryable
durationMs
```

### 7.1 Event codes

Operationally significant events SHALL have stable codes rather than relying only on human prose.

Examples:

```text
JOB_ACCEPTED
JOB_CLAIMED
LEASE_RENEWED
LEASE_LOST
JOB_RECLAIMED

SOURCE_ACQUISITION_STARTED
SOURCE_VALIDATED
SOURCE_CONTENT_MISMATCH

PARSER_STARTED
PARSER_COMPLETED
OCR_DECISION
OCR_STARTED
OCR_COMPLETED
NORMALIZATION_COMPLETED
SPLITTING_COMPLETED
PREPARED_ARTIFACT_PUBLISHED

ASTRAVECTOR_SESSION_STARTED
ASTRAVECTOR_BATCH_ACCEPTED
ASTRAVECTOR_FINALIZE_REQUESTED
ASTRAVECTOR_STATUS_RECONCILED
DOCUMENT_SEARCHABLE

RETRY_SCHEDULED
DEAD_LETTERED
CANCEL_REQUESTED
DOCUMENT_DELETE_SCHEDULED
DOCUMENT_EXPIRED
```

Event code evolution SHALL be version-controlled.

---

## 8. Sensitive-data logging policy

Normal application logs MUST NOT contain:

```text
raw source text
normalized text
OCR full text
embedding text
full table contents
credentials
tokens
passwords
Nexus credentials
PostgreSQL passwords
SeaweedFS secrets
privileged signed URLs
protobuf payload dumps containing document content
```

Permitted evidence includes bounded metadata such as:

```text
content hash
size
page count
element/fragment/block counts
file type
parser/OCR/model version
access-zone code/id when authorized for operational logs
source object key only according to TZ-16 redaction policy
```

Error logging SHALL prefer structured error code + bounded diagnostic over full exception payloads that may embed document text.

---

## 9. Metrics design rules

Metrics SHALL be low-cardinality and aggregation-friendly.

Allowed typical labels:

```text
status
stage
format
error_class
error_code_family
ocr_mode
ocr_device
parser_profile
normalizer_profile
splitter_profile
ingestion_mode
operation_state
access_zone_code  // only if deployment cardinality is bounded and policy permits
```

Disallowed/unbounded labels:

```text
job_id
document_id
fragment_id
source_hash
source_uri
session_id
error_message
file_name
```

Document-specific investigation belongs to logs/traces/Knowledge Inventory.

---

## 10. Coordinator metrics

Required baseline metrics SHOULD include:

```text
astra_indexator_jobs_total{status}
astra_indexator_jobs_in_state{status}
astra_indexator_jobs_in_stage{stage}
astra_indexator_job_age_seconds{status}
astra_indexator_job_processing_duration_seconds

astra_indexator_claim_total{result}
astra_indexator_claim_duration_seconds
astra_indexator_lease_renew_total{result}
astra_indexator_lease_lost_total
astra_indexator_reclaim_total

astra_indexator_retry_total{stage,error_class}
astra_indexator_retry_wait_seconds
astra_indexator_dead_letter_total{stage,error_class}

astra_indexator_active_workers
astra_indexator_active_jobs
```

Backlog age is as important as queue length.

---

## 11. Acquisition/storage metrics

Baseline:

```text
source_acquisition_total{result,format}
source_acquisition_bytes_total{format}
source_acquisition_duration_seconds{format}
source_validation_failed_total{reason}

prepared_artifact_publish_total{result}
prepared_artifact_bytes_total
prepared_artifact_parts_total{kind}
prepared_artifact_reuse_total{result}
orphan_artifact_candidates
```

---

## 12. Parser metrics

Baseline:

```text
parser_documents_total{format,result}
parser_duration_seconds{format}
parser_pages_total{page_class}
parser_elements_total{element_type}
parser_low_signal_pages_total
parser_reading_order_warning_total{type}
parser_table_total
parser_image_total
```

No actual text is stored in metrics.

---

## 13. OCR metrics

Baseline:

```text
ocr_candidates_total{decision}
ocr_pages_total{result,device}
ocr_duration_seconds{device}
ocr_page_duration_seconds{device}
ocr_pixels_total{device}
ocr_low_confidence_blocks_total
ocr_native_duplicate_suppressed_total
ocr_memory_high_water_bytes
ocr_timeout_total
ocr_model_error_total{reason}
```

Model ID/version MAY be attached only if the number of active versions is bounded; otherwise expose model version through build/runtime info metric and logs.

---

## 14. Normalization/splitter metrics

Baseline:

```text
normalization_elements_total{result}
normalization_duration_seconds
normalization_operations_total{operation}
normalization_warning_total{reason}

logical_fragments_total{type}
logical_fragment_chars
logical_fragment_words
logical_fragment_forced_split_total{reason}
logical_fragment_small_merge_total
logical_splitter_duration_seconds
```

Histograms are preferred for fragment size distributions.

---

## 15. AstraVector delivery metrics

Baseline:

```text
astravector_ingestion_total{mode,result}
astravector_rpc_duration_seconds{method,result}
astravector_rpc_error_total{method,grpc_code}

astravector_session_active
astravector_session_expired_total
astravector_batches_total{result}
astravector_batch_blocks
astravector_batch_bytes
astravector_finalize_total{result}

astravector_document_state_total{state}
astravector_document_progress_percent
astravector_document_searchable_total{searchable}

astravector_sync_pending_bindings
astravector_sync_failed_bindings
astravector_outbox_pending
astravector_outbox_failed
astravector_qdrant_missing_points
```

Per-document values SHOULD NOT become labels. Aggregated gauges/histograms are used for metrics; per-document detail belongs to Knowledge Inventory.

---

## 16. Knowledge Inventory purpose

The Knowledge Inventory is a queryable operational read model answering:

```text
What knowledge is present?
Which version is present?
Was it actually made searchable?
When was it indexed?
What source produced it?
Which processing/model versions produced it?
Which access zone owns it?
What is its lifecycle/TTL state?
When will it expire, if known?
How long remains at query time?
Was it deleted/expired/replaced?
```

It is not a second source of truth for vector state. It is a denormalized operational projection built from AstraIndexator durable state plus reconciled AstraVector public status.

---

## 17. Canonical KnowledgeInventoryRecord

Conceptual DTO:

```json
{
  "documentId": "DOC-100",
  "producerDocumentVersion": "3",
  "astraVectorDocumentVersion": 3,

  "jobId": "...",
  "jobStatus": "COMPLETED",
  "processingStage": "FINALIZING",

  "source": {
    "fileName": "contract.pdf",
    "detectedFormat": "PDF",
    "sizeBytes": 1827341,
    "contentHash": "sha256:..."
  },

  "processing": {
    "parserVersion": "...",
    "ocrModelId": "...",
    "ocrModelVersion": "...",
    "normalizerVersion": "...",
    "splitterVersion": "...",
    "processingFingerprint": "..."
  },

  "counts": {
    "pages": 35,
    "elements": 612,
    "logicalFragments": 94,
    "logicalBlocks": 126,
    "astravectorExpectedBindings": 284,
    "astravectorSyncedBindings": 284,
    "qdrantPointsExpected": 284,
    "qdrantPointsFound": 284
  },

  "access": {
    "accessZoneId": "...",
    "accessZoneCode": "1500"
  },

  "vectorState": {
    "operationState": "ACTIVE",
    "progressPercent": 100.0,
    "searchable": true,
    "readyToActivate": true,
    "lastSyncAttemptAt": "...",
    "lastSyncErrorCode": null
  },

  "ttl": {
    "requestedMode": "INHERIT_ZONE_POLICY",
    "requestedTtlDays": null,
    "effectiveState": "FINITE",
    "effectiveExpiresAt": "2027-08-22T08:00:00Z",
    "remainingLifetimeSeconds": 31536000,
    "expirySource": "ASTRAVECTOR_EFFECTIVE",
    "lastVerifiedAt": "..."
  },

  "timestamps": {
    "acceptedAt": "...",
    "processingStartedAt": "...",
    "searchableAt": "...",
    "completedAt": "...",
    "lastReconciledAt": "..."
  }
}
```

Fields unavailable from authoritative evidence remain null/unknown.

---

## 18. Knowledge lifecycle states

For operator visibility, define a normalized read-only lifecycle state:

```text
NOT_INDEXED
PROCESSING
PARTIALLY_INDEXED
SEARCHABLE
EXPIRING
EXPIRED
DELETE_SCHEDULED
DELETING
DELETED
FAILED
UNKNOWN
```

This read model SHALL be derived from local job state plus current AstraVector operation/searchability/lifecycle evidence.

It is not an independent state machine allowed to mutate either system.

---

## 19. TTL visibility model

The Inventory SHALL distinguish:

```text
requested TTL intent
!= effective TTL
!= ingestion session expiry
!= prepared-artifact retention
!= source-object retention
```

Canonical TTL read state:

```text
UNKNOWN
INHERITED_UNRESOLVED
FINITE
NEVER_EXPIRES
EXPIRED
```

### 19.1 FINITE

Allowed only when authoritative effective expiration is known:

```text
effectiveExpiresAt != null
```

Then remaining lifetime is calculated at read time:

```text
remainingLifetimeSeconds = max(0, effectiveExpiresAt - nowUtc)
```

This calculated field SHOULD NOT be persistently decremented in the database.

### 19.2 NEVER_EXPIRES

Allowed only when AstraVector authoritative lifecycle/TTL response confirms non-expiring policy.

An access-zone code in `0000..0999` alone is insufficient evidence to display `NEVER_EXPIRES`, because TZ-10 states the registry/runtime policy is authoritative.

### 19.3 INHERITED_UNRESOLVED

Use when the producer sent inherit/default TTL but AstraIndexator cannot currently obtain authoritative effective expiry.

Example UI/API presentation:

```text
TTL: inherited from access zone
Exact expiration: not exposed by current AstraVector facade
```

### 19.4 EXPIRED

Use when AstraVector lifecycle/searchability evidence proves expiration, or when an authoritative effective expiration timestamp is in the past and lifecycle reconciliation confirms the expired state.

---

## 20. Expiry warning buckets

For dashboards/alerts, finite records MAY be grouped into bounded buckets computed from authoritative expiry:

```text
expired
< 24h
1-7d
8-30d
31-90d
91-365d
> 365d
never
unknown
```

Metrics SHOULD expose aggregate counts by bucket rather than document IDs.

Example:

```text
astra_indexator_knowledge_ttl_bucket{bucket="1-7d"} 42
```

Exact per-document remaining time belongs to Inventory queries.

---

## 21. Knowledge Inventory persistence

The operational projection MAY be implemented as PostgreSQL tables/materialized read model owned by AstraIndexator.

Recommended logical records:

```text
knowledge_inventory
knowledge_inventory_sync
knowledge_audit_event
```

The projection SHOULD retain:

```text
document identity/version
job identity
source hash/format/size
processing fingerprint/version summary
access-zone selector
downstream operation/searchability state
counts
requested TTL intent
authoritative effective expiry when available
last reconciliation timestamp
last error summary
```

It MUST NOT persist raw document text merely for observability.

---

## 22. Inventory reconciliation

Inventory is refreshed:

1. after local milestone changes;
2. after AstraVector mutation responses;
3. after `GetLogicalDocumentIngestionStatus` reconciliation;
4. after `GetDocumentVectorStatus` reconciliation;
5. periodically for searchable/expiring/deleting records according to configurable policy;
6. on operator-requested refresh when authorized.

A stale Inventory record MUST expose:

```text
lastVerifiedAt
freshnessStatus
```

Suggested read-only freshness classification:

```text
FRESH
STALE
UNVERIFIED
DOWNSTREAM_UNAVAILABLE
```

Never hide staleness by returning old state as if current.

---

## 23. Operational read API

AstraIndexator SHOULD expose an internal/operator read-only API. The transport may be REST initially, protected by TZ-16.

Recommended endpoints:

```text
GET /internal/v1/knowledge
GET /internal/v1/knowledge/{documentId}
GET /internal/v1/knowledge/{documentId}/versions/{producerVersion}
GET /internal/v1/jobs/{jobId}
GET /internal/v1/jobs/{jobId}/attempts
GET /internal/v1/jobs/{jobId}/events
```

Optional controlled action:

```text
POST /internal/v1/knowledge/{documentId}/versions/{version}/refresh-status
```

The refresh endpoint performs reconciliation through supported service APIs. It does not mutate indexing lifecycle unless a separate authorized recovery operation is requested.

### 23.1 List filters

Knowledge list SHOULD support bounded filters:

```text
documentId
sourceFileName
jobStatus
knowledgeLifecycleState
searchable
accessZoneCode
acceptedFrom / acceptedTo
searchableFrom / searchableTo
expiresBefore / expiresAfter
ttlState
processingFingerprint
page/fragment count range
```

Pagination is mandatory.

---

## 24. Recommended list projection

A practical operator table should show at minimum:

| Field | Meaning |
|---|---|
| Document ID | Stable business identity |
| Version | Producer-visible version |
| File | Source file/display name |
| Size | Source bytes |
| Zone | Effective access-zone code |
| Job status | Local coordinator state |
| Vector state | AstraVector operation state |
| Searchable | Actual downstream searchability |
| Pages | Parsed pages/units |
| Fragments | Logical fragments |
| Synced bindings | AstraVector sync progress |
| Indexed at | Searchable/completion timestamp |
| TTL state | finite/never/inherited/unknown/expired |
| Expires at | Authoritative timestamp when available |
| Remaining | Calculated from authoritative expiry |
| Last verified | Freshness evidence |

This directly provides the requested operational answer: **what is loaded and how long it remains knowledge in the system**.

---

## 25. Tracing model

OpenTelemetry-compatible distributed tracing SHOULD be used.

Recommended span hierarchy:

```text
indexation.job
  |- coordinator.claim
  |- source.head
  |- source.download
  |- source.validate
  |- parser.parse
  |- ocr.decide
  |- ocr.page
  |- normalize.document
  |- splitter.document
  |- prepared.publish
  |- astravector.start
  |- astravector.append
  |- astravector.finalize
  |- astravector.status
  `- inventory.update
```

Do not create one trace span for every tiny paragraph/chunk in very large documents by default. Use aggregation/events or sampled child spans to avoid telemetry explosions.

---

## 26. Trace sampling

Recommended policy:

```text
errors / dead-letter / ownership loss -> retain at high probability
slow jobs                           -> retain at elevated probability
ordinary successful jobs            -> configurable sampling
health checks                        -> heavily reduced/disabled
```

Sampling policy SHALL NOT remove durable audit evidence required for lifecycle/security investigations.

---

## 27. Health endpoints

AstraIndexator SHALL distinguish liveness from readiness.

### 27.1 Liveness

Answers whether the process/event loop is alive.

It SHOULD NOT fail merely because AstraVector/SeaweedFS/Nexus is temporarily unavailable.

Conceptual:

```text
GET /livez
```

### 27.2 Readiness

Answers whether the instance may safely claim/execute jobs for its configured capabilities.

Readiness SHALL account for mandatory dependencies such as:

```text
PostgreSQL
required SeaweedFS connectivity
required local parser runtime
required local OCR model bundle for OCR-capable worker profile
AstraVector connectivity/contract compatibility according to startup policy
workspace writability/capacity
```

Nexus runtime connectivity is not necessarily required when models are already locally verified because TZ-06/TZ-15 forbid document-time downloads.

Conceptual:

```text
GET /readyz
```

### 27.3 Detailed internal health

Authenticated operator endpoint MAY expose component states without secrets:

```text
GET /internal/v1/health
```

---

## 28. Dependency health semantics

Component states SHOULD use a stable vocabulary:

```text
UP
DEGRADED
DOWN
NOT_REQUIRED
UNKNOWN
```

A dependency outage and a process bug must be distinguishable.

Readiness policy depends on worker profile. For example, an OCR-capable worker with missing mandatory OCR models is not ready for OCR work, whereas a parser-only worker may remain valid if capability routing exists.

---

## 29. Audit event model

Operational logs are not sufficient for business/security lifecycle audit.

Important durable audit events SHOULD include:

```text
JOB_CREATED
JOB_REQUEUED
JOB_CANCEL_REQUESTED
JOB_CANCELLED
DEAD_LETTER_ENTERED

DOCUMENT_INDEXING_STARTED
DOCUMENT_SEARCHABLE
DOCUMENT_REINDEX_REQUESTED
DOCUMENT_DELETE_REQUESTED
DOCUMENT_DELETE_CONFIRMED
DOCUMENT_EXPIRED

ACCESS_ZONE_RESOLVED
TTL_INTENT_ACCEPTED
TTL_EFFECTIVE_OBSERVED

MODEL_VERSION_USED
PROCESSING_FINGERPRINT_CHANGED
MANUAL_STATUS_REFRESH
MANUAL_RECOVERY_ACTION
```

Audit event fields:

```text
eventId
eventType
timestamp
actorType
actorId/callerService when allowed
correlationId
jobId
documentId
version
accessZoneId/code
oldState
newState
reason
result
bounded metadata
```

Audit records MUST NOT contain document body text.

---

## 30. Alerting baseline

Required alert classes SHOULD include:

```text
queue backlog age above SLO
no workers actively claiming
lease renewal failure rate high
reclaim storm
retry storm
dead-letter growth

source/storage dependency unavailable
prepared artifact publication failures

parser failure spike
OCR timeout/memory/model failure spike

AstraVector RPC error spike
sessions near/at expiry
Finalize stuck
searchability lag above SLO
outbox failed/pending growth
Qdrant missing-point evidence

knowledge inventory reconciliation stale
COMPLETED jobs with searchable != true
SEARCHABLE records with unexpected FAILED/DELETED downstream state
finite knowledge expiring within configured warning window
```

Alert thresholds belong to TZ-18/configuration, not hard-coded source constants.

---

## 31. SLI/SLO candidates

TZ-14 defines measurements; final numeric objectives belong to deployment/SRE policy.

Candidate SLIs:

```text
job acceptance availability
claim latency
queue wait latency
end-to-end indexing latency
parser success rate
OCR success rate
prepared publication success rate
AstraVector ingestion success rate
time from Finalize to searchable
recovery success rate
dead-letter rate
inventory freshness
TTL-state resolution coverage
```

Particularly useful:

```text
searchability_lag = searchableAt - acceptedAt
```

and:

```text
inventory_reconciliation_age = now - lastVerifiedAt
```

---

## 32. Cardinality controls

To prevent observability itself from degrading production:

- do not emit per-fragment Prometheus labels;
- do not emit per-page labels with page numbers;
- cap warning/error-code vocabularies;
- cap metadata values;
- aggregate high-volume events;
- sample fine-grained traces;
- use logs/inventory for document-specific lookup;
- enforce telemetry payload limits.

---

## 33. Observability retention

Retention of observability data is independent from document/vector TTL.

```text
document expires
!= delete job audit immediately
!= delete metrics immediately
!= retain sensitive source metadata forever
```

TZ-16/TZ-18 SHALL define retention periods for:

```text
application logs
traces
metrics
audit events
knowledge inventory tombstones
error diagnostics
```

After knowledge deletion/expiry, Inventory MAY retain a bounded tombstone such as:

```text
documentId/version
former access zone
expired/deleted timestamp
content hash
processing fingerprint
audit references
```

without retaining document body text.

---

## 34. Knowledge Inventory security

The Inventory reveals sensitive metadata and therefore is not a public endpoint.

Requirements:

1. operator API authentication/authorization is mandatory;
2. query scope must respect access-zone permissions;
3. a user MUST NOT enumerate document identities across unauthorized zones;
4. exact source URI/object key may require redaction;
5. hashes are operational identifiers and may be restricted;
6. debug/downstream errors may contain infrastructure details and require elevated role;
7. document text is excluded from baseline inventory;
8. audit records all manual refresh/recovery actions.

Detailed RBAC/auth belongs to TZ-16.

---

## 35. Failure behavior

Observability subsystem failures SHALL NOT corrupt indexing state.

Examples:

```text
metrics backend unavailable
→ indexing continues

trace exporter unavailable
→ bounded local buffering/drop according to policy

inventory projection update fails after job milestone
→ canonical job/downstream state remains valid
→ projection repair/reconciliation retries
```

However failure to persist mandatory security/business audit evidence MAY be configured fail-closed for privileged manual lifecycle actions.

---

## 36. Reconciliation anomalies

The Inventory reconciliation process SHALL detect at least:

```text
local COMPLETED + downstream searchable=false
local PROCESSING + downstream ACTIVE/searchable=true after lost ACK
local CANCELLED + downstream still ACTIVE
local DELETED + downstream searchable=true
local SEARCHABLE + downstream DELETED/EXPIRED
prepared manifest missing for claimed reusable checkpoint
session expired while local delivery marked active
binding/vector/Qdrant counts inconsistent
TTL finite but effective expiry missing
Inventory stale beyond freshness SLO
```

Anomalies produce structured codes and feed TZ-13 recovery/operator workflows.

---

## 37. Dashboard baseline

A production dashboard SHOULD include:

### Queue / workers

```text
pending jobs
oldest pending age
processing jobs
retry wait
dead-letter
active workers
lease lost/reclaimed
```

### Pipeline

```text
acquisition/parser/OCR/normalization/splitter latency
throughput by source format
OCR share
fragment-size distributions
failures by stage
```

### AstraVector

```text
sessions active
batches/sec
Finalize failures
operation-state distribution
searchability lag
sync/outbox/Qdrant anomaly gauges
```

### Knowledge inventory

```text
searchable document versions
processing versions
failed versions
expired/deleted versions
knowledge by access zone
finite / never / inherited-unresolved / unknown TTL
expiring <24h / <7d / <30d
inventory stale records
```

---

## 38. Verification requirements

TZ-17 SHALL prove at least:

1. one producer request can be correlated end-to-end by correlation/job/document IDs;
2. stale worker and reclaimed worker events are distinguishable by `leaseGeneration`;
3. source/parser/OCR/splitter timing is measurable;
4. no raw document text appears in normal logs;
5. Prometheus cardinality does not grow linearly with document IDs;
6. AstraVector `searchable` state appears in Inventory;
7. sync/binding/Qdrant counts appear when requested/available;
8. session `expires_at` is never displayed as document TTL;
9. inherited TTL without authoritative expiry displays `INHERITED_UNRESOLVED`;
10. finite authoritative expiry produces correct `remainingLifetimeSeconds` at read time;
11. expired lifetime clamps remaining seconds to zero;
12. never-expire is displayed only from authoritative downstream evidence;
13. stale Inventory state exposes freshness status;
14. downstream outage does not silently turn stale status into current status;
15. local `COMPLETED` + downstream non-searchable anomaly is detected;
16. job/delete/reindex manual operations generate audit events;
17. metrics/tracing exporter outage does not corrupt coordinator state;
18. knowledge list pagination/filtering works at production-like cardinality;
19. access-zone authorization prevents cross-zone inventory enumeration;
20. expiry warning buckets match authoritative effective expiry fixtures.

---

## 39. Acceptance criteria

TZ-14 is satisfied when:

- **AC-01:** logs are structured and carry stable correlation context;
- **AC-02:** metrics cover queue, stage, dependency, processing and AstraVector delivery health;
- **AC-03:** Prometheus labels remain bounded-cardinality;
- **AC-04:** traces correlate critical pipeline stages without per-fragment explosion;
- **AC-05:** liveness and readiness semantics are distinct;
- **AC-06:** operator health exposes dependency state without secrets;
- **AC-07:** a queryable Knowledge Inventory shows indexed document/version/source/process evidence;
- **AC-08:** Inventory shows AstraVector operation state, progress and `searchable`;
- **AC-09:** Inventory shows relevant block/binding/vector synchronization counts when available;
- **AC-10:** requested TTL, effective TTL and ingestion-session expiry are distinct fields/concepts;
- **AC-11:** exact remaining lifetime is calculated only from authoritative `effectiveExpiresAt`;
- **AC-12:** unresolved inherited lifetime is explicitly `INHERITED_UNRESOLVED/UNKNOWN`;
- **AC-13:** non-expiring knowledge is displayed only from authoritative evidence;
- **AC-14:** Inventory exposes `lastVerifiedAt`/freshness;
- **AC-15:** reconciliation detects local/downstream lifecycle contradictions;
- **AC-16:** audit records lifecycle/manual actions without document body content;
- **AC-17:** normal telemetry does not leak raw document/OCR/embedding text or secrets;
- **AC-18:** dashboards expose backlog/searchability/recovery/expiry risks;
- **AC-19:** observability backend failure cannot overwrite canonical job/vector state;
- **AC-20:** TZ-17 contains executable telemetry, inventory, TTL and anomaly-verification evidence.

---

## 40. Implementation decomposition

Recommended modules:

```text
ObservabilityContext
StructuredEventLogger
MetricsRegistry
TracingService
HealthService
DependencyHealthProbe
AuditEventRepository
KnowledgeInventoryRepository
KnowledgeInventoryProjector
KnowledgeStatusReconciler
TtlVisibilityMapper
InventoryFreshnessPolicy
```

AstraVector-specific status access remains behind the TZ-11 adapter.

---

## 41. P0/P1 contract decisions carried forward

### P0 — effective expiry visibility

AstraVector needs a stable service-level way to expose authoritative document effective expiry/never-expire state before AstraIndexator can promise exact remaining knowledge lifetime for inherited TTL.

Until then:

```text
requested TTL: visible
zone: visible
searchability: visible
exact effective expiration: UNKNOWN/INHERITED_UNRESOLVED
```

No local policy duplication is allowed.

### P0 — version mapping visibility

The producer version and numeric AstraVector version mapping from TZ-11 MUST both be visible in Inventory so support can correlate lifecycle operations correctly.

### P1 — historical version inventory

Inventory SHOULD retain bounded lifecycle history/tombstones for replaced, expired and deleted document versions, subject to TZ-16/TZ-18 retention policy.

---

## 42. Final invariant

AstraIndexator operations SHALL be able to answer:

```text
WHAT is loaded?
  -> document/version/source/process identity

WHERE is it scoped?
  -> access zone

IS it usable?
  -> AstraVector searchable + sync evidence

HOW MUCH was produced?
  -> pages/elements/fragments/blocks/bindings/vectors

WHEN was it loaded?
  -> accepted/searchable/completed timestamps

HOW LONG does it remain?
  -> authoritative effective expiry - now
     OR explicit NEVER_EXPIRES
     OR honest UNKNOWN/INHERITED_UNRESOLVED

WHY is it not available?
  -> job stage/error + downstream operation/sync status
```

No dashboard convenience may replace authoritative AstraVector lifecycle evidence with a locally guessed TTL.
