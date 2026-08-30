# AstraIndexator 1.0 — Implementation Roadmap 2.0

## 1. Status and purpose

**Status:** ACTIVE ROADMAP  
**Supersedes for forward planning:** `docs/IMPLEMENTATION-ROADMAP-1.0.md`  
**Historical M8 freeze:** `docs/M8.0-CONTRACT-FREEZE-GAP-AUDIT.md`  
**Current M8 remediation authority:** `docs/M8-COMPLETION-REMEDIATION-SPEC.md`

Roadmap 2.0 reconciles the implementation plan with executable evidence accumulated through M8 durable delivery and the code-only AccessZone refactor.

Core rule:

```text
code exists != milestone complete
milestone complete = implementation + required verification evidence + green CI + reviewed merge
```

A milestone marked **QUALIFIED** must have executable evidence. A later milestone may not weaken an earlier qualified invariant.

---

## 2. Sources of truth and precedence

```text
1. current reviewed TZ-00..TZ-18 requirements
2. current AccessZone code-only contract freeze
3. actual finalized AstraVector wire contract in llm2
4. approved integration contract
5. qualified executable tests
6. current M8 Completion Remediation spec
7. this roadmap
```

Generated AstraVector protobuf is transport-only. AstraIndexator must not maintain a parallel hand-written wire protocol and must not access AstraVector PostgreSQL or Qdrant directly.

Historical documents that describe producer AccessZone UUID compatibility are superseded for that topic by `ACCESS-ZONE-CODE-CONTRACT-FREEZE.md`, migration `0006_access_zone_code_only`, and current executable code/tests.

---

## 3. Frozen architectural invariants

1. PostgreSQL is the durable Inbox, scheduling, lease and recovery authority.
2. Processing is at-least-once; correctness relies on deterministic identity, idempotency, durable checkpoints, fencing and reconciliation.
3. PostgreSQL time is authoritative for lease expiry and retry scheduling.
4. Lease expiry immediately revokes mutation authority, even before another worker reclaims the job.
5. Every authoritative worker-side mutation is fenced by `job_id + worker_id + lease_generation + non-expired lease`.
6. Mutating downstream RPCs may start only when the remaining lease safely exceeds the RPC deadline plus safety margin.
7. `documentId`, `documentVersion`, `jobId`, `attemptId`, `fragmentId`, `ingestionSessionId` and AstraVector chunk identity are distinct.
8. One document version maps to one effective AccessZoneCode.
9. `accessZoneCode` is exactly four ASCII digits; leading zeroes are significant.
10. AstraIndexator producer/domain/inventory AccessZone identity is code-only. Producer UUID selectors are rejected.
11. AstraVector owns code-to-internal-UUID resolution. `delivery_checkpoint.resolved_access_zone_id` is private downstream recovery/status evidence only.
12. `ttl_days=0` means inherit AstraVector policy; it never means client-forced forever.
13. M7 prepared artifacts are the preferred restart boundary after parser/OCR/splitter work.
14. M8 recovery must not silently rerun parser/OCR when a compatible verified M7 artifact is available.
15. Append replay identity is `session_id + batch_index + batch_content_hash`.
16. Stable Start identity is derived from `document_id + document_version + verified source SHA-256`, not local `job_id`.
17. A mutating RPC timeout is an ambiguous outcome, not proof of failure.
18. Ingestion session `COMPLETED` is not equivalent to document `SEARCHABLE`.
19. Local business completion requires authoritative AstraVector readiness evidence.
20. Unknown downstream states/errors fail closed unless explicitly classified by a qualified contract.
21. M7→M8 replay must verify immutable delivery compatibility evidence before downstream mutation.

---

# 4. Implementation roadmap

## M0 — Project Foundation

**Status:** PARTIALLY DELIVERED / CONTINUOUS HARDENING

Foundation capabilities are introduced when required rather than through a separate big-bang phase.

Already present:

- Python package/build structure;
- SQLAlchemy/Alembic foundation;
- pytest/Testcontainers CI;
- Ruff and scoped mypy gates;
- typed configuration components.

Remaining production-facing foundation work belongs primarily to M10/M12.

---

## M1 — Persistence Foundation

**Status:** ✅ QUALIFIED / MERGED

Durable PostgreSQL model established for jobs, attempts, delivery checkpoints/batches, events and knowledge inventory.

Key invariants include producer request idempotency, positive immutable document version, AccessZoneCode preservation and PostgreSQL migration verification.

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

---

## M3 — SeaweedFS Safe Acquisition

**Status:** ✅ IMPLEMENTED IN CURRENT PIPELINE

Capabilities include approved source boundary, immutable/bounded acquisition, SHA-256/source evidence, attempt workspace safety and mutation/partial-acquisition protection.

