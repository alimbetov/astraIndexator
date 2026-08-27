# AstraIndexator 1.0 — Implementation Roadmap 2.0

## 1. Status and purpose

**Status:** ACTIVE ROADMAP  
**Supersedes for forward planning:** `docs/IMPLEMENTATION-ROADMAP-1.0.md`  
**Historical contract freeze remains authoritative:** `docs/M8.0-CONTRACT-FREEZE-GAP-AUDIT.md`

Roadmap 2.0 reconciles the original implementation plan with the implementation and qualification evidence accumulated through M8.3.

Core rule remains unchanged:

```text
code exists != milestone complete
milestone complete = implementation + required verification evidence + green CI + reviewed merge
```

A milestone marked **QUALIFIED** must have executable evidence. A later milestone may not weaken an earlier qualified invariant.

---

## 2. Sources of truth and precedence

```text
1. AstraIndexator TZ-00..TZ-18                 normative system requirements
2. actual AstraVector wire contract in llm2    downstream wire authority
3. approved integration contract               producer/application mapping
4. qualified executable tests                  implementation evidence
5. this roadmap                                sequencing and release gates
```

When roadmap wording conflicts with a frozen TZ contract, the TZ contract wins and the roadmap must be corrected.

Generated AstraVector protobuf is transport-only. AstraIndexator must not maintain a parallel hand-written wire contract and must not access AstraVector PostgreSQL or Qdrant directly.

---

## 3. Frozen architectural invariants

1. PostgreSQL is the durable Inbox, scheduling, lease and recovery authority.
2. Processing is at-least-once; correctness relies on deterministic identity, idempotency, durable checkpoints, fencing and reconciliation.
3. PostgreSQL time is authoritative for lease expiry and retry scheduling.
4. Lease expiry immediately revokes mutation authority, even before another worker reclaims the job.
5. Every authoritative worker-side mutation is fenced by `job_id + worker_id + lease_generation + non-expired lease`.
6. Mutating downstream RPCs may start only when the remaining lease safely exceeds the RPC deadline plus safety margin.
7. `documentId`, `documentVersion`, `jobId`, `attemptId`, `fragmentId`, `ingestionSessionId` and AstraVector chunk identity are distinct.
8. One document version maps to one effective AccessZone.
9. `accessZoneCode` is exactly four ASCII digits; leading zeroes are significant.
10. Requested AccessZone identity is producer intent and remains distinct from AstraVector-resolved `access_zone_id`.
11. `ttl_days=0` means inherit AstraVector policy; it never means client-forced forever.
12. M7 prepared artifacts are the preferred restart boundary after parser/OCR/splitter work.
13. M8 recovery must not silently rerun parser/OCR when a compatible verified M7 artifact is available.
14. Append replay identity is `session_id + batch_index + batch_content_hash`.
15. A mutating RPC timeout is an ambiguous outcome, not proof of failure.
16. Ingestion session `COMPLETED` is not equivalent to document `SEARCHABLE`.
17. Local business completion requires authoritative AstraVector readiness evidence.
18. Unknown downstream states/errors fail closed unless explicitly classified by a qualified contract.

---

# 4. Implementation roadmap

## M0 — Project Foundation

**Status:** PARTIALLY DELIVERED / CONTINUOUS HARDENING

Foundation capabilities are introduced when first required rather than as an isolated big-bang phase.

Already present:

- Python package/build structure;
- SQLAlchemy/Alembic foundation;
- pytest/Testcontainers CI;
- Ruff and scoped mypy gates;
- typed configuration components.

Remaining production-facing foundation work is owned by M10/M12.

---

## M1 — Persistence Foundation

**Status:** ✅ QUALIFIED / MERGED

Durable PostgreSQL model established for jobs, attempts, delivery checkpoints/batches, events and knowledge inventory.

Key qualified invariants include producer request idempotency, positive immutable document version, AccessZone code preservation and PostgreSQL migration verification.

---

## M2 — Job Coordinator & PostgreSQL Ownership

