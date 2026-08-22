# TZ-12 — Document Lifecycle

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-12
- **Title:** Document Lifecycle
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-02, TZ-03, TZ-09, TZ-10, TZ-11, TZ-13, TZ-14, TZ-16, TZ-17
- **Authoritative downstream contract:** AstraVector `AstraVectorIngestionFacade`, `AstraVectorRetrievalFacade`, current v007 facade semantics

---

## 2. Purpose

This specification defines the lifecycle of one logical document and its versions as orchestrated by AstraIndexator.

The lifecycle covers:

- first indexing of a document;
- new source revision / new version;
- reindexing of an existing source revision;
- replacement semantics;
- partial/in-progress indexing;
- activation/searchability;
- cancellation;
- deletion;
- TTL expiration;
- recovery after ambiguous downstream state;
- retention of provenance and prepared artifacts.

AstraIndexator SHALL NOT pretend that one local job state is identical to AstraVector document/vector state.

The system therefore maintains two distinct but correlated lifecycles:

```text
AstraIndexator job lifecycle
        +
AstraVector document/vector lifecycle
```

---

## 3. Responsibility boundary

### 3.1 Spring Boot / platform owns

- stable logical `documentId`;
- producer-visible source revision intent;
- source upload;
- access-zone assignment;
- optional TTL request;
- create/update/delete business intent;
- optional external/business document identity.

### 3.2 AstraIndexator owns

- mapping business intent to an `IndexationJob`;
- source acquisition and canonical processing;
- generation of deterministic canonical blocks/fragments;
- AstraVector ingestion/session orchestration;
- retry and reconciliation;
- status projection for the producer;
- prepared-artifact lifecycle according to retention policy;
- safe handling of cancellation/reindex/delete races.

### 3.3 AstraVector owns

- canonical vector/document state;
- tokenizer-aware chunking;
- embeddings;
- PostgreSQL/Qdrant projection;
- outbox;
- activation/searchability state;
- TTL enforcement;
- expired-document search exclusion;
- vector deletion/reconciliation.

---

## 4. Identity model

The following identities remain distinct:

```text
documentId
!= producerDocumentVersion
!= astraVectorDocumentVersion
!= jobId
!= processingAttemptId
!= ingestionSessionId
!= fragmentId/blockId
!= AstraVector chunk IDs
```

### 4.1 `documentId`

Stable logical document identity across revisions.

Example:

```text
DOC-100
```

### 4.2 `producerDocumentVersion`

Version/revision as seen by the producer. It may be opaque in AstraIndexator.

Examples:

```text
1
rev-17
sha256:...
```

### 4.3 `astraVectorDocumentVersion`

Current AstraVector public facade uses a numeric document version (`uint32/uint64` depending on the specific message).

TZ-12 SHALL NOT silently coerce arbitrary producer strings into numeric versions.

Until a final mapping contract is frozen in TZ-11 implementation work, one of the following MUST be selected explicitly:

1. producer contract restricts document version to positive integer; or
2. AstraIndexator persists an authoritative mapping from opaque producer version to positive AstraVector numeric version.

This is a cross-service contract decision, not an implementation convenience.

---

## 5. Lifecycle dimensions

AstraIndexator SHALL track at least three dimensions independently.

### 5.1 Job state

Defined by TZ-02:

```text
PENDING
PROCESSING
RETRY_WAIT
COMPLETED
FAILED
DEAD_LETTER
CANCELLED
```

### 5.2 Processing stage

Examples:

```text
ACQUIRING
PARSING
OCR_PROCESSING
NORMALIZING
SPLITTING
PREPARING_ARTIFACTS
STARTING_INGESTION
UPLOADING_BLOCKS
FINALIZING_INGESTION
WAITING_VECTOR_READY
DELETING_VECTORS
FINALIZING
```

### 5.3 Downstream AstraVector state

Current facade may expose operation/document states such as:

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
DELETE_SCHEDULED
DELETING
DELETED
```

Session ingestion additionally exposes string states such as:

```text
ACTIVE
FINALIZING
COMPLETED
FAILED
ABORTED
EXPIRED
UNKNOWN
```

AstraIndexator MUST preserve the raw downstream state for diagnostics and MUST NOT collapse all accepted/intermediate states to `COMPLETED`.

---

## 6. Canonical business lifecycle

A logical document may have multiple immutable versions:

```text
DOC-100
  |- version 1
  |- version 2
  `- version 3
```

