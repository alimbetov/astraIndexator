# TZ-12 — Document Lifecycle

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-12
- **Title:** Document Lifecycle
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-02, TZ-03, TZ-09, TZ-10, TZ-11, TZ-13, TZ-14, TZ-16, TZ-17
- **Authoritative downstream contract:** AstraVector `AstraVectorIngestionFacade`, `AstraVectorRetrievalFacade`, current public-facade semantics

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
- positive numeric `documentVersion` for the immutable source revision;
- optional opaque `externalRevision` metadata when the source system has its own revision string/hash;
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
!= documentVersion
!= externalRevision (optional metadata)
!= jobId
!= processingAttemptId
!= ingestionSessionId
!= fragmentId/blockId
!= AstraVector generated chunk IDs
```

### 4.1 `documentId`

Stable logical document identity across revisions.

Example:

```text
20fd6906-cf10-4d2a-bdbf-31ae32316716
```

### 4.2 `documentVersion`

AstraIndexator 1.0 canonical version is a **positive numeric immutable version**:

```text
value > 0
semantic domain = positive long/uint64
```

The producer SHALL supply the canonical numeric version for direct interoperability. AstraIndexator SHALL NOT maintain a second opaque canonical version identity.

The current `llm2` wire contract has a width difference that is handled only at the adapter boundary according to TZ-09/TZ-11:

```text
DocumentIdentity.document_version = uint64
DocumentRef.document_version      = uint64
session Start document_version    = uint32
```

Therefore a value outside the selected wire path's supported range is rejected explicitly; it is never truncated, wrapped or remapped ad hoc.

### 4.3 `externalRevision`

If a source system uses a value such as:

```text
rev-17
sha256:...
ERP-2026-08-22-A
```

it MAY be retained as immutable metadata named `externalRevision` (or equivalent). It is not a replacement for `documentVersion`, is not used as the AstraVector numeric version, and MUST NOT introduce a hidden numeric mapping lifecycle.

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

TZ-02 owns the canonical indexing-stage vocabulary. TZ-12 SHALL use the same names rather than define a competing enum:

```text
ACQUIRING
VALIDATING
PARSING
OCR_PROCESSING
NORMALIZING
SPLITTING
PREPARING_ARTIFACTS
DELIVERING / APPENDING_BLOCKS
FINALIZING
FINALIZING_VECTOR_STATE
```

Interpretation for session ingestion:

```text
DELIVERING / APPENDING_BLOCKS
  includes StartLogicalDocumentIngestion + AppendLogicalDocumentBlocks

FINALIZING
  includes FinalizeLogicalDocumentIngestion / session finalization

FINALIZING_VECTOR_STATE
  includes GetDocumentVectorStatus reconciliation until searchable=true
```

Lifecycle-only operations MAY expose an additional explicit stage such as `DELETING_VECTORS`, but the indexing pipeline MUST NOT introduce aliases such as `UPLOADING_BLOCKS` or `WAITING_VECTOR_READY` as a second canonical enum.

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

A logical document may have multiple immutable numeric versions:

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

Baseline completion definition for AstraIndexator 1.0:

```text
COMPLETED == downstream vector status proves document searchable
```

A weaker completion level, if ever introduced, must use a different explicit producer-facing semantic and cannot silently redefine `COMPLETED`.

---

## 8. New source revision

When source content changes, the preferred lifecycle is:

```text
same documentId
+
new positive documentVersion
+
new jobId
```

Example:

```text
DOC-100 / version 1 -> historical/current
DOC-100 / version 2 -> new build
```

The old version SHALL remain untouched while the new version is still processing unless a separately approved policy says otherwise.

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

A reindex SHALL be explicit and auditable and MUST NOT silently overwrite historical processing provenance.

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

If the producer resubmits the exact same logical revision with the same `documentId`, `documentVersion`, content hash and effective processing intent, AstraIndexator SHOULD treat the operation idempotently.

Possible result:

```text
existing successful version found
→ no duplicate vector document created
→ return/reuse known state
```

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

Large-document baseline using TZ-02 canonical processing stages:

```text
DELIVERING / APPENDING_BLOCKS
    ↓ StartLogicalDocumentIngestion
ACTIVE session
    ↓ AppendLogicalDocumentBlocks x N
FINALIZING
    ↓ FinalizeLogicalDocumentIngestion
FINALIZING session
    ↓
COMPLETED session
    ↓
FINALIZING_VECTOR_STATE
    ↓ GetDocumentVectorStatus
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

