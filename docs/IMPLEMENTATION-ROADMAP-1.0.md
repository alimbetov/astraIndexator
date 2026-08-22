# AstraIndexator 1.0 — Implementation Roadmap

## 1. Purpose

This roadmap converts the approved TZ-00..TZ-18 baseline into an implementation sequence with executable evidence at every milestone.

Core rule:

```text
code exists != milestone complete
milestone complete = implementation + required TZ-17 evidence
```

## 2. Sources of truth

Implementation follows this precedence:

```text
AstraIndexator TZ-00..TZ-18
+
actual AstraVector wire contract in alimbetov/llm2
+
approved consumer mapping in alimbetov/agent-astradeployment-portable-local-1.0
```

For downstream DTOs:

```text
llm2 proto = wire authority
agent-astradeployment-portable-local-1.0 = approved consumer/application mapping
AstraIndexator = domain/implementation specification
```

AstraIndexator must not introduce a third incompatible DTO model.

## 3. Frozen implementation invariants

1. PostgreSQL is the durable Inbox/job/coordinator authority.
2. `indexation_job` is the canonical Inbox; no duplicate generic inbox table is required.
3. Producer delivery idempotency uses `producer_request_id`, distinct from internal `job_id`.
4. Processing is at-least-once; correctness uses deterministic identity, fencing, idempotency and reconciliation.
5. Multi-replica coordination uses `FOR UPDATE SKIP LOCKED`, leases and monotonic `lease_generation`.
6. PostgreSQL time is authoritative for leases/retries.
7. Lease expiry itself revokes mutation authority, even before another worker reclaims the job.
8. SeaweedFS source is immutable for one `documentId + documentVersion`.
9. Canonical `documentVersion` is a positive numeric immutable version.
10. `accessZoneCode` is exactly four ASCII digits and remains a string; leading zeroes are significant.
11. Canonical root Access Zones are:

```text
0000 GENERAL
0100 CORPORATE
0200 REGULATORY
0300 LEGAL
0400 FINANCE
0500 HR
0600 TECHNICAL
0700 OPERATIONS
0800 SECURITY
0900 ARCHIVE
```

12. One indexed document version belongs to exactly one effective ingestion zone.
13. `ttl_days=0` means inherit AstraVector policy.
14. AstraIndexator owns semantic `LogicalFragment[]` and deterministic `LogicalBlock[]`; AstraVector owns tokenizer-aware searchable chunks and BGE-M3 embedding.
15. AstraIndexator uses `AstraVectorIngestionFacade`; it does not write directly to Qdrant or AstraVector PostgreSQL.
16. Local business completion requires downstream `searchable=true` proof.
17. Runtime model download is forbidden; approved model revisions are preloaded and checksum-verified.
18. Mutating downstream timeout is an ambiguous outcome and must be reconciled before replay/replacement.

---

# M0 — Project Foundation

**Status:** partially delivered by M1/M2 bootstrap; remaining operational pieces are implemented when first required.

### Goal

Create the executable Python project foundation without coupling the domain to FastAPI.

### Target stack

```text
Python 3.12+
FastAPI + Pydantic v2        # internal HTTP adapter, later milestone
SQLAlchemy 2.x
Alembic
psycopg 3
asyncio                      # worker orchestration
pytest + Testcontainers
```

Architecture follows Ports & Adapters. FastAPI is an inbound adapter, not the system core.

### Remaining outputs

- typed configuration;
- structured logging/correlation IDs;
- build/version metadata;
- formatting/lint/type-check gates;
- container skeleton.

---

# M1 — Persistence Foundation

**Status:** ✅ IMPLEMENTED AND MERGED

**Main merge:** `df979b081c491312ac582d1598c482715ee9332c`

### TZ scope

TZ-01, TZ-02, TZ-09, TZ-10, TZ-11, TZ-12, TZ-17, TZ-18.

### Implemented