A new version SHALL NOT mutate historical version identity in place.

Canonical flow:

```text
SOURCE REVISION ACCEPTED
        ↓
PROCESSING
        ↓
CANONICAL ARTIFACT READY
        ↓
ASTRAVECTOR INGESTION
        ↓
VECTOR STATE RECONCILED
        ↓
SEARCHABLE / ACTIVE
        ↓
(optional later)
EXPIRED or DELETED
```

---

## 7. First indexing

For a document not previously indexed:

```text
Spring Boot
  -> source upload
  -> create PENDING job
  -> AstraIndexator processing
  -> AstraVector ingestion
  -> vector status reconciliation
  -> searchable=true
  -> job COMPLETED
```

AstraIndexator SHALL mark the job `COMPLETED` only at the agreed completion level.

Baseline completion definition for AstraIndexator 1.0:

```text
COMPLETED == downstream vector status proves document searchable
```

If a deployment later selects a weaker completion level, that must be explicit configuration and reflected in API/status semantics.

---

## 8. New source revision

When source content changes, the preferred lifecycle is:

```text
same documentId
+
new documentVersion
+
new jobId
```

Example:

```text
DOC-100 / version 1 -> historical/current
DOC-100 / version 2 -> new build
```

The old version SHALL remain untouched while the new version is still processing unless a separately approved policy says otherwise.

This prevents retrieval downtime caused by partially built replacement versions.

---

## 9. Build-before-switch principle

For replacement/update flows, the safe baseline is:

```text
old searchable version
        |
        | remains searchable
        v
new version BUILDING
        ↓
all blocks accepted
        ↓
finalization
        ↓
vector readiness/searchability confirmed
        ↓
new version becomes effective according to AstraVector lifecycle
        ↓
old version may be retained/expired/deleted according to policy
```

AstraIndexator MUST NOT remove the previous searchable version merely because processing of a new version has started.

---

## 10. Reindex semantics

A reindex means reprocessing already known logical content because processing/indexing behavior changed, for example:

- parser version change;
- OCR model/version change;
- normalization change;
- logical splitter change;
- AstraVector chunking/model/indexing profile change;
- recovery/rebuild after projection loss.

A reindex SHALL be explicit and auditable.

It MUST NOT silently overwrite historical processing provenance.

Two baseline strategies are allowed:

### 10.1 New document version

Preferred when downstream semantic representation materially changes and historical comparison/rollback matters.

```text
DOC-100 / v2
  -> reindex
DOC-100 / v3
```

### 10.2 Replace existing numeric version

Allowed only if the current AstraVector facade's `replace_existing_version` behavior is explicitly selected and verified by TZ-11/TZ-17 tests.

This strategy MUST NOT be used by default until exact runtime behavior and rollback guarantees are demonstrated.

---

## 11. Same-content duplicate submission

If the producer resubmits the exact same logical revision with the same content hash and same effective processing intent, AstraIndexator SHOULD treat the operation idempotently.

Possible result:

```text
existing successful version found
→ no duplicate vector document created
→ return/reuse known state
```

The exact producer idempotency key and uniqueness constraints are defined by TZ-01/TZ-02/TZ-11.

A different content hash under the same logical operation identity MUST be treated as a conflict, not as an idempotent retry.

---

## 12. Partial indexing

A document that has only partially completed downstream ingestion MUST NOT be represented to the producer as successfully indexed.

Examples of partial states:

```text
session ACTIVE
some batches staged
session FINALIZING
vectors partially published
outbox pending
Qdrant sync incomplete
```

Canonical rule:

```text
partial downstream progress != job COMPLETED
```

On worker restart/reclaim, AstraIndexator SHALL reconcile session/vector state before replaying mutations.

---

## 13. Session lifecycle mapping

Large-document baseline:

```text
STARTING_INGESTION
    ↓
ACTIVE session
    ↓
UPLOADING_BLOCKS
    ↓
FINALIZING_INGESTION
    ↓
FINALIZING session
    ↓
COMPLETED session
    ↓
WAITING_VECTOR_READY
    ↓
GetDocumentVectorStatus
    ↓
searchable=true
    ↓
job COMPLETED
```

AstraIndexator MUST persist enough session identity/progress to recover after process restart.

At minimum:

```text
ingestion_session_id
last confirmed batch_index
batch hashes / checkpoint references
final content hash when available
raw session status
last vector status
```

Exact persistence belongs to TZ-02/TZ-11/TZ-13.

---

## 14. Finalize ambiguity

If `FinalizeLogicalDocumentIngestion` times out or the connection fails after submission:

```text
Finalize -> ambiguous failure
        ↓
GetLogicalDocumentIngestionStatus
```

Then:

```text
FINALIZING -> poll/reconcile
COMPLETED  -> query vector status
ACTIVE     -> safe finalize replay only under same final-hash contract
FAILED     -> classify error
NOT_FOUND  -> recovery decision; no blind new version
```

AstraIndexator MUST NOT create a new document version merely because a Finalize ACK was lost.

---

## 15. Cancellation semantics

Cancellation operates on AstraIndexator processing intent and is cooperative.

### 15.1 Before downstream ingestion

If the job is still in parsing/OCR/splitting and no irreversible downstream mutation exists:

```text
cancel_requested=true
→ worker stops at safe checkpoint
→ job CANCELLED
```

### 15.2 Active ingestion session

If a session exists and is still safely abortable:

```text
AbortLogicalDocumentIngestion(reason)
```

followed by status reconciliation.

### 15.3 Finalizing/completed downstream state

If downstream is already `FINALIZING` or beyond, AstraIndexator MUST reconcile before deciding whether abort/delete is applicable.

Cancellation SHALL NOT be implemented as unconditional local `status=CANCELLED` while downstream indexing continues unknown.

---

## 16. Delete lifecycle

Deletion of indexed vectors is performed through the AstraVector public facade:

```text
DeleteDocumentVectorsFacade
```

AstraIndexator SHALL NOT delete Qdrant points directly.

Current downstream semantics are asynchronous.

Conceptual lifecycle:

```text
DELETE REQUESTED
    ↓
DeleteDocumentVectorsFacade
    ↓
DELETE_SCHEDULED
    ↓
DELETING
    ↓
DELETED
```

A successful delete RPC does not necessarily mean all search projection data has already been physically removed.

AstraIndexator must reconcile downstream status before reporting final deletion if the business API promises completed deletion.

---

## 17. Delete scope

Deletion is scoped by the exact downstream document reference:

```text
access_zone_id
document_id
document_version
```

AstraIndexator MUST NOT delete all versions of one `documentId` unless the producer explicitly requests document-wide deletion and the implementation enumerates/controls every affected version.

Baseline v1 producer operation SHOULD be version-scoped.

Document-wide delete may be introduced as an orchestration operation, not as an accidental wildcard.

---

## 18. Delete vs new-version race

Example:

```text
v1 DELETE REQUESTED
v2 BUILDING
```

These are independent version operations.

Deleting v1 MUST NOT cancel/delete v2 unless the producer explicitly requested document-wide deletion.

Conversely, a document-wide delete intent must prevent newly submitted versions from becoming active until the delete operation is reconciled according to business policy.

Exact locking/admission policy belongs to TZ-02/TZ-13.

---

## 19. TTL expiration

TTL policy is defined in TZ-10 and enforced by AstraVector.

AstraIndexator SHALL NOT run an independent TTL cleanup engine for AstraVector vectors.

Current conceptual downstream lifecycle:

```text
ACTIVE/searchable
    ↓ effective TTL elapsed
EXPIRED/non-searchable
    ↓ cleanup/reconciliation
projection removed/metadata retained according to AstraVector policy
```

AstraIndexator MAY project an expired state upstream but AstraVector remains authoritative for actual vector expiration/search exclusion.

---

## 20. Session expiry vs document expiry

These are distinct concepts.

```text
ingestion session expires_at
!=
document TTL expiration
```

