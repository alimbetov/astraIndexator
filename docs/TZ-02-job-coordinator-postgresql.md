# TZ-02 — Job Coordinator & PostgreSQL

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-02
- **Title:** Job Coordinator & PostgreSQL
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Upstream contract:** `TZ-01-indexation-api-job-contract.md`
- **Related specifications:** TZ-03, TZ-09, TZ-10, TZ-11, TZ-12, TZ-13, TZ-14, TZ-17, TZ-18

---

## 2. Purpose

This specification defines the durable job coordinator used by Spring Boot producers and one or more AstraIndexator worker replicas.

It formalizes:

- PostgreSQL schema responsibilities;
- job lifecycle and state machine;
- atomic job claiming;
- lease ownership and renewal;
- fencing against stale workers;
- processing attempts;
- retry/backoff semantics;
- cancellation semantics;
- poison-job handling;
- concurrency rules for multiple replicas;
- crash recovery hooks;
- indexing and query requirements;
- transactional boundaries;
- downstream delivery checkpoints;
- operator/reconciliation queries;
- acceptance and verification criteria.

The design intentionally provides **at-least-once processing**, not distributed exactly-once execution. Correctness is achieved through deterministic identity, renewable leases, fencing, idempotent/reconcilable downstream effects and reconciliation.

---

## 3. Architectural baseline

Canonical control plane:

```text
Spring Boot producer
    |
    | INSERT PENDING
    v
PostgreSQL
    |
    +-------------------+-------------------+
    |                   |                   |
    v                   v                   v
Indexator-1        Indexator-2        Indexator-N
    |                   |                   |
    +------ atomic dynamic claim/lease -----+
                        |
                        v
                 processing attempt
                        |
                        v
                  downstream effects
```

PostgreSQL acts as:

1. the durable queue of accepted work;
2. the distributed coordination point for active workers;
3. the authoritative source of AstraIndexator job/attempt state;
4. the recovery source after worker crashes;
5. the checkpoint store for prepared-artifact and AstraVector delivery progress.

It MUST NOT be used to hold a transaction open for the full duration of parsing/OCR/indexation.

---

## 4. Core reliability model

AstraIndexator SHALL implement:

```text
at-least-once processing
+
idempotent/reconcilable side effects
+
deterministic identities
+
lease/fencing ownership
+
reconciliation
```

The system SHALL NOT claim exactly-once processing across PostgreSQL, SeaweedFS and AstraVector.

A repeated processing attempt is valid and expected after ambiguous failures.

Example:

```text
worker A -> AstraVector SUCCESS
worker A loses ACK / crashes
lease expires
worker B reclaims job
worker B reconciles/repeats downstream operation
```

This is correct only if the downstream operation is replayed according to TZ-11 idempotency/reconciliation rules.

---

## 5. Job identity and ownership

The coordinator SHALL preserve the identity model from TZ-01:

```text
documentId
!= documentVersion
!= jobId
!= processingAttemptId
```

`documentVersion` is a positive numeric value for direct AstraVector interoperability. If an upstream producer starts with an opaque revision string, the authoritative mapping to this numeric version is a separate persisted concern defined by TZ-01/TZ-11 and MUST NOT be derived ad hoc in worker memory.

### 5.1 Job ownership

A job is owned by at most one **current lease generation** at a time.

Ownership is represented by:

```text
worker_id
lease_generation
lease_acquired_at
lease_until
last_heartbeat_at
```

`worker_id` alone is insufficient as proof of ownership.

### 5.2 Fencing token

`lease_generation` SHALL be a monotonically increasing integer per job.

Every successful claim/reclaim increments it.

Any mutating operation that completes an attempt or advances authoritative job state MUST verify both:

```text
job_id
+
lease_generation
```

A stale worker from an earlier generation MUST NOT be able to complete or overwrite the state of the current owner.

---

## 6. Worker identity

Each active replica SHALL expose a unique, stable-for-process-lifetime `worker_id`.

Preferred Kubernetes baseline:

```text
worker_id = pod name / HOSTNAME
```

Example:

```text
astra-indexator-7d548c86b9-f7xvp
```

If multiple worker processes run inside one pod, the identifier SHALL additionally include a process/instance suffix.

Worker identity is an operational coordinate, not a business identity.

---

## 7. Job state model

### 7.1 Top-level status

Canonical top-level states:

```text
PENDING
PROCESSING
RETRY_WAIT
COMPLETED
FAILED
DEAD_LETTER
CANCELLED
```

`CLAIMED` SHALL NOT be required as a durable long-lived top-level state. Claim ownership is represented by lease fields and an attempt record. A short internal transition may use claim semantics, but the persistent state model SHOULD remain compact.

### 7.2 Processing stage

While `status = PROCESSING`, `processing_stage` provides diagnostic/progress detail.

Recommended baseline:

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

Exact stage names MAY evolve without changing top-level lifecycle semantics.

### 7.3 Terminal states

Terminal states:

```text
COMPLETED
FAILED
DEAD_LETTER
CANCELLED
```

Terminal jobs MUST NOT be claimable.

---

## 8. State-transition rules

Canonical transitions:

```text
PENDING
  -> PROCESSING

PROCESSING
  -> COMPLETED
  -> RETRY_WAIT
  -> FAILED
  -> DEAD_LETTER
  -> CANCELLED

RETRY_WAIT
  -> PROCESSING
  -> CANCELLED
  -> DEAD_LETTER

FAILED
  -> PENDING        (explicit operator/manual requeue only)

DEAD_LETTER
  -> PENDING        (explicit operator/manual requeue only)
```

Illegal transitions MUST be rejected at the persistence/service layer.

Automatic transition from terminal states back to active work is prohibited.

---

## 9. Recommended PostgreSQL schema

The exact DDL may evolve during implementation, but the following logical model is mandatory.

### 9.1 `indexation_job`

Recommended columns:

```sql
CREATE TABLE astra_indexator.indexation_job (
    job_id                  uuid PRIMARY KEY,

    document_id             text NOT NULL,
    document_version        bigint NOT NULL CHECK (document_version > 0),

    source_storage          text NOT NULL,
    source_bucket           text NULL,
    source_object_key       text NOT NULL,
    source_version_id       text NULL,
    source_etag             text NULL,

    original_file_name      text NULL,
    declared_content_type   text NULL,

    status                  text NOT NULL,
    processing_stage        text NULL,

    worker_id               text NULL,
    lease_generation        bigint NOT NULL DEFAULT 0,
    lease_acquired_at       timestamptz NULL,
    lease_until             timestamptz NULL,
    last_heartbeat_at       timestamptz NULL,

    attempt_count           integer NOT NULL DEFAULT 0,
    max_attempts            integer NOT NULL,
    next_retry_at           timestamptz NULL,

    priority                integer NOT NULL DEFAULT 0,

    cancel_requested        boolean NOT NULL DEFAULT false,
    cancel_requested_at     timestamptz NULL,

    last_error_class        text NULL,
    last_error_code         text NULL,
    last_error_message      text NULL,
    last_error_retryable    boolean NULL,

    prepared_manifest_ref   text NULL,
    prepared_schema_version text NULL,

    ingestion_mode          text NULL,
    ingestion_idempotency_key text NULL,
    ingestion_session_id    text NULL,
    next_batch_index        integer NULL,
    last_accepted_batch_index integer NULL,
    final_content_hash      text NULL,
    session_status_raw      text NULL,
    vector_state_raw        text NULL,
    searchable              boolean NULL,
    last_downstream_check_at timestamptz NULL,
    last_downstream_error_code text NULL,
    last_downstream_error_message text NULL,

    accepted_at             timestamptz NOT NULL,
    started_at              timestamptz NULL,
    completed_at            timestamptz NULL,
    failed_at               timestamptz NULL,
    updated_at              timestamptz NOT NULL,

    request_payload         jsonb NOT NULL,
    normalized_access       jsonb NULL,
    lifecycle_context       jsonb NULL,
    business_metadata       jsonb NULL,

    row_version             bigint NOT NULL DEFAULT 0
);
```

`request_payload` is the immutable accepted producer intent. Mutable execution/downstream state SHALL use dedicated columns/tables and MUST NOT rewrite accepted request semantics.

`document_version bigint` intentionally matches the positive numeric AstraVector public-facade version domain. Deployments that must preserve an upstream opaque revision string SHALL store that producer revision separately in immutable request/business identity data and use the authoritative mapping defined by TZ-01/TZ-11.

### 9.2 `indexation_attempt`

Each processing ownership period SHALL have an attempt record.

