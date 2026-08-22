# TZ-13 — Reliability & Recovery

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-13
- **Title:** Reliability & Recovery
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-02, TZ-03, TZ-04, TZ-09, TZ-10, TZ-11, TZ-12, TZ-14, TZ-16, TZ-17, TZ-18
- **Authoritative downstream boundary:** AstraVector `AstraVectorIngestionFacade` and status/reconciliation APIs

---

## 2. Purpose

This specification defines failure handling, recovery, replay, reconciliation and operator intervention for AstraIndexator.

The objective is not to eliminate repeated execution. The objective is to guarantee that accepted work is either:

1. completed and proven searchable;
2. deterministically waiting for retry/recovery;
3. classified as permanent failure/dead-letter with enough evidence to remediate;
4. cancelled/deleted according to an explicit lifecycle decision.

The system SHALL provide:

```text
at-least-once processing
+
lease/fencing ownership
+
idempotent downstream mutation
+
durable checkpoints
+
state reconciliation
+
operator-recoverable terminal failure
```

AstraIndexator SHALL NOT claim distributed exactly-once semantics across PostgreSQL, SeaweedFS and AstraVector.

---

## 3. Recovery invariants

The following are normative.

### RI-01 — PostgreSQL job state is the AstraIndexator coordination authority

A worker may execute work only while it owns the current lease generation defined in TZ-02.

### RI-02 — AstraVector is authoritative for downstream vector/document state

AstraIndexator SHALL NOT infer searchability from local RPC success alone.

### RI-03 — Qdrant is not an AstraIndexator recovery source

AstraIndexator does not directly repair/delete Qdrant points. AstraVector owns PostgreSQL/Qdrant reconciliation.

### RI-04 — Repeated execution is expected

A crash, timeout or network partition MAY cause the same logical stage to execute again.

Every externally visible mutation MUST therefore be either:

- idempotent;
- replay-safe via deterministic identity/hash;
- or reconciled before retry.

### RI-05 — Stale workers are fenced

A worker whose `lease_generation` is no longer current MUST NOT commit authoritative job transitions or continue downstream mutations after ownership loss is detected.

### RI-06 — Recovery prefers durable checkpoints over recomputation

If a compatible prepared artifact or ingestion checkpoint exists and validates successfully, recovery SHOULD resume from it rather than repeat parser/OCR/splitting.

### RI-07 — Ambiguous outcome is not failure confirmation

Transport timeout after a mutating call means `UNKNOWN_OUTCOME` until state is reconciled.

### RI-08 — Partial indexing is never equivalent to success

`COMPLETED` requires the completion contract from TZ-12: AstraVector must prove the intended document version is searchable.

---

## 4. Failure-domain model

AstraIndexator SHALL reason about independent failure domains:

```text
Producer / job submission
PostgreSQL coordinator
AstraIndexator worker process/pod
local temporary workspace
SeaweedFS
Parser / OCR / Normalizer / Splitter
prepared artifacts
network between Indexator and AstraVector
AstraVector facade
AstraVector PostgreSQL/outbox/Qdrant projection
Kubernetes/node/runtime
```

A single recovery action MUST NOT assume simultaneous failure of all domains.

---

## 5. Failure classification

Every processing error SHALL be classified before retry.

Canonical classes:

```text
TRANSIENT
PERMANENT_INPUT
PERMANENT_POLICY
RESOURCE_LIMIT
DOWNSTREAM_AMBIGUOUS
OWNERSHIP_LOST
CANCELLED
INTERNAL_BUG
DEPENDENCY_UNAVAILABLE
```

### 5.1 TRANSIENT

Examples:

- temporary network failure;
- AstraVector `UNAVAILABLE`;
- SeaweedFS timeout;
- PostgreSQL transient connection acquisition failure;
- capacity throttling that is known to be temporary.

Action: bounded retry/backoff.

### 5.2 PERMANENT_INPUT

Examples:

- unsupported MIME/type;
- corrupt document;
- malformed logical block tree;
- invalid access-zone code syntax;
- irreconcilable document-version contract.

Action: `FAILED`, no automatic blind retry.

### 5.3 PERMANENT_POLICY

Examples:

- access-zone mismatch;
- permission denied;
- disabled/missing required access zone;
- explicit lifecycle precondition violation.

Action: `FAILED`, operator/business correction required.

### 5.4 RESOURCE_LIMIT

Examples:

- source exceeds configured maximum;
- page/image/pixel limits exceeded;
- document exceeds AstraVector block/session limits.

Action depends on whether the limit is configurable/recoverable. Blind retry with identical parameters is prohibited.

### 5.5 DOWNSTREAM_AMBIGUOUS

Examples:

- Start RPC timeout after request transmission;
- Append RPC timeout;
- Finalize RPC timeout;
- Delete RPC timeout.

Action: reconcile downstream state before mutation replay.

### 5.6 OWNERSHIP_LOST

Detected when lease renewal/fenced mutation affects zero rows or DB connectivity prevents proving lease ownership beyond the safe interval.

Action: stop authoritative work and exit/abandon the current attempt safely.

### 5.7 INTERNAL_BUG

Unexpected invariant violation, serialization defect or code error.

Action: bounded retry only if known safe; otherwise DEAD_LETTER after policy threshold and alert.

---

## 6. Retry policy

Retry configuration SHALL be stage-aware and bounded.

A generic baseline may use:

```text
attempt delay = exponential backoff + jitter
```

with deployment-configured limits.

TZ-02 baseline values MAY be used initially:

```text
base delay ~= 5s
max delay  ~= 15m
max attempts ~= 8
```

These are operational defaults, not permanent protocol constants.

A retry MUST NOT:

- reset document identity;
- generate a new AstraVector document version for the same logical attempt;
- regenerate an idempotency key after ambiguous timeout;
- restart a TTL as if the document were newly accepted;
- mutate access-zone assignment;
- bypass lease fencing.

---

## 7. Poison-job policy

A poison job is a job that repeatedly fails deterministically or exhausts the automatic retry budget.

Poison handling:

```text
retryable attempts exhausted
        ↓
DEAD_LETTER
        ↓
operator inspection/remediation
        ↓
explicit requeue only
```

DEAD_LETTER record/evidence SHALL preserve at least:

```text
jobId
documentId/documentVersion
source reference
last processing stage
attempt count
last error code/class/message
last worker/lease generation
prepared artifact reference if any
ingestion session id if any
raw downstream status/error if any
created/failed timestamps
```

Operator requeue SHALL create a new processing attempt and increment lease generation on claim; it SHALL NOT erase historical attempts.

---

## 8. Worker-crash recovery

### 8.1 Crash before durable claim commit

No ownership was established. Another worker may claim normally.

### 8.2 Crash after claim but before side effects

Lease expires. Another worker reclaims the job and starts a new attempt.

### 8.3 Crash during parser/OCR/normalization

If no valid prepared checkpoint exists:

```text
reclaim
→ clean/recreate local workspace
→ reacquire source
→ repeat deterministic processing
```

If a valid intermediate/prepared artifact exists and the relevant schema/tool/profile versions match, recovery MAY resume from it.

### 8.4 Crash after prepared artifacts persisted

Preferred recovery:

```text
reclaim
→ verify manifest/schema/hash/version compatibility
→ reuse elements/fragments artifacts
→ continue AstraVector delivery
```

Expensive OCR SHALL NOT be repeated merely because the worker process changed.

### 8.5 Crash during AstraVector session delivery

Recovery SHALL use persisted session checkpoints from TZ-11 and downstream status reconciliation before opening a replacement session.

---

## 9. Lease expiry and stale-worker fencing

Canonical scenario:

```text
worker A owns generation 17
worker A stalls
lease expires
worker B claims generation 18
worker A resumes
```

Worker A MUST NOT be able to:

- renew generation 17;
- mark job COMPLETED/FAILED;
- advance durable delivery checkpoints;
- finalize/cancel/delete as authoritative owner once ownership loss is known.

Every local authoritative mutation SHALL include the current fencing token predicate.

Long downstream RPCs SHOULD be bounded by deadlines shorter than a safely renewable ownership window or accompanied by a lease-renewal strategy that does not violate cancellation/fencing rules.

If ownership becomes uncertain during a downstream mutation, the outcome is treated as ambiguous and the new owner reconciles it.