**Status:** ✅ QUALIFIED / MERGED

Qualified capabilities:

- `FOR UPDATE SKIP LOCKED` claim;
- multi-replica ownership;
- lease heartbeat and expiry;
- monotonic `lease_generation` fencing;
- stale-worker rejection;
- PostgreSQL-time retry scheduling;
- durable processing attempts/events.

M2 supplies the ownership primitive consumed by all later worker stages.

---

## M3 — SeaweedFS Safe Acquisition

**Status:** ✅ IMPLEMENTED IN CURRENT PIPELINE

Key capabilities:

- approved SeaweedFS source boundary;
- immutable/bounded source acquisition;
- SHA-256/source evidence;
- attempt workspace safety;
- source mutation/partial acquisition protection.

**Roadmap 2.0 action:** include M3 in the later full traceability audit; do not reopen its architecture unless audit evidence reveals a gap.

---

## M4 — Canonical Parser

**Status:** ✅ IMPLEMENTED

Deterministic structural parsing exists for the qualified format baseline and extended formats.

**Remaining work:** release-level regression evidence belongs to M11, not a redesign of M4.

---

## M5 — OCR Pipeline

**Status:** ✅ IMPLEMENTED / HARDENED

Includes OCR policy, offline model/runtime controls, multilingual verification and PP-OCRv5 qualification assets.

**Remaining work:** real production throughput and deployment/resource evidence belongs to M11/M12.

---

## M6 — Normalization & Logical Splitter

**Status:** ✅ IMPLEMENTED / HARDENED

Produces deterministic semantic fragments while preserving AstraVector ownership of tokenizer-aware searchable chunking and embedding.

---

## M7 — Prepared Artifacts & Replay

**Status:** ✅ QUALIFIED

M7 is the canonical expensive-processing restart boundary.

Qualified design:

```text
parser/OCR/normalizer/splitter
        ↓
immutable prepared parts
        ↓
manifest published last
        ↓
PostgreSQL PreparedArtifactCheckpoint
        ↓
verified replay after crash/retry
```

Manifest/parts carry SHA-256 and compatibility evidence. M8 must consume this boundary and must not construct a second processing pipeline.

---

# M8 — AstraVector Delivery & Reliability

**Status:** 🚧 ACTIVE — ARCHITECTURE APPROVED, FINAL RELEASE QUALIFICATION OPEN

M8 is one coherent milestone. Roadmap 2.0 retains the useful M8.1/M8.2/M8.3 implementation history while restoring the original M8.0 requirement that reconciliation, real downstream evidence and post-implementation traceability must be completed before declaring **M8 QUALIFIED**.

## M8.A — AccessZone / TTL durable lineage

**Historical implementation label:** M8.1  
**Status:** ✅ QUALIFIED

Qualified invariants:

- singular/plural boundary normalization;
- exactly one effective AccessZone;
- four-digit `AccessZoneCode` preserved byte-for-byte;
- requested ID + requested code may coexist as correlation assertion;
- `ttlDays=0` inheritance semantics;
- negative TTL rejected;
- AccessZone/TTL survive PostgreSQL and M7 replay.

## M8.B — AstraVector wire adapter

**Historical implementation label:** M8.2  
**Status:** ✅ QUALIFIED / MERGED

Qualified capabilities:

- pinned AstraVector proto/client generation;
- canonical batch/final hash contract and golden vectors;
- `LogicalBlock` → generated protobuf mapper;
- real generated-gRPC Start/Append/Finalize/Abort/status round-trips;
- deterministic bounded batching;
- response identity/ack validation;
- fail-closed gRPC failure classification;
- `READY_TO_ACTIVATE != SEARCHABLE`;
- `ACTIVE + searchable=true + consistent sync evidence -> SEARCHABLE`;
- real M7 prepared-artifact → M8 delivery-input mapping.

## M8.C — Durable delivery execution

**Historical implementation label:** M8.3  
**Status:** 🚧 IMPLEMENTED / FINAL CI AND MERGE OPEN