```sql
CREATE TABLE astra_indexator.indexation_attempt (
    processing_attempt_id   uuid PRIMARY KEY,
    job_id                  uuid NOT NULL REFERENCES astra_indexator.indexation_job(job_id),
    attempt_no              integer NOT NULL,
    lease_generation        bigint NOT NULL,
    worker_id               text NOT NULL,

    started_at              timestamptz NOT NULL,
    finished_at             timestamptz NULL,

    outcome                 text NULL,
    terminal_stage          text NULL,

    error_class             text NULL,
    error_code              text NULL,
    error_message           text NULL,
    retryable               boolean NULL,

    UNIQUE(job_id, attempt_no),
    UNIQUE(job_id, lease_generation)
);
```

Attempt history SHALL be append-oriented and SHOULD NOT be overwritten after completion.

### 9.3 AstraVector session/batch delivery ledger

The old conceptual `fragment_delivery/root_chunk_id` shape is not the canonical AstraVector v007 integration model. TZ-11 uses either a single-call facade request or, for large documents, a session with deterministic batches.

For session mode, AstraIndexator SHOULD persist batch-level delivery evidence such as:

```sql
CREATE TABLE astra_indexator.astravector_batch_delivery (
    job_id                  uuid NOT NULL REFERENCES astra_indexator.indexation_job(job_id),
    batch_index             integer NOT NULL,
    batch_content_hash      text NOT NULL,
    block_count             integer NOT NULL,
    serialized_bytes        bigint NOT NULL,

    status                  text NOT NULL,
    attempt_count           integer NOT NULL DEFAULT 0,
    accepted_at             timestamptz NULL,

    last_error_code         text NULL,
    last_error_message      text NULL,
    updated_at              timestamptz NOT NULL,

    PRIMARY KEY(job_id, batch_index),
    UNIQUE(job_id, batch_index, batch_content_hash)
);
```

The job-level fields hold session identity/current progress; the batch ledger proves deterministic Append replay identity:

```text
ingestion_session_id
+
batch_index
+
batch_content_hash
```

A same `batch_index` with a different content hash is an integrity conflict and MUST NOT be treated as a harmless retry.

Single-call ingestion does not require artificial fragment-level rows merely to imitate the session path.

The byte-exact canonical hashing algorithm/golden vectors remain governed by TZ-11/TZ-17 and MUST NOT be guessed independently in this coordinator specification.

---

## 10. Producer insertion contract

Spring Boot SHALL create a new job only after its source object reference is established according to TZ-01.

Producer-owned insertion SHALL set at minimum:

```text
job_id
document_id
document_version
source_*
status = PENDING
attempt_count = 0
max_attempts
accepted_at
updated_at
request_payload
normalized_access/lifecycle context where contract requires
```

Producer SHALL NOT set runtime/downstream fields such as:

```text
worker_id
lease_generation
lease_until
processing_stage
processing attempt outcome
ingestion_session_id
last_accepted_batch_index
vector_state_raw
searchable
completed_at
```

Those fields are consumer-owned.

---

## 11. Atomic claim algorithm

Multiple replicas SHALL claim work transactionally using PostgreSQL row locking.

Recommended baseline:

```sql
BEGIN;

WITH candidates AS (
    SELECT job_id
    FROM astra_indexator.indexation_job
    WHERE
        (
            status = 'PENDING'
            OR (
                status = 'RETRY_WAIT'
                AND next_retry_at <= now()
            )
            OR (
                status = 'PROCESSING'
                AND lease_until < now()
            )
        )
        AND cancel_requested = false
    ORDER BY priority DESC, accepted_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT :claim_limit
)
UPDATE astra_indexator.indexation_job j
SET
    status = 'PROCESSING',
    worker_id = :worker_id,
    lease_generation = j.lease_generation + 1,
    lease_acquired_at = now(),
    lease_until = now() + :lease_duration,
    last_heartbeat_at = now(),
    attempt_count = j.attempt_count + 1,
    started_at = COALESCE(j.started_at, now()),
    next_retry_at = NULL,
    updated_at = now(),
    row_version = j.row_version + 1
FROM candidates c
WHERE j.job_id = c.job_id
RETURNING j.*;

COMMIT;
```

The claim transaction MUST remain short.

No file download, parser execution, OCR or downstream network operation may occur while row locks from the claim transaction are held.

---

## 12. Claim eligibility

A job is claimable if all conditions hold:

1. it is not terminal;
2. cancellation has not been requested before claim;
3. attempts remain according to retry policy;
4. one of the following is true:
   - `PENDING`;
   - `RETRY_WAIT` and `next_retry_at <= now()`;
   - `PROCESSING` with an expired lease and recovery policy permits reclaim.

A worker MUST NOT claim a job whose active lease has not expired.

---

## 13. Lease semantics

### 13.1 Purpose

The lease converts process liveness into recoverable ownership.

### 13.2 Duration

Lease duration SHALL be configurable.

It MUST be longer than the expected heartbeat interval and tolerant of temporary scheduler/GC/runtime pauses.

Recommended relationship:

```text
heartbeat_interval <= lease_duration / 3
```

Example baseline:

```text
heartbeat_interval = 20s
lease_duration      = 90s
```

These are operational defaults, not hard-coded protocol constants.

### 13.3 Renewal

Only the current owner may renew:

```sql
UPDATE astra_indexator.indexation_job
SET
    lease_until = now() + :lease_duration,
    last_heartbeat_at = now(),
    updated_at = now(),
    row_version = row_version + 1
WHERE job_id = :job_id
  AND worker_id = :worker_id
  AND lease_generation = :lease_generation
  AND status = 'PROCESSING'
  AND lease_until >= now() - :renewal_grace;
```

If zero rows are updated, the worker SHALL treat ownership as lost.

### 13.4 Ownership loss

After ownership loss, a worker MUST stop authoritative state mutations for that job.

It MAY finish local cleanup, but MUST NOT mark the job `COMPLETED`, `FAILED`, `RETRY_WAIT` or overwrite current attempt/downstream checkpoint state.

---

## 14. Fencing semantics

Lease expiry creates a split-brain risk:

```text
worker A pauses
lease expires
worker B reclaims generation 8
worker A resumes with generation 7
```

Therefore every authoritative completion/update MUST include the current `lease_generation` predicate.

Representative completion:

```sql
UPDATE astra_indexator.indexation_job
SET
    status = 'COMPLETED',
    processing_stage = 'FINALIZING_VECTOR_STATE',
    completed_at = now(),
    lease_until = NULL,
    worker_id = NULL,
    updated_at = now(),
    row_version = row_version + 1
WHERE job_id = :job_id
  AND lease_generation = :lease_generation
  AND worker_id = :worker_id
  AND status = 'PROCESSING'
  AND searchable = true;
```

Zero updated rows means the worker is fenced out or downstream searchability has not been proven. The worker SHALL NOT retry completion as if it still owned the job.

---

## 15. Heartbeat behavior during long stages

Heartbeat MUST be independent enough from stage execution that a long parser/OCR call does not accidentally expire ownership.

Implementation SHOULD use a lightweight coordinator task/thread/coroutine that renews the lease while a processing attempt is alive.

However, heartbeat MUST stop if:

- the worker is shutting down and cannot finish safely;
- database connectivity is lost beyond configured tolerance;
- the attempt is cancelled;
- the process no longer has confidence it owns the job.

Loss of PostgreSQL connectivity SHALL be treated conservatively: after the lease cannot be renewed, the worker must assume another replica may reclaim the job and MUST stop authoritative downstream mutations whose ownership cannot be proven.

---

## 16. Retry classification

Errors SHALL be classified consistently with TZ-13, including at least:

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

`DOWNSTREAM_AMBIGUOUS` means a side effect may have succeeded but acknowledgement is uncertain. Such outcomes SHALL reconcile/retry only through the deterministic TZ-11 contract.

Deterministic input/policy failures SHALL NOT be blindly retried.

---

## 17. Retry policy

Retryable failures SHALL transition to `RETRY_WAIT` with persisted `next_retry_at`.

Recommended baseline:

```text
exponential backoff
+
jitter
+
upper bound
```

Conceptual formula:

```text
delay = min(max_delay, base_delay * 2^(attempt_no - 1)) + jitter
```

Exact values are deployment configuration, not protocol constants.

After the retry budget is exhausted, policy SHALL move the job to `FAILED` or `DEAD_LETTER` according to the deterministic failure/dead-letter rules in TZ-13.

---

## 18. Retry transition

A retry transition MUST be fenced:

```sql
UPDATE astra_indexator.indexation_job
SET
    status = 'RETRY_WAIT',
    processing_stage = NULL,
    worker_id = NULL,
    lease_until = NULL,
    next_retry_at = :next_retry_at,
    last_error_class = :error_class,
    last_error_code = :error_code,
    last_error_message = :error_message,
    last_error_retryable = true,
    updated_at = now(),
    row_version = row_version + 1
WHERE job_id = :job_id
  AND worker_id = :worker_id
  AND lease_generation = :lease_generation
  AND status = 'PROCESSING';
```

Zero rows updated means ownership was lost and the worker MUST NOT overwrite the newer owner state.

---

## 19. Cancellation

Cancellation is cooperative and follows TZ-12 lifecycle rules.

A producer/operator cancellation request SHALL set:

```text
cancel_requested = true
cancel_requested_at = now()
```

without pretending an in-flight external operation instantly disappeared.

The current owner observes cancellation at safe boundaries. If an AstraVector session is active, the owner follows TZ-11/TZ-12 Abort/reconciliation rules. If downstream state is already FINALIZING/ambiguous, the worker reconciles before declaring a final local outcome.

---

## 20. Poison jobs and dead-letter

Jobs that repeatedly fail with the same deterministic defect SHALL not consume unbounded retries.

Dead-letter entry SHALL preserve:

```text
job identity
attempt history
last failure class/code/message
source/prepared references
processing fingerprint
AstraVector checkpoint/status evidence where available
```

Requeue from `DEAD_LETTER` is explicit and auditable according to TZ-13/TZ-14.

---

## 21. Processing attempts

A new `indexation_attempt` row is created for each successful claim generation.

Attempt lifecycle is append-oriented:

```text
claim/reclaim
  -> insert attempt(job_id, attempt_no, lease_generation, worker_id)
  -> process
  -> finish outcome if still authoritative
```

If a worker is fenced out before it can close the attempt row, reconciliation MAY close/annotate the stale attempt without rewriting historical identity.

---

## 22. Processing-stage checkpoint semantics

`processing_stage` is operational progress, not proof that every side effect before that label is durable.

Durable recovery proof comes from the specific authoritative checkpoint for the stage:

```text
source acquired         -> source identity/hash evidence
prepared artifact       -> validated manifest + fenced prepared_manifest_ref
session started         -> persisted ingestion_session_id
batch accepted          -> batch ledger accepted state/hash
finalize ambiguous      -> session status reconciliation
vector ready            -> GetDocumentVectorStatus evidence/searchable=true
```

Recovery SHALL use those durable checkpoints rather than infer correctness from a stage name alone.

---

## 23. Prepared artifact reuse

A reclaimed worker MAY reuse prepared artifacts only when TZ-03/TZ-09 compatibility checks succeed, including source/content identity, schema/processing fingerprint and manifest part/hash validation.

If any required compatibility check fails:

- the prepared artifact SHALL NOT be trusted for replay;
- the worker SHALL regenerate from the earliest safe durable source stage if the immutable source is still valid;
- the incompatible/corrupt artifact remains diagnostic/orphan data until housekeeping policy removes it safely;
- the job is not failed solely because a regenerable prepared artifact is unusable, unless the source itself is unavailable/corrupt or policy forbids regeneration.

---

## 24. Downstream mutation ownership

Before starting a new authoritative AstraVector mutation, a worker MUST still have confidence in current lease ownership.

If PostgreSQL connectivity is lost such that ownership cannot be proven/renewed, the worker SHALL stop initiating new authoritative downstream mutations.

If an already-started network call returns after ownership is lost, the result is observational evidence only for the stale worker. The current owner/reconciliation path determines authoritative next action.

---

## 25. AstraVector single/session checkpoint semantics

TZ-11 is normative.

### Single-call

Persist deterministic idempotency identity and response/status evidence. A successful mutation response is not automatically equivalent to `searchable=true` unless the downstream state contract guarantees it.

### Session

Persist at minimum:

```text
ingestion_session_id
next_batch_index
last_accepted_batch_index
session_status_raw
final_content_hash
last_downstream_check_at
```

and batch-level `batch_index + batch_content_hash` evidence.

Lost ACK handling:

```text
Start timeout    -> retry/reconcile with same idempotency key
Append timeout   -> retry only same batch_index + same batch_content_hash
Finalize timeout -> GetLogicalDocumentIngestionStatus before unsafe recreation
```

---

## 26. Completion rule

AstraIndexator SHALL set local `COMPLETED` only when:

1. current lease/fencing ownership is still valid for the final local mutation;
2. required prepared/delivery state is durable;
3. AstraVector `GetDocumentVectorStatus` or an equivalent stabilized public contract proves the target document/version is `searchable=true` according to TZ-11/TZ-12.

`FinalizeLogicalDocumentIngestion` transport success alone is insufficient.

---

## 27. Indexing and constraints

Implementation SHALL create indexes supporting at least:

```text
claimable jobs by status/next_retry_at/priority/accepted_at
expired PROCESSING leases
job attempts by job_id/attempt_no
batch deliveries by job_id/batch_index/status
operator filtering by document_id/document_version/status
reconciliation candidates by downstream/session state and last_downstream_check_at
```

Recommended integrity constraints include:

```text
document_version > 0
lease_generation >= 0
attempt_count >= 0
max_attempts > 0
next_batch_index >= 0 when not null
last_accepted_batch_index >= 0 when not null
```

Status/stage enumerations SHOULD be constrained through CHECK/domain/enums or application+persistence validation so illegal durable states cannot be silently written.

---

## 28. Transaction isolation assumptions

The claim algorithm relies on PostgreSQL row locks under normal `READ COMMITTED` semantics unless implementation evidence requires stronger isolation for a specific operation.

Long-running parser/OCR/network operations MUST execute outside the claim transaction.

No requirement exists for global SERIALIZABLE transactions across the pipeline.

---

## 29. Operator and reconciliation queries

Operators/reconciliation workers must be able to identify at least:

```text
PENDING backlog and oldest age
RETRY_WAIT due jobs
PROCESSING jobs with expired leases
jobs near/exceeding max attempts
DEAD_LETTER jobs
cancel_requested jobs
jobs with prepared_manifest_ref but incomplete downstream delivery
active/stuck ingestion sessions
local COMPLETED but searchable != true inconsistencies
stale downstream checks
failed/pending batch deliveries
```

These views feed TZ-14 Knowledge Inventory/observability and TZ-13 reconciliation.

---

## 30. Data retention

Job, attempt and delivery-checkpoint retention is operational/audit state and is independent from source/prepared/vector TTL.

Deleting/expiring vectors in AstraVector MUST NOT implicitly delete coordinator history.

Retention policy and housekeeping belong to TZ-14/TZ-18.

---

## 31. Multi-replica invariants

For N active replicas:

1. two workers may examine the queue concurrently;
2. row locking prevents simultaneous claim of the same row in one generation;
3. reclaim increments `lease_generation`;
4. stale generations are fenced from authoritative local updates;
5. downstream ambiguous side effects are reconciled through deterministic identities;
6. increasing/decreasing replicas does not change document/job identity;
7. pod death does not destroy durable job/prepared/session checkpoint state.

---

## 32. Failure scenarios required for TZ-17

Executable evidence SHALL cover at least:

```text
concurrent claim race
worker crash before/after claim
lease expiry and reclaim
stale worker resumes after reclaim
DB loss during heartbeat
DB loss after downstream mutation
prepared artifact compatible reuse
prepared artifact corruption/regeneration
Start lost ACK
Append lost ACK
same batch index + different hash conflict
Finalize lost ACK
cancellation during active/finalizing session
retry exhaustion / dead-letter / auditable requeue
local completion blocked while searchable=false
```

---

## 33. Acceptance criteria

TZ-02 is accepted when:

1. top-level status matches TZ-00/TZ-01 and does not require durable `CLAIMED`;
2. `document_version` is a positive numeric value compatible with AstraVector public ingestion;
3. atomic claim uses short `FOR UPDATE SKIP LOCKED` transactions;
4. every claim/reclaim increments a monotonic fencing generation;
5. authoritative worker updates are fenced by current job/worker/generation;
6. processing attempts are append-oriented and auditable;
7. retry/cancellation/dead-letter transitions are finite and explicit;
8. prepared reuse is based on durable compatibility evidence, not stage labels;
9. large-document downstream progress is checkpointed by AstraVector session/batch semantics rather than obsolete fragment/root-chunk assumptions;
10. ambiguous downstream ACKs reconcile before unsafe replay/recreation;
11. local `COMPLETED` requires downstream `searchable=true` proof;
12. PostgreSQL loss prevents unsafe new authoritative mutations when ownership cannot be proven;
13. multi-replica crash/reclaim/stale-worker scenarios are executable TZ-17 evidence.