Release-level traceability remains part of later audits.

---

## M4 — Canonical Parser

**Status:** ✅ IMPLEMENTED

Deterministic structural parsing exists for the qualified baseline and extended formats. Release-level regression evidence belongs to M11.

---

## M5 — OCR Pipeline

**Status:** ✅ IMPLEMENTED / HARDENED

Includes OCR policy, offline model/runtime controls, multilingual verification and PP-OCRv5 qualification assets. Production throughput/resource evidence belongs to M11/M12.

---

## M6 — Normalization & Logical Splitter

**Status:** ✅ IMPLEMENTED / HARDENED

Produces deterministic semantic fragments while preserving AstraVector ownership of tokenizer-aware searchable chunking and embedding.

---

## M7 — Prepared Artifacts & Replay

**Status:** ✅ QUALIFIED

M7 is the canonical expensive-processing restart boundary:

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

Manifest/parts carry SHA-256 and compatibility evidence. M8 consumes this boundary and must not construct a second processing pipeline.

---

# M8 — AstraVector Delivery & Reliability

**Status:** ✅ B2 REAL-RUNTIME QUALIFIED ON `codex/real-pdf-smoke`; DEFAULT-BRANCH MERGE/CI STILL REQUIRED

M8 remains one coherent milestone. The durable-delivery core, completion remediation and CODEX-10 real AstraVector B2 positive path are implemented and qualified on the working branch. Default-branch merge plus post-merge CI are still required before `main` is advertised as fully qualified.

## M8.A — AccessZone / TTL durable lineage

**Status:** ✅ QUALIFIED / CODE-ONLY CONTRACT MERGED

Current invariants:

- boundary normalizes singular/plural AccessZoneCode representations to exactly one effective code;
- producer UUID selectors are explicitly rejected;
- `access_zone_code` is preserved as exactly four ASCII digits;
- leading zeroes are significant;
- `ttlDays=0` inherits AstraVector policy;
- negative TTL is rejected;
- AccessZoneCode/TTL survive PostgreSQL and M7 replay;
- AstraVector receives `access_zone_code`; compatibility `access_zone_id` on Start is absent/None;
- resolved AstraVector UUID may be retained only in the private delivery checkpoint for public status/recovery.

## M8.B — AstraVector wire adapter

**Status:** ✅ QUALIFIED / MERGED

Capabilities:

- pinned AstraVector proto/client generation;
- canonical batch/final hash contract and golden vectors;
- `LogicalBlock` generated-protobuf mapper;
- generated-gRPC Start/Append/Finalize/Abort/status round-trips;
- deterministic bounded batching;
- response identity/ack validation;
- fail-closed gRPC classification;
- `READY_TO_ACTIVATE` synchronization, public activation, and searchable completion evidence;
- real M7 prepared-artifact → M8 delivery-input mapping.

## M8.C — Durable delivery execution

**Status:** ✅ IMPLEMENTED / CODEX-10 QUALIFIED

Existing capabilities include lease-fenced PREPARED/ACCEPTED batch mutations, stale-worker rejection, Start/session/final-hash/resolved-zone fencing, public activation fencing, RPC lease-window guards, retry/dead-letter primitives, crash/reclaim from M7 artifacts and failure-injection coverage.

Completion Remediation adds/qualifies the missing release-level closure rather than creating a second delivery engine.

## M8.CR — Completion Remediation

**Normative spec:** `docs/M8-COMPLETION-REMEDIATION-SPEC.md`  
**Status:** ✅ IMPLEMENTED / CODEX-10 POSITIVE REAL-RUNTIME PATH PASS

Required work packages:

```text
CR-01 stable logical Start idempotency
CR-02 mandatory verified source SHA-256 before mutation
CR-03 unified durable runtime failure executor
CR-04 ambiguous Finalize convergence / explicit public-contract gap
CR-05 immutable M7→M8 delivery compatibility fingerprint
CR-06 canonical runtime bootstrap
CR-07 public ActivateDocumentVersion completion bridge
```

Branch qualification evidence includes Ruff lint/format, scoped mypy, full pytest, PostgreSQL/migration verification, package build and CODEX-10 real AstraVector B2 smoke. No CR item is called qualified on the default branch before merge and post-merge main CI.

## M8.D — Post-implementation traceability reconciliation

**Status:** ✅ UPDATED THROUGH CODEX-10 CONTRACT RECONCILIATION

Audit current requirements against executable evidence. Historical UUID-producer requirements must be marked obsolete/superseded rather than reintroduced.

Each requirement receives one disposition:

```text
QUALIFIED
IMPLEMENTED
MOVED
GAP
OBSOLETE/SUPERSEDED
```

