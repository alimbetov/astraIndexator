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
- operator/reconciliation queries;
- acceptance and verification criteria.

The design intentionally provides **at-least-once processing**, not distributed exactly-once execution. Correctness is achieved through deterministic identity, renewable leases, fencing, idempotent downstream effects and reconciliation.

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
3. the authoritative source of job/attempt state;
4. the recovery source after worker crashes.

It MUST NOT be used to hold a transaction open for the full duration of parsing/OCR/indexation.

---

## 4. Core reliability model

AstraIndexator SHALL implement:

```text
at-least-once processing
+
idempotent side effects
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
worker B repeats downstream operation
```

This is correct only if the downstream operation is idempotent according to TZ-11.

---

## 5. Job identity and ownership

The coordinator SHALL preserve the identity model from TZ-01:

```text
documentId
!= documentVersion
!= jobId
!= processingAttemptId
```

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
REGISTERING_DOCUMENT
DELIVERING_FRAGMENTS
ACTIVATING_DOCUMENT
FINALIZING
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
    document_version        text NOT NULL,

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

`request_payload` is the immutable accepted producer intent. Mutable execution state SHALL use dedicated columns/tables and MUST NOT rewrite accepted request semantics.

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

### 9.3 Delivery ledger

TZ-02 requires a durable delivery/checkpoint concept for large documents. The exact AstraVector protocol belongs to TZ-11, but the persistence surface SHOULD support a table such as:

```sql
CREATE TABLE astra_indexator.fragment_delivery (
    job_id                  uuid NOT NULL,
    fragment_id             text NOT NULL,
    document_id             text NOT NULL,
    document_version        text NOT NULL,

    idempotency_key         text NOT NULL,
    status                  text NOT NULL,
    attempt_count           integer NOT NULL DEFAULT 0,

    root_chunk_id           text NULL,
    last_error_code         text NULL,
    last_error_message      text NULL,

    delivered_at            timestamptz NULL,
    updated_at              timestamptz NOT NULL,

    PRIMARY KEY(job_id, fragment_id),
    UNIQUE(idempotency_key)
);
```

This table MAY be introduced physically in TZ-11, but TZ-02 reserves the coordinator contract for persisted fragment-level checkpoints.

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

Producer SHALL NOT set:

```text
worker_id
lease_generation
lease_until
processing_stage
attempt outcome
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

It MAY finish local cleanup, but MUST NOT mark the job `COMPLETED`, `FAILED`, `RETRY_WAIT` or overwrite current attempt state.

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
    processing_stage = 'FINALIZING',
    completed_at = now(),
    lease_until = NULL,
    worker_id = NULL,
    updated_at = now(),
    row_version = row_version + 1
WHERE job_id = :job_id
  AND lease_generation = :lease_generation
  AND worker_id = :worker_id
  AND status = 'PROCESSING';
```

Zero updated rows means the worker is fenced out and SHALL NOT retry completion as if it still owned the job.

---

## 15. Heartbeat behavior during long stages

Heartbeat MUST be independent enough from stage execution that a long parser/OCR call does not accidentally expire ownership.

Implementation SHOULD use a lightweight coordinator task/thread/coroutine that renews the lease while a processing attempt is alive.

However, heartbeat MUST stop if:

- the worker is shutting down and cannot finish safely;
- database connectivity is lost beyond configured tolerance;
- the attempt is cancelled;
- the process no longer has confidence it owns the job.

Loss of PostgreSQL connectivity SHALL be treated conservatively: after the lease cannot be renewed, the worker must assume another replica may reclaim the job.

---

## 16. Retry classification

Errors SHALL be classified as at least:

```text
TRANSIENT
PERMANENT
CANCELLED
RESOURCE_LIMIT
DOWNSTREAM_AMBIGUOUS
```

Representative transient errors:

- temporary PostgreSQL/network failure after claim;
- SeaweedFS timeout;
- AstraVector unavailable;
- Nexus transient download failure;
- retryable OCR runtime failure.