Session expiry means chunked upload/finalization did not finish inside the session lifetime.

Document TTL means already indexed content reached its allowed lifetime.

AstraIndexator MUST use different status/error fields for these conditions.

---

## 21. Expired session recovery

If an ingestion session becomes `EXPIRED` before finalization:

1. do not assume document vectors exist;
2. query/reconcile document vector state where meaningful;
3. classify the session as unusable;
4. create/restart ingestion only using the same logical document/version identity and idempotency rules;
5. do not increment business document version solely because the transport session expired.

Exact recovery orchestration belongs to TZ-13.

---

## 22. Failed version behavior

If processing/indexing of a new version fails:

```text
new version FAILED
```

The previous successful/searchable version MUST remain unaffected unless business policy explicitly requested destructive replacement.

This is the default availability-preserving behavior.

A failed build must be diagnosable using:

```text
jobId
documentId
producerDocumentVersion
astraVectorDocumentVersion
processingAttemptId
ingestionSessionId
error code/message
processing stage
raw downstream state
```

---

## 23. Dead-letter behavior

`DEAD_LETTER` is an AstraIndexator job state, not an AstraVector document state.

A dead-lettered job means automated retry budget is exhausted or the job is treated as poison work.

The last known downstream state MUST remain attached for operator reconciliation.

Operators MUST NOT infer that `DEAD_LETTER` means downstream has no partial data.

TZ-13 defines reconciliation and repair procedures.

---

## 24. Prepared artifact lifecycle

Prepared artifacts such as:

```text
manifest.json
elements.jsonl
fragments.jsonl
```

are processing/recovery artifacts, not the authoritative vector document state.

They MAY be retained after successful indexing for:

- replay;
- debugging;
- reindexing;
- provenance audit;
- parser/OCR/splitter comparison.

Retention must be bounded and configurable.

Deleting vectors from AstraVector does not automatically imply immediate deletion of original/prepared SeaweedFS objects unless the platform business retention policy explicitly links them.

SeaweedFS retention belongs to TZ-03.

---

## 25. Source object lifecycle

Source object, prepared artifacts, and vector index are distinct resources:

```text
source object
!=
prepared canonical artifacts
!=
AstraVector document/vector state
```

Therefore create/update/delete operations must state which resource classes are affected.

AstraIndexator SHALL NOT infer business source deletion from vector deletion.

---

## 26. Lifecycle command model

Recommended conceptual producer commands:

```text
INDEX_NEW_VERSION
REINDEX_VERSION
DELETE_VERSION
CANCEL_JOB
```

A document-wide delete may be added separately:

```text
DELETE_DOCUMENT
```

Each command must create or reference an auditable durable operation/job rather than silently mutating storage state.

---

## 27. Version uniqueness

For one effective access zone and logical document, AstraIndexator SHALL prevent uncontrolled duplicate concurrent builds of the same target downstream version.

Conceptual uniqueness scope:

```text
(accessZone, documentId, astraVectorDocumentVersion, active lifecycle operation)
```

Exactly how this is enforced belongs to TZ-02 PostgreSQL constraints/admission logic.

---

## 28. Concurrent update policy

Baseline recommendation:

- allow different document IDs concurrently;
- allow historical delete and new-version build concurrently when semantics do not overlap;
- prevent two competing mutations of the same exact document version unless they are idempotent retries;
- serialize destructive replace/delete conflicts for the same target version.

The database coordinator is the authority for mutation admission.

---

## 29. Idempotency across lifecycle operations

Each mutating operation must have stable idempotency identity.

Examples:

```text
index:{documentId}:{version}:{contentHash}
delete:{accessZone}:{documentId}:{version}
reindex:{documentId}:{version}:{processingProfileVersion}:{contentHash}
```

These strings are conceptual. Exact key format belongs to TZ-11/TZ-13 implementation contracts.

A retry of one logical operation reuses the same key.

A materially different command must not reuse the same idempotency key.

---

## 30. Searchability invariant

AstraIndexator MUST distinguish:

```text
accepted
processed
finalized
vector-ready
searchable
```

The producer-facing term `INDEXED` or `COMPLETED` SHOULD mean `searchable=true` unless another completion contract is explicitly documented.