Current downstream semantics are asynchronous:

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

These are distinct concepts:

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
5. do not increment `documentVersion` solely because the transport session expired.

Exact recovery orchestration belongs to TZ-13.

---

## 22. Failed version behavior

If processing/indexing of a new version fails:

```text
new version FAILED
```

The previous successful/searchable version MUST remain unaffected unless business policy explicitly requested destructive replacement.

A failed build must be diagnosable using:

```text
jobId
documentId
documentVersion
externalRevision (optional)
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

They MAY be retained after successful indexing for replay, debugging, reindexing, provenance audit and parser/OCR/splitter comparison.

Retention must be bounded and configurable.

Deleting vectors from AstraVector does not automatically imply immediate deletion of original/prepared SeaweedFS objects unless the platform business retention policy explicitly links them.

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

For one effective access zone and logical document, AstraIndexator SHALL prevent uncontrolled duplicate concurrent builds of the same target version.

Conceptual uniqueness scope:

```text
(accessZone, documentId, documentVersion, active lifecycle operation)
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
documentVersion
externalRevision (optional)
accessZoneId
accessZoneCode
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

## 35. Trust-boundary requirements

Lifecycle commands are trust-sensitive internal operations.

The system MUST NOT allow a producer to delete or mutate a document outside its assigned access-zone/document scope.

AstraIndexator must pass the exact resolved access-zone reference required by AstraVector and must not widen scope during delete/reindex operations.

Document-wide delete, if introduced, requires explicit trusted-platform authorization/enumeration semantics according to TZ-16.

---

## 36. Acceptance criteria

### AC-01 — First indexing
A new document version reaches `COMPLETED` only after AstraVector reports it searchable.

### AC-02 — Stable identity
A source revision keeps the same `documentId` and positive numeric `documentVersion` through processing, downstream ingestion and retrieval.

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

### AC-21 — Version contract
Opaque external source revisions remain metadata only; no hidden opaque-to-numeric document-version mapping exists in AstraIndexator 1.0.

### AC-22 — Stage vocabulary
Indexing lifecycle status uses the TZ-02 canonical processing-stage vocabulary.

---

## 37. Required verification evidence

Implementation based on TZ-12 must provide tests for:

- positive numeric `documentVersion` validation and wire-range guards;
- optional `externalRevision` persistence without identity substitution;
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
- raw downstream-state preservation;
- processing-stage enum consistency with TZ-02.

---

## 38. Contract gaps / implementation gates

The following remain explicit gates and MUST NOT be invented locally:

1. byte-exact `batch_content_hash` and `final_content_hash` canonicalization;
2. exact runtime guarantees of `replace_existing_version` before enabling destructive same-version replacement;
3. typed session-state/error-reason contract if AstraVector later publishes one;
4. exact platform rule for which document version retrieval considers effective/current when multiple versions coexist.

The opaque-version mapping issue is **closed for AstraIndexator 1.0**: canonical `documentVersion` is positive numeric; opaque source revisions are metadata only.

---

## 39. Architectural invariants established by TZ-12

1. `documentId` is stable across document revisions.
2. `documentVersion` is a positive numeric immutable version; external opaque revisions are metadata only.
3. New source revisions normally create a new document version and a new job.
4. AstraIndexator job state and AstraVector vector/document state are separate lifecycles.
5. `COMPLETED` means searchable under the baseline completion contract.
6. Partial/finalizing downstream state is not success.
7. Lost mutation acknowledgements trigger reconciliation before replay/new operations.
8. Previous successful versions remain intact while replacements build.
9. Reindex is explicit, versioned and auditable.
10. Deletion uses AstraVector facade and is asynchronous/reconcilable.
11. AstraIndexator never deletes Qdrant directly.
12. Session expiry and document TTL expiry are different conditions.
13. AstraVector owns effective TTL expiration/search exclusion.
14. Cancellation is cooperative and downstream-aware.
15. Source objects, prepared artifacts and vector state have separate lifecycles.
16. Destructive same-version replacement is not baseline until verified.
17. Same-target concurrent destructive mutations must be serialized/rejected.
18. Recovery preserves identity and reconciles downstream state.
19. Indexing processing stages use the canonical TZ-02 vocabulary.

---

## 40. Next dependency

TZ-13 Reliability & Recovery remains the authoritative recovery algorithm specification and SHALL consume the lifecycle/idempotency contracts from TZ-02, TZ-10, TZ-11 and this TZ without introducing a separate identity or stage model.