Representative permanent errors:

- unsupported/corrupt file after validated acquisition;
- invalid immutable job contract;
- parser rejects malformed format where retries cannot help;
- security policy violation;
- deterministic downstream contract rejection.

`DOWNSTREAM_AMBIGUOUS` means a side effect may have succeeded but acknowledgement is uncertain. Such errors SHALL retry only through an idempotent downstream contract.

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

Example defaults:

```text
base_delay = 5s
max_delay  = 15m
max_attempts = configurable, baseline 8
```

Exact defaults belong to configuration TZ-15/TZ-18, but the persisted model MUST support them.

Retries MUST NOT reset accepted TTL semantics from TZ-10.

---

## 18. Attempt accounting

`attempt_count` SHALL count claimed processing attempts, not individual internal operations.

If one processing attempt retries an HTTP/gRPC call internally within a bounded local retry policy, that does not necessarily increment job `attempt_count`.

However, once the job transitions back to `RETRY_WAIT` and is reclaimed later, a new attempt is counted.

This distinction keeps job-level attempt history meaningful.

---

## 19. Exhausted attempts and dead-letter behavior

When retryable work exceeds `max_attempts`, the job SHALL transition to `DEAD_LETTER` unless an explicit policy maps the failure to `FAILED`.

Recommended distinction:

```text
FAILED      = deterministic/permanent processing failure
DEAD_LETTER = retryable/ambiguous work exhausted its retry budget or was quarantined as poison
```

Both are terminal until explicit administrative action.

The full dead-letter operational workflow belongs to TZ-13.

---

## 20. Poison-job detection

A poison job is a job that repeatedly fails in a deterministic or resource-destructive way.

Detection signals MAY include:

- same normalized error code across multiple attempts;
- repeated worker crash/OOM during the same stage;
- repeated hard resource-limit violation;
- parser/OCR deterministic failure signature.

The coordinator SHALL allow policy-driven early transition to `DEAD_LETTER` rather than exhausting all retries when repeated execution is demonstrably unsafe/useless.

Automatic poison classification MUST be evidence-based and observable.

---

## 21. Cancellation

Cancellation SHALL use an intent flag rather than blind state overwrite.

Producer/operator requests:

```text
cancel_requested = true
cancel_requested_at = now()
```

### 21.1 Pending/retry-wait job

If no active worker owns the job, coordinator MAY atomically transition directly to `CANCELLED`.

### 21.2 Processing job

The current worker SHOULD observe `cancel_requested` during heartbeat/stage boundaries and perform cooperative cancellation.

After safe cleanup it SHALL transition to `CANCELLED` using the same fencing predicates as any other terminal transition.

Cancellation MUST NOT cause a stale worker to overwrite a newer generation.

### 21.3 Downstream side effects

If cancellation occurs after partial AstraVector delivery, cleanup/visibility semantics belong to TZ-11/TZ-12/TZ-13. Coordinator cancellation alone does not imply that all downstream side effects were rolled back.

---

## 22. Stage checkpointing

`processing_stage` is for durable progress/diagnostics, but stage changes SHALL NOT imply exactly-once stage execution.

After crash, the next attempt MAY:

- replay a stage idempotently;
- resume from a prepared artifact checkpoint;
- resume fragment delivery using the delivery ledger;
- restart from source if checkpoints are invalid.

The choice depends on TZ-03/TZ-09/TZ-11/TZ-13.

A stage marker is not by itself proof that every side effect in the stage completed.

---

## 23. Prepared artifact recovery hook

When canonical prepared artifacts exist, the job MAY persist:

```text
prepared_manifest_ref
prepared_schema_version
```

A reclaimed attempt may use them only if:

- manifest is readable;
- content hash/integrity is valid;
- schema version is supported;
- source/document identity matches;
- relevant parser/OCR/normalizer/splitter versions are compatible with the intended replay contract.

Otherwise the job falls back to earlier stages.