```text
schema: astra_indexator

indexation_job            # durable Inbox
processing_attempt
delivery_checkpoint
delivery_batch
job_event
knowledge_inventory
```

Technology:

```text
SQLAlchemy 2.x
Alembic
psycopg 3
PostgreSQL 16 verification
```

Important persistence contracts:

```text
producer_request_id UUID UNIQUE NOT NULL
producer_request_id != job_id

document_version BIGINT CHECK (> 0)
access_zone_code VARCHAR(4) CHECK (^[0-9]{4}$)
```

Partial indexes exist for runnable work, retry scheduling, expired leases and active-document-version uniqueness.

`IndexationJobRepository.create_or_get()` uses PostgreSQL conflict handling for Inbox idempotency.

### Evidence

- clean PostgreSQL initializes through Alembic `0001_initial_schema`;
- domain and persistence tests pass;
- PostgreSQL 16 Testcontainers CI passes;
- leading zero Access Zones survive round-trip;
- invalid document versions/codes are rejected;
- duplicate active document version is rejected.

---

# M2 — Job Coordinator

**Status:** ✅ IMPLEMENTED AND MERGED

**Main merge:** `163c62783c2313ddbdabf3cea05e6f249256fe4f`

### TZ scope

TZ-02, TZ-13, TZ-17.

### Implemented coordinator contract

```text
PENDING
RETRY_WAIT when due
PROCESSING when lease expired
        ↓
SELECT ... FOR UPDATE SKIP LOCKED
        ↓
PROCESSING
worker_id
lease_generation + 1
processing_attempt
```

Lease token:

```text
LeaseToken {
  job_id
  worker_id
  lease_generation
  attempt_id
}
```

Authoritative mutation requires:

```text
job_id matches
worker_id matches
lease_generation matches
status = PROCESSING
lease_until >= PostgreSQL now()
```

Implemented:

- atomic multi-replica claim;
- heartbeat/lease renewal;
- expired lease reclaim;
- monotonic generation fencing;
- previous attempt marked `LEASE_EXPIRED` on reclaim;
- processing-stage advancement;
- coordinator-level completion mechanics;
- retry scheduling;
- cancellation/max-attempt claim guards;
- durable job events.

### Evidence

PostgreSQL 16/Testcontainers tests prove:

- three replicas claim three distinct jobs;
- three replicas racing one job produce one current owner;
- heartbeat extends a valid lease;
- expired lease is reclaimable;
- generation increments on reclaim;
- stale worker heartbeat fails;
- expired worker cannot complete before reclaim;
- stage/completion mutations are fenced;
- RETRY_WAIT is not claimable before due time.

Important: M2 `complete()` implements coordinator mechanics only. Later milestones must call completion only after the complete business invariant is satisfied, including AstraVector `searchable=true`.

---

# M3 — SeaweedFS & Safe Acquisition

**Status:** planned

### TZ scope

TZ-03, TZ-04, TZ-13, TZ-17.

### Goal

Implement immutable source access and bounded acquisition without introducing arbitrary external URL fetching.

### Outputs

Storage port:

```text
head
open_read / stream
put_stream
delete
list_prefix
```

Acquisition:

- approved SeaweedFS reference validation;
- streaming download;
- SHA-256 calculation;
- source size/type verification;
- bounded attempt workspace;
- immutable source identity check;
- file/container safety limits.

Initial types:

```text
PDF
DOCX
TXT
Markdown
JPEG
PNG
TIFF
```

### Definition of Done

- partial download never reaches parser;
- source mutation is detected;
- large source is processed with bounded memory;
- hostile ZIP/image/container cases are rejected according to TZ-04.

---

# M4 — Canonical Parser

**Status:** planned

### TZ scope

TZ-05, TZ-09, TZ-17.

### Goal

Produce deterministic structural `ParsedDocument + DocumentElement[]`.

### Delivery order