### M8.C.1 Ownership and fencing

**Status:** ✅ QUALIFIED BY GREEN IMPLEMENTATION GATES

Includes:

- lease-fenced PREPARED/ACCEPTED batch mutations;
- stale worker rejection;
- production coordinator fencing for Start/session binding/final hash/resolved zone;
- RPC deadline vs remaining lease window guard.

### M8.C.2 Retry / backoff / dead letter

**Historical label:** M8.3.1  
**Status:** IMPLEMENTED — FINAL BRANCH QUALITY GATE OPEN

Contract:

```text
TRANSIENT / DEPENDENCY_UNAVAILABLE
    -> bounded RETRY_WAIT
    -> DEAD_LETTER after attempt budget

PERMANENT_INPUT / PERMANENT_POLICY / RESOURCE_LIMIT
    -> FAILED

DOWNSTREAM_AMBIGUOUS
    -> RECONCILE
    -> never blind retry

OWNERSHIP_LOST
    -> ABANDON without stale-worker mutation
```

### M8.C.3 Crash / resume from M7 prepared artifacts

**Historical label:** M8.3.2  
**Status:** IMPLEMENTED — FINAL BRANCH QUALITY GATE OPEN

Required recovery path:

```text
new lease owner
  -> verify current ownership
  -> load PreparedArtifactCheckpoint
  -> verify manifest / artifact / source hashes
  -> verify compatibility
  -> verify document/version + AccessZone/TTL lineage
  -> rebuild M8 delivery input from verified M7 artifact
  -> resume delivery without parser/OCR rerun
```

### M8.C.4 Durable failure-injection matrix

**Historical label:** M8.3.3  
**Status:** IMPLEMENTED — FINAL BRANCH QUALITY GATE OPEN

Must cover at least:

- ownership loss before/after Start;
- Start ACK loss;
- PREPARED-before-Append crash;
- remote Append accepted / local ACK commit lost;
- same-index/same-hash replay;
- insufficient lease window;
- bounded retry and attempt exhaustion;
- crash/reclaim using M7 artifact;
- Finalize ambiguity;
- Abort ambiguity;
- session complete while vector readiness is pending.

### M8.C exit gate

M8.C is not QUALIFIED until all of the following are true:

```text
Ruff lint                    PASS
Ruff format                  PASS
M8 scoped mypy               PASS
package build                PASS
full pytest                  PASS
PostgreSQL integration       PASS
PR merged to main            YES
post-merge main CI           PASS
```

## M8.D — Post-implementation traceability audit

**Status:** ⬜ NEXT AFTER M8.C MERGE

This replaces the ambiguous earlier notion of immediately creating a new M8.4 subsystem.

Audit every original M8.0 P0/P1/P2 requirement against executable evidence and the current implementation.

Required normative inputs:

```text
TZ-01 Indexation API / Job Contract
TZ-10 Access Zones / TTL
TZ-11 AstraVector Integration
TZ-13 Reliability / Recovery
TZ-17 Testing / Verification
M8.0 Contract Freeze & Gap Audit
```

Every M8.0 checkbox must receive one disposition:

```text
QUALIFIED       executable evidence exists
IMPLEMENTED     code exists, evidence incomplete
MOVED           explicitly owned by later milestone with rationale
GAP             release blocker
OBSOLETE        superseded by newer authoritative contract, with citation
```

No unchecked historical list may remain as the operational status source.

## M8.E — Reconciliation closure

**Status:** ⬜ CONDITIONAL NEXT

Implement only the gaps identified by M8.D.

Scope is convergence after ambiguous downstream mutations, not a duplicate delivery engine.

Minimum expected responsibilities:

- reconcile ambiguous Start/Finalize/Abort outcomes;
- reuse existing session/document identity where authoritative evidence exists;
- prevent duplicate logical document versions;
- reconcile local checkpoint with AstraVector public status only;
- never read AstraVector PostgreSQL or Qdrant directly;
- preserve AccessZone/TTL/document identity during recovery;
- converge to deterministic local state or explicit operator-visible failure.