This avoids false success when vectors are staged but not visible to retrieval.

---

## 31. Retrieval continuity

When creating a new version, AstraIndexator should preserve retrieval continuity by not destroying the previous searchable version before the replacement is ready.

Desired behavior:

```text
v1 searchable
v2 building
→ retrieval still finds v1

v2 searchable
→ platform may switch effective version policy
→ v1 retained/expired/deleted according to policy
```

Exact retrieval version selection is owned by AstraVector/platform contracts and must be verified in TZ-17.

---

## 32. Rollback semantics

AstraIndexator 1.0 SHALL NOT claim automatic rollback unless explicitly implemented and verified.

Safe baseline:

- previous version remains intact during new-version build;
- failed new version is marked failed;
- operator/platform may continue using previous searchable version;
- destructive replacement is avoided by default.

Rollback from already-active new version to an older version requires explicit platform/AstraVector lifecycle support and is not assumed by TZ-12.

---

## 33. Error classification

Lifecycle errors should be classified at minimum as:

```text
VALIDATION_ERROR
CONFLICT
NOT_FOUND
TRANSIENT_DEPENDENCY
DOWNSTREAM_AMBIGUOUS
DOWNSTREAM_FAILED
SESSION_EXPIRED
DOCUMENT_EXPIRED
CANCELLED
SECURITY_ERROR
RESOURCE_LIMIT
INTERNAL_ERROR
```

The adapter must retain raw gRPC status/error details in diagnostics while exposing stable application error categories upstream.

---

## 34. Observability requirements

Lifecycle events must be traceable using:

```text
jobId
documentId
producerDocumentVersion
astraVectorDocumentVersion
accessZone
processingAttemptId
ingestionSessionId
operationId
processingStage
jobStatus
raw downstream state
```

Recommended lifecycle events:

```text
DOCUMENT_VERSION_ACCEPTED
DOCUMENT_VERSION_PROCESSING_STARTED
INGESTION_SESSION_STARTED
INGESTION_SESSION_FINALIZING
VECTOR_STATUS_READY
DOCUMENT_VERSION_SEARCHABLE
DOCUMENT_VERSION_FAILED
DELETE_SCHEDULED
DOCUMENT_DELETING
DOCUMENT_DELETED
DOCUMENT_EXPIRED
JOB_CANCELLED
```

High-cardinality identifiers MUST NOT be used blindly as metric labels.

---

## 35. Security requirements

Lifecycle commands are security-sensitive.

The system MUST NOT allow a producer to delete or mutate a document outside its authorized access-zone/document scope.

AstraIndexator must pass the exact resolved access-zone reference required by AstraVector and must not widen scope during delete/reindex operations.

Document-wide delete, if introduced, requires explicit authorization and enumeration semantics.

Detailed authorization/trust model belongs to TZ-16.

---

## 36. Acceptance criteria

### AC-01 — First indexing
A new document version reaches `COMPLETED` only after AstraVector reports it searchable.

### AC-02 — Stable identity
A source revision keeps the same `documentId` through processing, downstream ingestion and retrieval.

### AC-03 — New-version isolation
A failing new version does not remove or corrupt the previous successful version.

### AC-04 — Partial indexing protection
Partially staged/finalizing downstream data never results in AstraIndexator job `COMPLETED`.

### AC-05 — Finalize ambiguity
A lost Finalize ACK is recovered through status reconciliation, not by creating a new document version.

### AC-06 — Session expiry distinction
Session expiry is handled separately from document TTL expiry.

### AC-07 — Cancellation before ingestion
A job cancelled before downstream mutation reaches local `CANCELLED` without creating vector state.

### AC-08 — Cancellation with active session
An active session cancellation uses Abort/reconciliation rather than local-only state mutation.

### AC-09 — Delete is asynchronous
Delete RPC acceptance is not treated as final deletion until downstream state confirms completion according to the chosen API contract.

### AC-10 — Exact delete scope
Deleting one version does not delete another version of the same document.

### AC-11 — No direct Qdrant deletion
AstraIndexator never directly deletes Qdrant points as part of document lifecycle.