1. TXT/Markdown baseline;
2. native PDF;
3. DOCX;
4. image shell elements;
5. mixed/scanned PDF classification;
6. tables/images/captions/multi-column/bilingual layouts.

### Definition of Done

Golden fixtures prove stable structure, reading order, provenance and deterministic IDs. Parser must not invent unavailable physical coordinates such as fake DOCX page numbers.

---

# M5 — OCR Pipeline & Nexus Model Supply

**Status:** planned

### TZ scope

TZ-06, TZ-15, TZ-17, TZ-18.

### Goal

Implement selective multilingual OCR with offline execution.

### Modes

```text
OCR_DISABLED
OCR_IF_NEEDED
OCR_FORCE
```

Scopes:

```text
PAGE
REGION
EMBEDDED_IMAGE
```

Baseline languages:

```text
ru
kk
en
mixed ru/kk/en
```

Required capabilities:

- CPU portable baseline;
- pinned immutable model revision;
- Nexus preload;
- manifest/SHA-256 verification;
- no document-time model download;
- native/OCR reconciliation;
- duplicate suppression;
- bounded page/concurrency processing.

---

# M6 — Text Normalization & Logical Splitter

**Status:** planned

### TZ scope

TZ-07, TZ-08, TZ-09, TZ-17.

### Goal

Produce deterministic multilingual semantic fragments without taking over AstraVector tokenizer ownership.

### Normalizer

- Unicode NFC;
- conservative whitespace/dehyphenation;
- protected technical/legal identifiers;
- page-furniture suppression;
- preserve Kazakh letters, tables, lists, code, URLs, UUIDs, versions, dates.

### Splitter

Structure-first boundaries:

```text
CHAPTER
SECTION
SUBSECTION
ARTICLE
CLAUSE
PARAGRAPH
LIST_GROUP
SENTENCE
FORCED_SPLIT
```

Size values in TZ-08 are calibration inputs, not permanent magic constants. Runtime AstraIndexator does not load the BGE-M3 tokenizer for final chunking.

Output:

```text
LogicalFragment[]
        ↓
TZ-09 LogicalBlockMapper
        ↓
LogicalBlock[]
```

---

# M7 — Prepared Artifacts & Replay

**Status:** planned

### TZ scope

TZ-03, TZ-09, TZ-13, TZ-17.

### Goal

Persist expensive processing output so retry/recovery does not automatically repeat parser/OCR.

Baseline layout:

```text
manifest.json
parts/elements-00000.jsonl
parts/fragments-00000.jsonl
...
```

Manifest is published last and acts as commit marker. Parts have SHA-256, record counts and byte counts. Publication/checkpoint installation must be fenced by the current M2 lease token.

---

# M8 — AstraVector Integration

**Status:** planned

### TZ scope

TZ-09, TZ-10, TZ-11, TZ-12, TZ-13, TZ-17.

### Goal

Implement the real adapter to `alimbetov/llm2`.

Use generated protobuf classes from the actual proto revision.

Pipeline:

```text
LogicalFragment[]
→ LogicalBlockMapper
→ LogicalBlock[]
→ generated protobuf
→ AstraVectorIngestionFacade
```

Large-document path:

```text
StartLogicalDocumentIngestion
AppendLogicalDocumentBlocks x N
FinalizeLogicalDocumentIngestion
GetLogicalDocumentIngestionStatus
GetDocumentVectorStatus
```

Persist requested `accessZoneCode` and resolved `accessZoneId` where available.

### External P0 gate

Byte-exact canonicalization for:

```text
batch_content_hash
final_content_hash
```

must be proven with shared Rust/Python golden fixtures. AstraIndexator must not invent an independent serialization algorithm.

### Completion invariant

```text
session COMPLETED != document completed
local COMPLETED requires downstream searchable=true
```

---

# M9 — Document Lifecycle, Reconciliation & Knowledge Inventory

**Status:** planned

### TZ scope

TZ-10, TZ-11, TZ-12, TZ-13, TZ-14.

### Goal