Long-running reconciliation scheduling/persistence is introduced only where the traceability audit proves the current inline reconciliation insufficient.

## M8.F — Real AstraVector qualification

**Status:** ⬜ REQUIRED BEFORE M8 QUALIFIED

In-process generated-gRPC tests are necessary but not sufficient for final M8 release qualification.

Required real-service evidence:

```text
1. Start -> Append(s) -> Finalize
2. session completion
3. vector lifecycle progression
4. GetDocumentVectorStatus
5. SEARCHABLE proof
6. retrieval-visible document/version
```

Required variants:

- AccessZone by code, including a leading-zero code;
- AccessZone by UUID;
- `ttl_days=0` inheritance;
- finite positive TTL;
- multi-batch large document;
- duplicate/replayed delivery without duplicate logical version;
- AstraVector unavailable/restarted during delivery;
- ambiguous Finalize recovery;
- worker crash/reclaim using M7 prepared artifact.

## M8.G — Final M8 qualification

**Status:** ⬜ BLOCKED BY M8.C/D/E/F

M8 becomes **QUALIFIED** only when:

```text
M8.A AccessZone/TTL lineage          QUALIFIED
M8.B wire adapter                    QUALIFIED
M8.C durable delivery                QUALIFIED
M8.D traceability audit              COMPLETE, no unresolved P0
M8.E reconciliation gaps             QUALIFIED or proven unnecessary
M8.F real AstraVector evidence        PASS
full CI on final branch/main          PASS
```

Required final document:

```text
docs/M8-FINAL-QUALIFICATION.md
```

It must contain exact commit pins, CI runs, AstraVector revision, test matrix and remaining non-blocking debt.

Only then:

```text
M8 = QUALIFIED
```

---

# M9 — Document Lifecycle & Knowledge Reconciliation

**Status:** ⬜ PLANNED — BLOCKED BY M8 QUALIFICATION

M9 owns business/document lifecycle beyond delivery reliability.

Scope:

- reindex/new document version lifecycle;
- previous searchable version remains valid while new version builds;
- delete lifecycle through public AstraVector facade;
- cancellation at business lifecycle level;
- Knowledge Inventory projection;
- requested/resolved AccessZone visibility;
- TTL/expiry visibility where public downstream evidence exists;
- operator-visible lifecycle reconciliation not already required to make M8 delivery safe.

Important boundary:

```text
M8 reconciliation = make one delivery operation converge safely
M9 reconciliation = maintain document lifecycle / inventory over time
```

---

# M10 — Internal API, Observability & Operational Hardening

**Status:** ⬜ PLANNED

Scope:

- FastAPI internal surfaces;
- live/ready/capabilities health;
- job and knowledge diagnostics;
- smoke-test orchestration through normal production path;
- structured logs and correlation IDs;
- Prometheus metrics;
- optional tracing;
- graceful worker shutdown;
- lease draining;
- bounded worker concurrency/backpressure;
- dependency readiness semantics.

Operational hardening is not permitted to redefine delivery correctness established by M8.

---

# M11 — Full E2E, Reliability, Performance & RAG Verification

**Status:** ⬜ PLANNED

This is the product-level verification stage.

Required end-to-end pipeline:

```text
submission
 -> PostgreSQL job
 -> SeaweedFS acquisition
 -> parser / OCR
 -> normalization / splitter
 -> M7 prepared artifact
 -> M8 durable AstraVector delivery
 -> SEARCHABLE
 -> retrieval proof
```

Required evidence:

- clean PostgreSQL initialization and migrations;
- real SeaweedFS source;
- real OCR where applicable;
- real AstraVector;
- worker/process restart matrix;
- DB/SeaweedFS/AstraVector outage/recovery;
- large-document bounded-memory/RPC behavior;
- repeated delivery/version scenarios;
- RU/KK/EN and mixed-language corpora;
- RAG Recall@K, MRR, nDCG, duplicate-context and citation correctness evidence.