### AC-12 — TTL authority
Document expiration/search exclusion is governed by AstraVector/TZ-10 policy, not an independent Indexator cleanup engine.

### AC-13 — Reindex auditability
A reindex records the parser/OCR/normalizer/splitter/indexing profile versions that motivated or produced the new processing result.

### AC-14 — Duplicate submission
Exact same operation replay is idempotent; same key with changed content/intention conflicts.

### AC-15 — Same-version concurrency
Two non-idempotent concurrent mutations of the same target version are rejected/serialized.

### AC-16 — Recovery identity
Worker restart retains the same document/version/session identities and continues via reconciliation.

### AC-17 — Prepared artifact separation
Deleting vector state does not implicitly delete source/prepared artifacts unless explicit retention policy requires it.

### AC-18 — Failed build availability
A failed replacement build leaves the previous searchable version available.

### AC-19 — Searchability proof
An E2E test demonstrates `source -> index -> searchable -> retrieve` and verifies citation identity.

### AC-20 — Delete proof
An E2E test demonstrates `searchable -> DELETE_SCHEDULED/DELETING -> DELETED/non-searchable` without direct Qdrant manipulation.

---

## 37. Required verification evidence

Implementation based on TZ-12 must provide tests for:

- first document indexing;
- second version build while first remains searchable;
- failed second version build;
- duplicate source submission;
- explicit reindex;
- worker crash during session upload;
- worker crash during finalization;
- Finalize timeout/lost ACK;
- session expiry;
- document TTL expiry;
- cancel before Start;
- cancel after Start but before Finalize;
- cancel/finalize race;
- delete one version;
- delete timeout/reconciliation;
- delete vs new-version race;
- stale worker fencing during lifecycle mutation;
- restart using prepared artifacts;
- retrieve continuity across versions;
- raw downstream-state preservation.

---

## 38. Contract gaps / implementation gates

The following remain explicit gates and MUST NOT be invented locally:

1. final opaque producer-version -> AstraVector numeric-version strategy;
2. byte-exact `batch_content_hash` and `final_content_hash` canonicalization;
3. exact runtime guarantees of `replace_existing_version` before enabling destructive same-version replacement;
4. typed session-state/error-reason contract if AstraVector later publishes one;
5. exact platform rule for which document version retrieval considers effective/current when multiple versions coexist.

Until these are resolved, the safest baseline is immutable new-version indexing plus status reconciliation and non-destructive replacement behavior.

---

## 39. Architectural invariants established by TZ-12

1. `documentId` is stable across document revisions.
2. New source revisions normally create a new document version and a new job.
3. AstraIndexator job state and AstraVector vector/document state are separate lifecycles.
4. `COMPLETED` means searchable under the baseline completion contract.
5. Partial/finalizing downstream state is not success.
6. Lost mutation acknowledgements trigger reconciliation before replay/new operations.
7. Previous successful versions remain intact while replacements build.
8. Reindex is explicit, versioned and auditable.
9. Deletion uses AstraVector facade and is asynchronous/reconcilable.
10. AstraIndexator never deletes Qdrant directly.
11. Session expiry and document TTL expiry are different conditions.
12. AstraVector owns effective TTL expiration/search exclusion.
13. Cancellation is cooperative and downstream-aware.
14. Source objects, prepared artifacts and vector state have separate lifecycles.
15. Destructive same-version replacement is not baseline until verified.
16. Same-target concurrent destructive mutations must be serialized/rejected.
17. Recovery preserves identity and reconciles downstream state.

---

## 40. Next dependency

After TZ-12 the next critical specification is:

```text
TZ-13 — Reliability & Recovery
```

TZ-13 must turn the lifecycle rules into concrete recovery algorithms for:

```text
worker crash
lease loss
PostgreSQL outage
SeaweedFS outage
AstraVector timeout/unavailable
ambiguous Start/Append/Finalize/Delete
stale session
expired session
poison job
partial prepared artifact
partial vector projection
DEAD_LETTER reconciliation
manual/operator repair
```

TZ-13 must use the actual lifecycle and idempotency semantics defined by TZ-02, TZ-10, TZ-11 and TZ-12 rather than introducing a separate recovery state model.