Implement reindex/version/delete/cancel/reconciliation semantics and operational knowledge visibility.

Outputs:

- numeric version lifecycle;
- previous version remains searchable while new version builds;
- async delete through AstraVector facade;
- ambiguous downstream mutation reconciliation;
- Knowledge Inventory projection;
- searchable/vector sync counts;
- requested/resolved access-zone identity;
- TTL state and authoritative expiry visibility when downstream exposes it.

AstraIndexator must not read Qdrant or AstraVector PostgreSQL directly for lifecycle/observability.

---

# M10 — Internal API, Smoke & Observability

**Status:** planned

### TZ scope

TZ-14, TZ-16, TZ-17, TZ-18.

### HTTP adapter

FastAPI internal surfaces:

```text
GET  /internal/health/live
GET  /internal/health/ready
GET  /internal/health/capabilities
GET  /internal/v1/knowledge
GET  /internal/v1/jobs/{jobId}
POST /internal/v1/smoke-tests
GET  /internal/v1/smoke-tests/{id}
POST /internal/v1/smoke-tests/{id}/cleanup
```

Smoke must use the normal durable production path and reserved immutable fixtures; it is not a second ingestion API.

Add structured logs, Prometheus metrics, audit visibility and optional tracing.

---

# M11 — Reliability, Performance & RAG Verification

**Status:** planned

### TZ scope

TZ-13, TZ-17, TZ-18.

### Required evidence

- multi-replica stress/race tests;
- worker crash at each major stage;
- DB/SeaweedFS/AstraVector outage/recovery;
- lost ACK reconciliation;
- stale worker proof;
- large-document bounded-memory tests;
- OCR CPU throughput;
- RAG benchmark RU/KK/EN;
- Recall@K, MRR, nDCG, citation correctness and duplicate-context metrics;
- parser/OCR/splitter regression corpora.

Resource requests/limits and autoscaling thresholds must come from measured evidence rather than guessed constants.

---

# M12 — Deployment & Operations

**Status:** planned

### TZ scope

TZ-15, TZ-16, TZ-17, TZ-18.

### Production outputs

```text
container image
controlled Alembic migration step/job
astra-indexator-control       # optional separate role
astra-indexator-worker-cpu x N
astra-indexator-worker-gpu     # optional
model preload/init
ConfigMap/Secret integration
readiness/liveness
post-deploy smoke
HPA/KEDA when justified
runbooks
backup/restore verification
rollback procedure
```

Deployment technology does not provide indexing correctness; PostgreSQL coordination, immutable artifacts, fencing and reconciliation do.

---

## 4. Current implementation state

```text
M0  Foundation                     PARTIAL
M1  Persistence Foundation         ✅ MERGED
M2  Job Coordinator                ✅ MERGED
M3  SeaweedFS & Acquisition        NEXT
M4  Parser                         PLANNED
M5  OCR                            PLANNED
M6  Normalization/Splitter         PLANNED
M7  Prepared Artifacts             PLANNED
M8  AstraVector Integration        PLANNED
M9  Lifecycle/Inventory            PLANNED
M10 Internal API/Observability     PLANNED
M11 Verification                   PLANNED
M12 Deployment                     PLANNED
```

## 5. Recommended next vertical slice

The next implementation milestone is **M3 — SeaweedFS & Safe Acquisition**.

M3 should consume a genuinely claimed M2 job and prove:

```text
PENDING job
→ M2 claim + lease token
→ validate SeaweedFS source reference
→ stream immutable source
→ SHA-256 / size / type validation
→ bounded attempt workspace
→ fenced stage checkpoint
```

This is the first milestone that connects the durable coordinator to actual document bytes while still avoiding parser/OCR complexity.

## 6. Change-control rule

If implementation requires changing a baseline contract, update the relevant TZ first or in the same reviewed change. Implementation code must not silently redefine a canonical DTO, Access Zone, lifecycle state or downstream ownership boundary.