This allows recovery without re-running expensive parsing/OCR when safe.

---

## 24. Fragment delivery checkpointing

Large documents SHALL NOT require all fragments to be redelivered after one failed fragment or worker restart.

Coordinator persistence SHALL support fragment-level status such as:

```text
PENDING
IN_FLIGHT
DELIVERED
RETRY_WAIT
FAILED
```

The exact transition protocol is TZ-11, but core requirements are:

- stable `fragmentId`;
- deterministic idempotency key;
- bounded in-flight concurrency;
- persisted delivery acknowledgement (`root_chunk_id` when available);
- retry only for incomplete/ambiguous fragments;
- ability to calculate expected vs delivered fragment counts before activation.

---

## 25. Completion invariant

A job SHALL transition to `COMPLETED` only after all mandatory conditions are proven.

At minimum, when AstraVector integration is enabled:

```text
canonical preparation complete
+
all required LogicalFragments delivered/acknowledged
+
document version activation succeeded
+
required durable coordinator state persisted
```

`COMPLETED` MUST NOT be set merely because local parsing/splitting finished.

This protects retrieval from partially indexed documents.

---

## 26. Partial indexing invariant

AstraIndexator SHALL treat document indexation as a build-then-activate workflow.

Conceptually:

```text
RegisterDocumentVersion
        ↓
BUILDING
        ↓
fragment 1 ... fragment N delivery
        ↓
verify expected == delivered
        ↓
ActivateDocumentVersion
        ↓
ACTIVE
        ↓
job COMPLETED
```

If a worker crashes after some fragments are delivered, the next attempt resumes delivery from persisted checkpoints and MUST NOT expose a partial version as successfully completed.

---

## 27. Scheduling fairness and priority

Claim ordering SHALL be deterministic enough for operations.

Baseline:

```text
priority DESC,
accepted_at ASC
```

Priority values SHOULD be bounded to prevent arbitrary producer starvation of normal work.

Future fairness controls MAY include per-source-system quotas or aging, but the 1.0 coordinator MUST at minimum prevent one extremely old/retrying job from globally locking the queue.

`SKIP LOCKED` combined with short claim transactions prevents head-of-line blocking at row-lock level.

---

## 28. Claim batch size and worker concurrency

`claim_limit` SHALL be configurable and MUST be coordinated with local worker capacity.

A worker SHOULD NOT claim substantially more jobs than it can actively heartbeat/process.

Recommended rule:

```text
claim_limit <= available local job slots
```

Local resource controls are stage-specific and belong to TZ-18, but TZ-02 requires bounded active job ownership.

Example:

```text
MAX_ACTIVE_JOBS=4
```

A worker must not lease 100 jobs while only being able to execute 4, because that causes unfairness and avoidable lease churn.

---

## 29. PostgreSQL indexes

Minimum useful indexes SHOULD include:

```sql
CREATE INDEX ix_job_claim_pending
ON astra_indexator.indexation_job (priority DESC, accepted_at ASC)
WHERE status = 'PENDING' AND cancel_requested = false;

CREATE INDEX ix_job_retry_ready
ON astra_indexator.indexation_job (next_retry_at, priority DESC, accepted_at ASC)
WHERE status = 'RETRY_WAIT' AND cancel_requested = false;

CREATE INDEX ix_job_expired_lease
ON astra_indexator.indexation_job (lease_until)
WHERE status = 'PROCESSING';

CREATE INDEX ix_job_document
ON astra_indexator.indexation_job (document_id, document_version);

CREATE INDEX ix_job_worker
ON astra_indexator.indexation_job (worker_id)
WHERE status = 'PROCESSING';

CREATE INDEX ix_attempt_job
ON astra_indexator.indexation_attempt (job_id, attempt_no);
```

Final index selection MUST be validated using representative queue cardinality and `EXPLAIN (ANALYZE, BUFFERS)`.

---

## 30. Database constraints

Implementation SHOULD use CHECK constraints or equivalent application/DB enforcement for invariants such as:

```text
attempt_count >= 0
max_attempts > 0
priority within configured range
COMPLETED => completed_at IS NOT NULL
PROCESSING => worker_id IS NOT NULL
PROCESSING => lease_until IS NOT NULL
non-PROCESSING terminal state => active lease fields cleared
RETRY_WAIT => next_retry_at IS NOT NULL
```

PostgreSQL constraints are preferred for invariants that protect data integrity independently of application bugs.

---

## 31. Transaction boundaries

Transactions SHALL be short and limited to coordinator state changes.

Good transaction examples:

- insert accepted job;
- claim/reclaim N jobs;
- renew heartbeat;
- persist retry outcome;
- persist fragment-delivery acknowledgement;
- finalize job.

Bad transaction:

```text
BEGIN
claim job
Download 2 GB PDF
OCR 500 pages
call AstraVector
COMMIT
```

Long business processing MUST occur outside database transactions.

---

## 32. Isolation level

`READ COMMITTED` is expected to be sufficient for the baseline claim algorithm when using `FOR UPDATE SKIP LOCKED` and guarded updates.

The implementation MUST NOT rely on implicit serial execution between workers.

If a stronger isolation level is introduced, its deadlock/retry impact must be explicitly proven.

---

## 33. Clock semantics

Lease/retry decisions SHOULD use PostgreSQL `now()` as the authoritative coordinator clock rather than worker-local timestamps.

This reduces cross-node clock-skew effects.

Application logs may use local UTC time, but ownership expiry decisions SHALL be based on database time.

---

## 34. Database outage behavior

If PostgreSQL is unavailable:

- workers cannot safely claim new jobs;
- workers cannot safely renew leases;
- workers SHOULD stop acquiring new side-effectful work;
- an active worker may finish local computation only if it understands that ownership may be lost before it can persist results;
- once lease validity cannot be confirmed, worker SHALL assume it can be fenced out;
- downstream side effects after uncertain lease ownership SHOULD be minimized and must be idempotent if performed.

After database recovery, expired jobs become reclaimable.

---

## 35. Graceful shutdown

On shutdown a worker SHALL:

1. stop claiming new work;
2. signal active attempts to reach safe cancellation/checkpoint boundaries;
3. continue heartbeat only while it intends and is able to finish safely;
4. persist outcomes where possible;
5. avoid artificially extending leases for abandoned work.

If graceful completion is impossible, allowing the lease to expire is acceptable; recovery then occurs through another replica.

---

## 36. Race-condition scenarios that MUST be tested

### 36.1 Two workers claim same PENDING job

Expected: exactly one receives ownership for a given lease generation.

### 36.2 Worker pauses beyond lease

Worker B reclaims with generation N+1. Worker A resumes and attempts completion.

Expected: worker A updates zero authoritative rows and is fenced out.

### 36.3 ACK lost after AstraVector success

Expected: job/fragment retries through deterministic idempotency; no duplicate logical downstream data.

### 36.4 Worker crashes after prepared artifacts

Expected: next worker can validate and reuse prepared artifacts when compatible.

### 36.5 Worker crashes during fragment delivery

Expected: delivered fragments remain checkpointed; next worker resumes incomplete set.

### 36.6 Cancellation races with claim

Expected: either cancellation wins before claim, or current worker sees cancel intent and cooperatively cancels. No future normal claim after terminal CANCELLED.

### 36.7 Retry becomes ready while another worker scans queue

Expected: normal `SKIP LOCKED`/predicate behavior; no duplicate active owner.

### 36.8 PostgreSQL outage during heartbeat

Expected: worker eventually treats ownership as uncertain/lost and does not commit stale terminal state.

---

## 37. Operator/reconciliation queries

Operations SHALL be able to identify at least:

- oldest PENDING jobs;
- RETRY_WAIT jobs ready/overdue;
- PROCESSING jobs with expired leases;
- active jobs by worker;
- jobs close to/exceeding max attempts;
- FAILED/DEAD_LETTER counts by error code/stage;
- long-running jobs by stage;
- jobs with prepared artifacts but incomplete delivery;
- jobs where expected fragment count differs from delivered count.