---

## 10. PostgreSQL outage semantics

PostgreSQL is required to prove worker ownership.

If the worker cannot renew/verify its lease:

1. it MAY finish purely local computation for a short configured grace only if no authoritative side effect is performed;
2. it MUST stop new downstream mutations when ownership can no longer be proven;
3. it MUST NOT mark completion/failure using stale cached state;
4. after DB recovery, work is reclaimed according to lease expiry/fencing.

AstraIndexator SHALL prefer duplicate-safe replay after recovery over split-brain mutation during DB uncertainty.

---

## 11. SeaweedFS failure recovery

### 11.1 Source temporarily unavailable

Classify as dependency/transient failure and retry with bounded backoff.

### 11.2 Source object missing

If the durable job references an object that does not exist after bounded consistency/retry checks:

```text
SOURCE_NOT_FOUND
→ permanent input/integration failure
```

The system SHALL NOT silently create an empty document.

### 11.3 Source changed unexpectedly

When source `etag/versionId/contentHash` is available, AstraIndexator SHOULD verify immutability.

If fetched source bytes do not match the durable processing intent:

```text
SOURCE_CONTENT_MISMATCH
→ fail closed
```

A changed source requires a new version/job, not silent replacement under the same processing identity.

### 11.4 Prepared artifact unavailable/corrupt

A corrupt prepared artifact MAY be discarded and regenerated from the source if the source is intact and the operation remains deterministic.

Artifact corruption SHALL be logged/metriced distinctly from source corruption.

---

## 12. Local workspace recovery

Local disk is ephemeral and non-authoritative.

On new processing attempt:

- workspace path must be attempt-scoped;
- stale temporary files from previous pod/process MUST NOT be trusted as durable checkpoint;
- downloaded files SHOULD be validated before use;
- cleanup must be idempotent;
- failure to clean a stale workspace must not cause use of mixed-version contents.

Disk-full and inode-exhaustion conditions SHALL be classified as resource/dependency failures and surfaced operationally.

---

## 13. Prepared-artifact recovery

Prepared artifacts defined by TZ-09 SHALL include enough information to determine replay compatibility.

Recovery validator SHALL consider at least:

```text
canonical schema version
source content hash
parser version/profile
OCR engine/model/profile when used
normalizer version
splitter version/profile
documentId/documentVersion
artifact manifest integrity
```

If all required compatibility checks pass, artifact replay is permitted.

If a material processing version differs, the artifact SHALL NOT be reused as if equivalent; reprocessing/reindex policy from TZ-12 applies.

Artifacts SHALL be stream-readable so recovery of a large document does not require loading all fragments into memory.

---

## 14. AstraVector Start recovery

Canonical ambiguous case:

```text
StartLogicalDocumentIngestion
→ DEADLINE_EXCEEDED / connection lost
```

Recovery:

```text
retry SAME logical Start
using SAME idempotency key
```

AstraIndexator MUST NOT generate a new document version or new idempotency identity merely because the Start acknowledgement was lost.

If an ingestion session identity/status has been persisted, it SHOULD be reconciled first.

---

## 15. Append recovery

For session batch `N`:

```text
Append(batchIndex=N, hash=H)
→ timeout
```

Recovery shape:

```text
replay batchIndex=N with SAME H and SAME logical block bytes/representation
```

Same batch index with a different hash is an integrity conflict and MUST NOT be auto-healed by overwriting the server state.

A reclaimed worker SHALL regenerate or load the same deterministic batch partitioning/checkpoint mapping before replay.

Until the canonical hashing algorithm and golden vectors are formally published, production session replay remains gated by TZ-11 GAP-01.

---

## 16. Finalize recovery

Finalize is a mutating operation with potentially ambiguous outcome.

After timeout/error where request acceptance is unknown:

```text
GetLogicalDocumentIngestionStatus
```

Decision matrix:

```text
ACTIVE      → retry Finalize using the same final hash when contract permits
FINALIZING  → poll/reconcile; do not create another session
COMPLETED   → proceed to GetDocumentVectorStatus
FAILED      → classify using server error; retry only if explicitly safe
ABORTED     → cancellation/lifecycle decision
EXPIRED     → session recovery decision; do not pretend document TTL expired
UNKNOWN     → conservative retry/reconciliation path
```

