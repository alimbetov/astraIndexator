# M1 — Persistence Foundation

## Status

Implementation baseline for AstraIndexator 1.0 database-first foundation.

## Scope

M1 implements persistence contracts from TZ-01, TZ-02, TZ-10, TZ-11, TZ-12, TZ-14 and verification requirements from TZ-17.

M1 intentionally does **not** implement claim/lease/heartbeat/fencing orchestration; those are M2 responsibilities.

## Technology

```text
Python 3.12+
SQLAlchemy 2.x
Alembic
psycopg 3
PostgreSQL 16
pytest + Testcontainers
```

## Durable Inbox decision

`astra_indexator.indexation_job` is the canonical durable Inbox/work queue. A separate generic `inbox_message` table is not used.

```text
producer_request_id != job_id
```

`producer_request_id UUID UNIQUE NOT NULL` provides producer-delivery idempotency. Repeating the same producer request returns the existing job instead of creating a second durable job.

## Initial schema

`0001_initial_schema` creates:

1. `indexation_job` — durable Inbox and authoritative job state;
2. `processing_attempt` — attempt/worker/lease-generation history;
3. `delivery_checkpoint` — AstraVector session/finalization/searchability checkpoint;
4. `delivery_batch` — deterministic session batch ledger;
5. `job_event` — append-oriented audit/state-transition evidence;
6. `knowledge_inventory` — operational projection of indexed knowledge.

## Canonical invariants

- `document_version > 0`;
- `access_zone_code` is `varchar(4)` and must match `^[0-9]{4}$`;
- leading zeroes are significant;
- canonical root codes are `0000,0100,0200,0300,0400,0500,0600,0700,0800,0900`;
- `requested_ttl_days` is null or non-negative; `0` remains downstream inherit semantics;
- top-level job status is one of `PENDING, PROCESSING, RETRY_WAIT, COMPLETED, FAILED, DEAD_LETTER, CANCELLED`;
- `lease_generation` and attempt counters cannot be negative;
- one producer request creates at most one durable job;
- one active `(access_zone_code, document_id, document_version)` exists at a time.

## Indexes

The initial schema includes dedicated indexes for:

- runnable job claim ordering;
- retry scheduling;
- expired lease recovery;
- document/version lookup;
- active document-version uniqueness;
- attempt history;
- job event history;
- knowledge inventory zone/searchability and expiry queries.

## Persistence API

`IndexationJobRepository.create_or_get()` implements durable Inbox idempotency using PostgreSQL `ON CONFLICT DO NOTHING` on `producer_request_id`.

Coordinator mutations are deliberately absent from M1.

## Verification

`tests/test_domain_contracts.py` proves:

- all ten canonical Access Zone roots;
- leading-zero preservation;
- malformed Access Zone rejection;
- positive numeric `documentVersion`.

`tests/test_persistence_postgres.py` uses PostgreSQL 16 Testcontainers and proves:

- clean DB -> `alembic upgrade head` creates all six foundational tables;
- duplicate `producer_request_id` returns the existing job;
- `0000` survives a PostgreSQL round trip unchanged;
- non-positive `documentVersion` is rejected by PostgreSQL;
- malformed Access Zone is rejected by PostgreSQL;
- duplicate active document version in the same zone is rejected.

## Definition of Done

M1 is complete when the test suite passes against a clean PostgreSQL 16 instance and Alembic can migrate from base to head and back to base.

The next milestone is M2 — Job Coordinator: `FOR UPDATE SKIP LOCKED`, lease acquisition/renewal, heartbeat, monotonic `lease_generation`, reclaim and fenced state transitions.