These queries form inputs to TZ-13 reconciliation and TZ-14 observability.

---

## 38. Metrics required from the coordinator

TZ-14 owns the full metrics specification, but TZ-02 requires at least the semantic availability of:

```text
jobs_pending
jobs_processing
jobs_retry_wait
jobs_failed
jobs_dead_letter
claim_rate
claim_conflict_or_empty_rate
lease_renew_success
lease_renew_failure
expired_lease_reclaims
attempts_per_job
retry_rate
job_age_seconds
processing_duration_seconds
stage_duration_seconds
```

High-cardinality identifiers such as `jobId` or `documentId` MUST NOT be metric labels.

---

## 39. Error persistence

Persisted error data SHALL be bounded.

The coordinator SHOULD store:

```text
error_class
error_code
sanitized message
retryable flag
stage
attempt id
```

Full stack traces SHOULD go to structured logs/tracing, not unbounded database columns.

Secrets, credentials and raw sensitive document content MUST NOT be written into error messages.

---

## 40. Schema migration requirements

PostgreSQL schema SHALL be migration-controlled.

Requirements:

- forward migrations are versioned;
- rolling deployments MUST define compatibility expectations;
- nullable/additive changes are preferred during rolling upgrade windows;
- destructive enum/status changes require explicit migration strategy;
- indexes that may lock large production tables SHOULD use an operationally safe migration method where applicable.

Exact migration tooling belongs to implementation/deployment design.

---

## 41. Multi-replica invariant

AstraIndexator SHALL support scaling from one to N active replicas without changing business semantics.

Correctness MUST NOT depend on:

- sticky assignment of documents to workers;
- in-memory exclusive locks;
- local filesystem state as the only checkpoint;
- a leader-only worker for normal job processing.

PostgreSQL durable state is the shared coordination authority.

---

## 42. Security/access/lifecycle boundary

TZ-02 persists normalized access/lifecycle context but SHALL NOT invent access semantics.

Specifically:

- access-zone normalization/meaning belongs to TZ-10;
- TTL/expiresAt semantics belong to TZ-10;
- retries MUST preserve original accepted lifecycle intent;
- coordinator state changes MUST NOT reset TTL clocks;
- access/lifecycle data MUST remain available to all later attempts.

---

## 43. Downstream integration boundary

TZ-02 does not define AstraVector protobuf field mapping, but it requires TZ-11 to provide:

- deterministic idempotency keys;
- safe repeat after ambiguous ACK;
- persisted fragment-level delivery checkpoints;
- document registration/build/activation semantics;
- final activation before job completion;
- reconciliation APIs/queries sufficient to determine downstream truth.

If TZ-11 cannot satisfy these, `COMPLETED` semantics in this specification must not be weakened silently.

---

## 44. Acceptance criteria

TZ-02 implementation is accepted only when all mandatory criteria pass.

### AC-01 Multi-replica exclusive claim

With at least 3 concurrent workers and a shared queue, no job has more than one current lease generation owner.

### AC-02 Claim scalability

Concurrent claim uses `FOR UPDATE SKIP LOCKED` (or an explicitly proven equivalent) and does not serialize all workers behind the first locked row.

### AC-03 Short transaction proof

No claim transaction includes external I/O or long processing.

### AC-04 Lease renewal

A healthy worker renews ownership before expiry and retains the same generation.

### AC-05 Expired lease reclaim

A crashed/stalled worker's job is reclaimable after lease expiry with incremented generation and new attempt record.

### AC-06 Stale worker fencing

A previous-generation worker cannot mark a reclaimed job complete/fail/retry/cancel.

### AC-07 Attempt history

Every claim/reclaim creates a unique attempt history record tied to worker and lease generation.

### AC-08 Retry persistence