No unchecked historical list may remain the operational status source.

## M8.E — Reconciliation closure

**Status:** ✅ IMPLEMENTED / POSITIVE PATH QUALIFIED

Scope is convergence after ambiguous downstream mutations, not a duplicate delivery engine.

Minimum responsibilities:

- reuse existing session/document identity where authoritative evidence exists;
- prevent blind replay after ambiguous mutations;
- reconcile local checkpoint with AstraVector public status only;
- never read AstraVector PostgreSQL/Qdrant directly;
- preserve AccessZoneCode/TTL/document identity;
- converge to deterministic local state or explicit operator-visible unresolved state.

## M8.F — Real AstraVector qualification

**Status:** ✅ CODEX-10/B2 POSITIVE PATH PASS

In-process/generated-gRPC tests are necessary but not sufficient.

CODEX-10 supplied the required positive real-service evidence against `registry.astrabase.asia/astravector:sha-f6493fa`:

```text
1. Start -> Append(s) -> Finalize
2. session completion
3. vector lifecycle progression
4. GetDocumentVectorStatus
5. SEARCHABLE proof
6. retrieval-visible document/version
```

Required variants:

- AccessZoneCode with a leading-zero code;
- explicit proof that producer UUID selectors are rejected by AstraIndexator;
- `ttl_days=0` inheritance;
- finite positive TTL;
- multi-batch large document;
- duplicate/replayed delivery without duplicate logical version;
- AstraVector unavailable/restarted during delivery;
- ambiguous Finalize recovery or explicit finalized-public-contract gap evidence;
- worker crash/reclaim using M7 prepared artifact;
- M7→M8 compatibility fingerprint acceptance/rejection cases.

Producer “AccessZone by UUID success” is obsolete and is not an AstraIndexator qualification variant.

CODEX-10 caveat: retrieval of the real PDF was proven through public HTTP with `callerAccessLevel=INTERNAL`; `PUBLIC` did not return the target document. The positive path does not rely on AstraVector private PostgreSQL/Qdrant access.

## M8.G — Final M8 qualification

**Status:** 🚧 BRANCH QUALIFIED; DEFAULT-BRANCH MERGE/CI AND FINAL ROLLUP DOC REMAIN

M8 becomes QUALIFIED only when:

```text
M8.A AccessZone/TTL lineage           QUALIFIED
M8.B wire adapter                     QUALIFIED
M8.C durable delivery core            QUALIFIED
M8.CR completion remediation          QUALIFIED
M8.D traceability reconciliation      COMPLETE, no unresolved P0
M8.F real AstraVector evidence        PASS
full final main CI                    PASS
```

Required final document:

```text
docs/M8-FINAL-QUALIFICATION.md
```

It must contain exact commit pins, CI runs, AstraVector revision, test matrix and remaining non-blocking debt.

---

# M9 — Document Lifecycle & Knowledge Reconciliation

**Status:** ⬜ PLANNED — BLOCKED BY M8 QUALIFICATION

Scope:

- reindex/new document version lifecycle;
- previous searchable version remains valid while new version builds;
- delete lifecycle through public AstraVector facade;
- cancellation at business lifecycle level;
- Knowledge Inventory projection;
- AccessZoneCode visibility;
- downstream resolved UUID visibility only where operationally required and never as producer identity;
- TTL/expiry visibility where public downstream evidence exists;
- operator-visible lifecycle reconciliation beyond one M8 delivery operation.

Boundary:

```text
M8 reconciliation = make one delivery operation converge safely
M9 reconciliation = maintain document lifecycle / inventory over time
```

---

# M10 — Internal API, Observability & Operational Hardening

**Status:** ⬜ PLANNED

Scope includes FastAPI internal surfaces, health/capabilities, diagnostics, smoke orchestration through production path, structured logs/correlation IDs, Prometheus metrics, optional tracing, graceful shutdown, lease draining, bounded concurrency/backpressure and dependency readiness semantics.

Operational hardening may not redefine M8 delivery correctness.

---

# M11 — Full E2E, Reliability, Performance & RAG Verification

**Status:** ⬜ PLANNED

Required product pipeline:

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

Evidence includes clean PostgreSQL initialization/migrations, real SeaweedFS/OCR/AstraVector, restart/outage matrices, large-document bounded behavior, repeated version scenarios, RU/KK/EN corpora and RAG quality metrics.

---

# M12 — Production Readiness, Deployment & Operations

**Status:** ⬜ PLANNED

Outputs include reproducible image, controlled Alembic migration step, worker topology, model preload/checksum verification, ConfigMap/Secret integration, measured resources, readiness/liveness, post-deploy smoke, justified autoscaling, backup/restore, rollback and operational runbooks.