`Finalize RPC success` alone SHALL NOT mark the AstraIndexator job `COMPLETED`.

---

## 17. Vector readiness reconciliation

After session completion or single-call ingestion, AstraIndexator SHALL reconcile using:

```text
GetDocumentVectorStatus
```

A job may be completed only when the TZ-12 completion condition is satisfied:

```text
searchable = true
```

Intermediate downstream states such as:

```text
INDEXING
VECTORING
PUBLISHING
SYNCING
READY_TO_ACTIVATE
```

are non-terminal from AstraIndexator's success perspective.

If AstraVector reports `FAILED`, the error/retryability contract determines whether AstraIndexator enters `RETRY_WAIT`, `FAILED`, or eventually `DEAD_LETTER`.

---

## 18. AstraVector/Qdrant degradation

AstraIndexator SHALL interact only with AstraVector public/admin/status contracts.

It SHALL NOT:

- inspect Qdrant directly to decide document success;
- write missing vectors directly into Qdrant;
- delete Qdrant points directly;
- rebuild Qdrant independently.

AstraVector's architectural invariant is:

```text
PostgreSQL = canonical vector/document state
Qdrant     = rebuildable search projection
```

Therefore projection repair/reconciliation is delegated to AstraVector. Deployment recovery documentation likewise treats PostgreSQL as primary state and Qdrant as rebuildable projection.

---

## 19. Delete recovery

Deletion is asynchronous per TZ-12.

Canonical operation:

```text
DeleteDocumentVectorsFacade
→ DELETE_SCHEDULED / intermediate state
→ DELETING
→ DELETED
```

If Delete RPC times out:

- do not issue unrelated replacement deletion identities;
- reconcile the document/vector state;
- replay only when the facade semantics prove replay safe.

Local source/prepared-artifact deletion SHALL remain a separate retention operation and MUST NOT be coupled transactionally to Qdrant/vector deletion.

---

## 20. Cancellation recovery

Cancellation is cooperative and stage-aware.

If no downstream session/mutation exists, the current worker may transition to `CANCELLED` at a safe fenced checkpoint.

If an active ingestion session exists, cancellation SHOULD invoke/reconcile `AbortLogicalDocumentIngestion` as specified in TZ-12.

If downstream state is `FINALIZING` or later, AstraIndexator MUST reconcile instead of assuming abort succeeded.

A stale worker MUST NOT complete cancellation after its lease generation is superseded.

---

## 21. Session expiry versus document expiry

These concepts remain distinct:

```text
ingestion session expiry
!=
document TTL expiry
```

Session expiry means the staging/upload session can no longer be continued under that session identity.

Document expiry is owned by AstraVector TTL policy and affects searchability/lifecycle after ingestion.

Recovery logic SHALL NOT map one automatically to the other.

---

## 22. Same-version concurrent job conflict

The system SHALL prevent two independent jobs from concurrently producing incompatible mutations for the same effective tuple:

```text
(accessZone, documentId, AstraVectorDocumentVersion)
```

Possible implementation mechanisms belong to TZ-02/TZ-12, but recovery behavior is normative:

- one operation becomes authoritative;
- the other reconciles existing state;
- no automatic version bump is used to escape contention;
- ambiguous duplicate jobs reuse the same logical identity if they represent the same request.

---

## 23. Reindex recovery

Reindex is an explicit lifecycle operation, not an error retry.

If a reindex fails while an older version remains searchable:

```text
old version remains available
new/reindexed version remains non-successful
```

Recovery SHALL NOT delete the healthy prior version simply because the replacement job failed.

A new parser/OCR/splitter/model contract may require regeneration of prepared artifacts; incompatible old artifacts must not be reused.

---

## 24. Reconciliation worker

AstraIndexator SHOULD include a periodic reconciliation capability independent of normal claim processing.

Its purpose is to identify durable states that need correction, not to bypass normal job ownership.

Candidate queries/checks:

```text
PROCESSING jobs with expired lease
RETRY_WAIT jobs whose next_retry_at passed
jobs stuck in downstream-wait stage beyond threshold
jobs with session_id but no recent downstream status update
jobs locally COMPLETED whose downstream proof is missing/inconsistent
cancel_requested jobs still active
DEAD_LETTER counts/trends
orphaned prepared artifacts / source artifacts according to retention policy
```

A reconciliation worker MUST use the same fencing/state-transition rules as normal workers.

---

## 25. Recovery checkpoint model

A job may persist recoverable checkpoints such as:

```text
source_verified_at
source_content_hash
prepared_manifest_ref
prepared_schema_version
ingestion_mode
ingestion_session_id
last_accepted_batch_index
next_batch_index
final_content_hash
downstream_raw_state
downstream_checked_at
vector_searchable_proof_at
```

Checkpoint writes MUST be fenced by current lease generation while the job is actively owned.

Checkpoint values SHALL be sufficient for another replica to determine the next safe action without relying on process memory.

---

## 26. Recovery decision algorithm

A reclaimed job SHOULD follow this conceptual sequence:

```text
1. acquire new lease generation
2. load durable job + attempt + checkpoints
3. inspect cancellation/deletion intent
4. validate source/prepared artifact compatibility
5. inspect downstream session/document identity
6. reconcile ambiguous downstream state
7. determine earliest safe resume stage
8. execute idempotently
9. persist fenced checkpoint
10. continue until searchable / terminal classification
```

Recovery MUST be state-driven, not simply "restart pipeline from step 1".

---

## 27. Retry versus reconciliation decision table

| Situation | Default action |
|---|---|
| network failure before any mutation | retry |
| mutating RPC timeout | reconcile first |
| `UNAVAILABLE` | bounded retry |
| capacity `RESOURCE_EXHAUSTED` | bounded backoff |
| deterministic size limit exceeded | permanent/config remediation |
| `INVALID_ARGUMENT` | permanent payload failure |
| `PERMISSION_DENIED` | policy/security failure |
| `FAILED_PRECONDITION` lifecycle state | reconcile |
| same batch index + different hash | integrity failure |
| worker lease lost | stop attempt |
| source missing | permanent unless external consistency policy says retry |
| prepared artifact corrupt | regenerate from source if possible |
| AstraVector searchable=false while intermediate | poll/reconcile |
| AstraVector FAILED retryable | `RETRY_WAIT` |
| retry budget exhausted | `DEAD_LETTER` |

---

## 28. Dead-letter remediation

Operator tooling/runbook SHOULD support:

```text
inspect job
inspect all attempts
inspect last/downstream states
inspect source/prepared artifact references
classify root cause
correct external configuration/data if needed
explicit requeue
```

Manual requeue SHALL record:

```text
requeued_by
requeued_at
reason
previous terminal state
```

History MUST NOT be deleted.

---

## 29. Graceful shutdown

On SIGTERM/pod termination, a worker SHOULD:

1. stop claiming new work;
2. stop starting new heavy stages/downstream sessions;
3. attempt to finish or checkpoint bounded in-flight work within shutdown budget;
4. persist safe checkpoints when possible;
5. avoid extending lease far beyond process lifetime;
6. permit lease expiry/reclaim when work cannot finish.

Kubernetes termination grace period MUST be coordinated with worker shutdown and lease duration in TZ-18.

---

## 30. Backpressure and overload recovery

Reliability includes refusing excess work before resource collapse.

AstraIndexator SHALL use bounded:

```text
active jobs per replica
parser concurrency
OCR concurrency
download concurrency
gRPC in-flight requests
memory/disk workspace usage
```

Overload SHOULD result in queued work / delayed claim / bounded retry rather than OOM-driven crash loops.

A downstream `RESOURCE_EXHAUSTED` response must distinguish capacity from permanent document-size constraints where possible.

---

## 31. Observability requirements

Recovery events SHALL be observable with structured fields:

```text
jobId
documentId
producerDocumentVersion
astraVectorDocumentVersion
processingAttemptId
workerId
leaseGeneration
processingStage
recoveryAction
errorClass
errorCode
ingestionSessionId
batchIndex
downstreamState
```

Recommended metrics:

```text
job_reclaim_total
lease_expired_total
ownership_lost_total
recovery_resume_total{stage}
prepared_artifact_reuse_total
prepared_artifact_invalid_total
astravector_reconcile_total{operation}
ambiguous_rpc_total{operation}
retry_total{class,stage}
dead_letter_total
requeue_total
stuck_job_total
recovery_duration_seconds
```

High-cardinality identities MUST NOT be metric labels.

Detailed telemetry belongs to TZ-14.

---

## 32. Security requirements during recovery

Recovery MUST NOT weaken normal access/security checks.

Specifically:

- requeue does not bypass access-zone validation;
- stored credentials/tokens are not embedded in checkpoints;
- source links/metadata must not contain reusable secrets;
- a recovered job uses current runtime credentials through secret management;
- operators need explicit authorization for dead-letter requeue/delete actions;
- access-zone mismatch is fail-closed.

Detailed security policy belongs to TZ-16.

---

## 33. Recovery data retention

Attempt/checkpoint/error history SHALL be retained long enough to diagnose production failures.

Prepared artifacts may use independent retention policy.

Vector deletion MUST NOT automatically erase audit history required to explain previous processing.

Retention periods are deployment/policy configuration and belong to TZ-03/TZ-14/TZ-18.

---

## 34. Golden failure-recovery scenarios

TZ-17 SHALL implement at least the following E2E proofs.

### FR-01 — worker crash during OCR

```text
claim → OCR → kill worker → lease expiry → new worker reclaim → complete
```

Expected: one final searchable document, no orphaned job.

### FR-02 — crash after prepared artifact

```text
prepared artifact persisted → kill worker → reclaim → artifact reused → ingestion continues
```

Expected: parser/OCR not repeated unnecessarily.

### FR-03 — Start ACK lost

```text
Start accepted server-side → client timeout → retry same idempotency key
```

Expected: one logical ingestion session/document operation.

### FR-04 — Append ACK lost

```text
batch N accepted → timeout → replay same N/hash
```

Expected: no duplicate/corrupt batch.

### FR-05 — Finalize ACK lost

```text
Finalize accepted → timeout → status reconciliation
```

Expected: no duplicate document version/session.

### FR-06 — stale worker resumes

```text
A generation X stalls → B claims X+1 → A resumes
```

Expected: A cannot finalize/overwrite state.

### FR-07 — PostgreSQL outage during processing

Expected: worker stops authoritative downstream mutation after ownership cannot be proven; later reclaim completes safely.

### FR-08 — SeaweedFS transient outage

Expected: bounded retry; no duplicate downstream version.

### FR-09 — AstraVector unavailable

Expected: job enters retry/reconciliation path; prepared artifacts retained; no reparse required.

### FR-10 — Qdrant projection loss while AstraVector PostgreSQL survives

Expected: AstraIndexator does not rebuild Qdrant directly; AstraVector recovery/reconciliation restores search projection.

### FR-11 — cancellation during ACTIVE session

Expected: abort/reconcile; no false `CANCELLED` while downstream continues unknowingly.

### FR-12 — failed new version while old version remains searchable

Expected: old version remains available.

### FR-13 — retry budget exhausted

Expected: DEAD_LETTER with complete attempt/error evidence and explicit operator requeue path.

---

## 35. Acceptance criteria

### AC-01 — At-least-once correctness

Repeated processing of the same logical job after crash does not produce an uncontrolled duplicate downstream document.

### AC-02 — Lease recovery

An expired lease is reclaimed by another replica.

### AC-03 — Fencing

A stale lease generation cannot complete/overwrite the current job.

### AC-04 — DB uncertainty safety

Loss of PostgreSQL ownership proof stops new authoritative mutation.

### AC-05 — Prepared artifact reuse

Compatible artifacts are reused after worker crash.

### AC-06 — Artifact invalidation

Incompatible/corrupt artifacts are not silently reused.

### AC-07 — Start ambiguity

Lost Start ACK is recovered with the same idempotency identity.

### AC-08 — Append ambiguity

Lost Append ACK is recovered by replaying the same batch index/hash.

### AC-09 — Append integrity conflict

Same batch index with different hash is rejected/escalated, never overwritten automatically.

### AC-10 — Finalize ambiguity

Finalize timeout causes status reconciliation rather than blind version/session recreation.