Retryable errors persist classification, next retry time and preserve the original accepted lifecycle/access contract.

### AC-09 Backoff

Repeated retryable failures follow bounded backoff with jitter and do not form an uncontrolled tight retry loop.

### AC-10 Permanent failure

Non-retryable deterministic errors become terminal without useless automatic retries.

### AC-11 Dead-letter exhaustion

Retry budget exhaustion produces a terminal dead-letter/quarantine result that is operator-visible.

### AC-12 Cooperative cancellation

Cancellation is race-safe for PENDING, RETRY_WAIT and PROCESSING jobs.

### AC-13 Database outage safety

Loss of PostgreSQL heartbeat capability cannot allow stale workers to later overwrite current ownership state.

### AC-14 Prepared artifact recovery

A worker crash after canonical artifact creation can resume from validated compatible prepared artifacts without mandatory reparsing/OCR.

### AC-15 Fragment checkpoint recovery

A crash after partial fragment delivery does not require successful fragments to be logically duplicated or blindly redelivered.

### AC-16 Ambiguous downstream ACK

An operation that may have succeeded downstream is safely repeatable using deterministic idempotency semantics from TZ-11.

### AC-17 Partial version protection

Job cannot become `COMPLETED` until all mandatory fragments are acknowledged and document activation succeeds.

### AC-18 Queue indexing

Representative claim/retry/expired-lease queries are verified with `EXPLAIN (ANALYZE, BUFFERS)` against production-like row counts.

### AC-19 Bounded ownership

A worker does not lease materially more jobs than its configured active job capacity.

### AC-20 Clock consistency

Lease and retry eligibility are calculated from PostgreSQL time rather than depending on synchronized worker clocks.

### AC-21 State-transition validation

Illegal state transitions are rejected and covered by tests.

### AC-22 Terminal immutability

Terminal jobs cannot return to active processing without explicit administrative requeue semantics.

### AC-23 Recovery golden scenario

The following E2E scenario passes:

```text
producer creates PENDING job
-> one of N workers claims
-> worker processes and creates checkpoints
-> worker is killed at an injected failure point
-> lease expires
-> another worker reclaims
-> already durable work is reused/reconciled
-> downstream effects are safely retried
-> all fragments become acknowledged
-> document version activates
-> job becomes COMPLETED
```

### AC-24 No exactly-once false claim

Documentation and implementation consistently state at-least-once processing plus idempotency/fencing/reconciliation rather than claiming distributed exactly-once execution.

---

## 45. Required verification evidence

Implementation SHALL provide automated evidence including:

- PostgreSQL integration tests using real PostgreSQL/Testcontainers or equivalent;
- concurrent claim stress test with at least 3 workers;
- lease expiry/reclaim test;
- stale-generation fencing test;
- retry/backoff test with deterministic clock control where practical;
- cancellation race tests;
- worker-kill recovery tests;
- database outage/heartbeat-loss test;
- prepared-artifact resume test;
- fragment-delivery resume test;
- downstream ambiguous-ACK/idempotency test;
- state-transition property/table tests;
- query-plan evidence for queue indexes;
- E2E golden failure-recovery scenario.

---

## 46. Decisions deferred to child specifications

TZ-02 deliberately does not finalize:

- exact access-zone representation and fan-out: TZ-10;
- AstraVector protobuf mapping and delivery protocol: TZ-11;
- document version replacement/delete semantics: TZ-12;
- reconciliation/dead-letter operator procedures: TZ-13;
- full metrics/log/tracing schema: TZ-14;
- configuration values and deployment sizing: TZ-15/TZ-18;
- end-to-end test matrix and performance gates: TZ-17.

---

## 47. Final coordinator invariant

The AstraIndexator control plane SHALL remain correct under concurrent replicas, retries, worker crashes, delayed acknowledgements and temporary dependency failures.

The coordinator is considered correct when work may be replayed, but **authoritative ownership cannot be replayed by a stale worker**, and all externally visible side effects are either idempotent or reconcilable.
