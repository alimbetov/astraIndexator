# M2 — Job Coordinator

## Status

Implementation milestone based on TZ-02, TZ-13 and TZ-17.

## Goal

Implement safe multi-replica ownership of durable `indexation_job` rows using PostgreSQL as the coordination authority.

## Implemented invariants

```text
FOR UPDATE SKIP LOCKED
+
PostgreSQL now() lease authority
+
monotonic lease_generation
+
worker_id
+
processing_attempt
+
fencing CAS
+
at-least-once processing
```

A worker owns a job only while all of the following remain true:

```text
job_id matches
worker_id matches
lease_generation matches
status = PROCESSING
lease_until >= PostgreSQL now()
```

A lease that has expired is no longer authoritative even before another worker reclaims the job.

## Runnable job selection

`claim_next()` can select:

- `PENDING`;
- `RETRY_WAIT` when `next_retry_at <= now()` (or no retry timestamp exists);
- `PROCESSING` only when its lease has expired.

It excludes:

- terminal jobs;
- `cancel_requested = true` jobs;
- jobs where `attempt_count >= max_attempts`.

Claim order is deterministic by:

```text
priority DESC
created_at ASC
id ASC
```

The selection uses:

```sql
SELECT ...
FOR UPDATE SKIP LOCKED
LIMIT 1
```

so multiple replicas can claim different jobs without a global coordinator mutex.

## Claim operation

Each successful claim/reclaim:

1. locks one runnable row;
2. increments `lease_generation`;
3. increments `attempt_count`;
4. assigns `worker_id`;
5. writes `lease_acquired_at`, `lease_until`, `last_heartbeat_at` using PostgreSQL time;
6. creates `processing_attempt`;
7. emits `JOB_CLAIMED` or `JOB_RECLAIMED` event.

When a previously processing job is reclaimed after lease expiry, the prior open attempt is completed with result `LEASE_EXPIRED`.

## Lease token

Application code receives an immutable token:

```text
LeaseToken {
    job_id
    worker_id
    lease_generation
    attempt_id
}
```

The token is required for authoritative worker mutations.

## Heartbeat

`heartbeat()` renews the current lease only when the fencing predicate still matches and the existing lease has not already expired.

A late heartbeat after lease expiry fails with `LeaseLostError`; it must not resurrect ownership.

## Fenced mutations

The following operations are fenced:

- heartbeat;
- processing-stage advancement;
- completion;
- retry scheduling.

A stale or expired worker receives `LeaseLostError` and must stop authoritative processing for that job.

## Retry

`schedule_retry()` transitions:

```text
PROCESSING -> RETRY_WAIT
```

and stores:

- `next_retry_at` based on PostgreSQL time;
- error code/message;
- finished processing attempt;
- `RETRY_SCHEDULED` event.

`RETRY_WAIT` is not claimable before `next_retry_at`.

## Completion

`complete()` transitions:

```text
PROCESSING -> COMPLETED
```

and clears active lease ownership.

M2 implements coordinator completion mechanics only. Higher milestones remain responsible for deciding when business completion is valid (for example, TZ-11/TZ-12 require AstraVector downstream searchability proof before indexing can be considered complete).

## Multi-replica proof

`tests/test_coordinator_postgres.py` uses PostgreSQL 16 through Testcontainers and verifies:

1. three replicas claim three distinct jobs concurrently;
2. three replicas racing for one job produce exactly one owner;
3. heartbeat renews the active lease;
4. an expired lease is reclaimed by a new worker;
5. reclaim increments `lease_generation`;
6. prior attempt is marked `LEASE_EXPIRED`;
7. stale worker heartbeat is rejected;
8. expired worker cannot complete even before reclaim;
9. stage and completion mutations are fenced;
10. `RETRY_WAIT` is unavailable before its due timestamp.

## Deliberately deferred

Not part of M2:

- actual parser/OCR/indexing work;
- SeaweedFS acquisition;
- AstraVector delivery;
- cancel/delete business lifecycle implementation;
- dead-letter operator workflows;
- worker runtime loop and shutdown orchestration;
- metrics/health endpoints.

Those remain in later milestones according to TZ-03..TZ-18.

## Definition of Done

M2 is complete when:

- all M1 tests remain green;
- all M2 PostgreSQL/Testcontainers tests pass;
- concurrent claim has no duplicate current ownership;
- expired/stale workers cannot mutate authoritative job state;
- lease generation is monotonic across reclaim;
- CI passes on Python 3.12/PostgreSQL 16.