### AC-11 — Searchability gate

Local `COMPLETED` requires downstream searchability proof.

### AC-12 — Poison handling

Retry exhaustion produces DEAD_LETTER with durable diagnostics.

### AC-13 — Explicit requeue

Dead-letter/permanent failures return to processing only by explicit auditable requeue.

### AC-14 — Source immutability

Source mismatch under an accepted job is detected and fails closed.

### AC-15 — Qdrant ownership boundary

AstraIndexator never repairs or deletes Qdrant directly.

### AC-16 — Cancellation race

Cancellation during active/finalizing downstream work is reconciled correctly.

### AC-17 — Graceful shutdown

A terminating worker stops claims and leaves in-flight work recoverable.

### AC-18 — Bounded resource behavior

Failure/overload does not create unbounded concurrency/retry storms.

### AC-19 — Same-version conflict

Concurrent same-version work cannot produce two divergent authoritative results.

### AC-20 — Old-version availability

Failure of replacement/reindex does not destroy a previously healthy searchable version unless explicitly requested by lifecycle policy.

### AC-21 — Session/document expiry distinction

Tests prove ingestion session expiry is not treated as document TTL expiry.

### AC-22 — Recovery observability

Every reclaim/retry/reconciliation/dead-letter transition is traceable in logs/history.

---

## 36. Required verification evidence

Implementation based on TZ-13 SHALL provide:

- PostgreSQL/Testcontainers multi-worker lease/fencing tests;
- kill/restart worker integration tests;
- SeaweedFS transient/missing-object fixtures;
- prepared artifact replay/corruption tests;
- AstraVector facade fault-injection tests for timeout/UNAVAILABLE/RESOURCE_EXHAUSTED;
- Start/Append/Finalize ambiguous-outcome tests;
- stale-worker fencing proof;
- graceful shutdown test;
- cancellation/delete race tests;
- dead-letter/requeue audit tests;
- large-document bounded-memory recovery test;
- E2E searchable result after crash/recovery;
- evidence that Qdrant recovery is delegated to AstraVector rather than implemented in AstraIndexator.

---

## 37. Known contract gaps / implementation gates

TZ-13 inherits unresolved gates from TZ-11/TZ-12:

1. byte-exact canonical algorithm and golden vectors for `batch_content_hash` and `final_content_hash`;
2. deterministic producer-version → AstraVector numeric-version mapping when producer version is non-numeric;
3. final production semantics of `replace_existing_version`;
4. stronger typed session/error reasons when AstraVector publishes them.

Recovery implementation MUST NOT invent incompatible behavior to bypass these gaps.

---

## 38. Architectural invariants established by TZ-13

1. Reliability is based on at-least-once execution plus idempotency/reconciliation, not distributed exactly-once claims.
2. PostgreSQL lease/fencing is the authority for AstraIndexator worker ownership.
3. Ambiguous mutating outcomes require reconciliation before unsafe replay.
4. Compatible prepared artifacts are durable recovery checkpoints.
5. Downstream session/vector status is persisted sufficiently for another replica to resume safely.
6. Searchability, not RPC acceptance, is the success gate.
7. AstraVector owns vector state and Qdrant reconciliation.
8. Stale workers cannot finalize authoritative state.
9. Retry budgets are finite; poison work becomes DEAD_LETTER.
10. Requeue is explicit and auditable.
11. Recovery never silently changes document identity, access zone, TTL intent or version.
12. Failed replacement/reindex does not automatically destroy a healthy prior version.
13. Recovery must remain bounded in memory, concurrency and retry rate.

---

## 39. Next specification dependency

The next critical specification is **TZ-17 — Testing & Verification**, but before full E2E implementation the remaining processing-plane specifications TZ-03 through TZ-07 must also be completed.

TZ-17 SHALL turn the golden failure scenarios in this document into executable evidence, especially:

```text
multi-replica claim/fencing
worker crash/reclaim
prepared artifact reuse
Start/Append/Finalize lost ACK
AstraVector readiness reconciliation
cancellation/delete races
DEAD_LETTER/requeue
end-to-end retrieve after recovery
```

TZ-14 should additionally formalize the logs/metrics/traces required to operate these recovery paths in production.