---

# M12 — Production Readiness, Deployment & Operations

**Status:** ⬜ PLANNED

Outputs:

- reproducible container image;
- controlled Alembic migration job/step;
- worker role topology;
- model preload/checksum verification;
- ConfigMap/Secret integration;
- resource requests/limits based on measured evidence;
- readiness/liveness;
- post-deploy smoke;
- autoscaling only when justified by measurements;
- backup/restore verification;
- rollback procedure;
- operational runbooks;
- clean-install production qualification.

Final release gate:

```text
M0..M12 required release criteria satisfied
       ↓
ASTRAINDEXATOR 1.0 RELEASE QUALIFIED
```

---

## 5. Current position — 2026-08-27

```text
M0   Foundation                              PARTIAL / CONTINUOUS
M1   Persistence Foundation                  ✅ QUALIFIED
M2   Job Coordinator                         ✅ QUALIFIED
M3   SeaweedFS Safe Acquisition              ✅ IMPLEMENTED
M4   Canonical Parser                        ✅ IMPLEMENTED
M5   OCR Pipeline                            ✅ IMPLEMENTED/HARDENED
M6   Normalization / Logical Splitter        ✅ IMPLEMENTED/HARDENED
M7   Prepared Artifacts / Replay             ✅ QUALIFIED

M8   AstraVector Delivery & Reliability      🚧 ACTIVE
 ├─ M8.A AccessZone / TTL lineage            ✅ QUALIFIED
 ├─ M8.B Wire Adapter                        ✅ QUALIFIED / MERGED
 ├─ M8.C Durable Delivery                    🚧 FINAL GATE OPEN
 ├─ M8.D Traceability Audit                  ⬜ NEXT
 ├─ M8.E Reconciliation Closure              ⬜ AFTER AUDIT
 ├─ M8.F Real AstraVector Qualification      ⬜ REQUIRED
 └─ M8.G Final M8 Qualification              ⬜

M9   Document Lifecycle / Inventory          ⬜
M10  Internal API / Operational Hardening    ⬜
M11  Full E2E / Reliability / RAG            ⬜
M12  Deployment / Production Readiness       ⬜
```

Current development position:

```text
feat/m8-3-durable-delivery / PR #37
        ↓
fix final branch quality gate
        ↓
M8.C QUALIFIED + merge + post-merge CI
        ↓
M8.D POST-IMPLEMENTATION TRACEABILITY AUDIT
```

---

## 6. Immediate execution sequence

No new functional branch should start before the current gate is closed.

```text
STEP 1
Repair final M8.C quality failure
  -> Ruff
  -> format
  -> scoped mypy
  -> build
  -> full pytest/PostgreSQL

STEP 2
Merge PR #37 after green final head
  -> verify post-merge main CI
  -> M8.C = QUALIFIED

STEP 3
Execute M8.D traceability audit
  -> TZ-01 / TZ-10 / TZ-11 / TZ-13 / TZ-17
  -> M8.0 P0/P1/P2 matrix
  -> identify only real remaining gaps

STEP 4
Implement M8.E reconciliation closure
  -> only audit-proven gaps

STEP 5
Execute M8.F real AstraVector qualification
  -> real service
  -> crash/restart/duplicate/large-doc
  -> AccessZone ID/code
  -> TTL inherit/finite
  -> SEARCHABLE + retrieval evidence

STEP 6
Produce M8-FINAL-QUALIFICATION.md
  -> final green main
  -> M8 = QUALIFIED

STEP 7
Begin M9 document lifecycle
```

---

## 7. Change-control rule

No implementation milestone may silently redefine a qualified invariant.

Any required change to AccessZone, TTL, identity, lifecycle, downstream wire semantics, hash canonicalization, retry ownership, lease fencing or completion semantics must update the relevant TZ/contract in the same reviewed change.

Roadmap numbering is organizational. The frozen system contracts and executable qualification evidence are authoritative.
