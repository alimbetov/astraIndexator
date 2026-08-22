# AstraIndexator 1.0 — Implementation Roadmap

## 1. Purpose

This roadmap converts the approved TZ-00..TZ-18 design baseline into an implementation sequence that preserves architectural invariants and produces executable evidence at each stage.

The implementation SHALL proceed by vertical slices rather than by isolated packages that cannot be proven end-to-end.

Core rule:

```text
no milestone is complete because code exists;
a milestone is complete only when its TZ-17 evidence passes.
```

---

## 2. Sources of truth

Implementation SHALL follow this precedence:

```text
AstraIndexator TZ-00..TZ-18
  +
actual AstraVector wire contract in alimbetov/llm2
  +
approved consumer mapping in agent-astradeployment-portable-local-1.0
```

For downstream DTOs:

```text
llm2 proto = wire authority
agent-astradeployment-portable-local-1.0 = approved consumer mapping
AstraIndexator = implementation/domain specification
```

AstraIndexator SHALL NOT introduce a third incompatible DTO model.

---

## 3. Frozen implementation invariants

The following are non-negotiable for 1.0:

1. PostgreSQL is the durable job/coordinator authority.
2. Processing is at-least-once; correctness comes from deterministic identity, idempotent effects, reconciliation and fencing.
3. Worker coordination uses `FOR UPDATE SKIP LOCKED`, renewable leases and monotonic `lease_generation` fencing.
4. SeaweedFS source objects are immutable for one `documentId + documentVersion`.
5. Canonical `documentVersion` is a positive numeric immutable version. Opaque upstream revisions are metadata only.
6. `accessZoneCode` is a four-character ASCII string; leading zeroes are significant.
7. Canonical root Access Zones are:

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

8. One indexed document version belongs to exactly one effective ingestion zone.
9. `ttl_days=0` means inherit AstraVector zone/platform policy.
10. AstraIndexator creates semantic `LogicalFragment[]` and deterministic `LogicalBlock[]`; AstraVector owns tokenizer-aware searchable chunks and BGE-M3 embedding.
11. AstraIndexator uses `AstraVectorIngestionFacade`; no direct Qdrant or AstraVector PostgreSQL writes.
12. Local `COMPLETED` requires downstream `searchable=true` proof.
13. Runtime public model download is forbidden; models are preloaded and checksum-verified.
14. Parser/OCR/normalizer/splitter do not infer or alter Access Zone or TTL.
15. Mutating downstream timeouts are ambiguous outcomes and trigger reconciliation before replay/replacement.

---

# 4. Implementation strategy

The project is divided into 12 implementation milestones.

Each milestone contains:

```text
Goal
TZ scope
Implementation artifacts
Dependencies
Definition of Done
Mandatory verification
```

The recommended branch/PR strategy is one milestone per reviewable PR stack or a small number of tightly coupled PRs.

---

# M0 — Project foundation and executable skeleton

## Goal

Create the production repository structure and development/test foundation without implementing business processing yet.

## TZ scope

TZ-00, TZ-15, TZ-16, TZ-17, TZ-18.

## Implementation artifacts

Recommended Python package structure:

```text
src/astra_indexator/
  app/
  domain/
  coordinator/
  storage/
  acquisition/
  parser/
  ocr/
  normalization/
  splitter/
  prepared/
  astravector/
  inventory/
  api/
  observability/
  config/

migrations/
tests/
  unit/
  component/
  integration/
  e2e/
  fixtures/
```

Add:

- typed application configuration;
- environment/profile loading;
- structured logging;
- correlation ID infrastructure;
- application/version metadata;
- `/internal/health/live` skeleton;
- `/internal/health/ready` skeleton;
- pytest/testcontainers foundation;
- formatting/lint/type checks;
- Dockerfile skeleton;
- CI PR gate.

## Definition of Done

- application starts with no external business work enabled;
- typed config fails closed for missing mandatory settings;
- secrets are not logged;
- unit/format/lint/type-check run in CI;
- container starts as expected;
- application version/commit are visible operationally.

## Mandatory verification

TZ-15 config precedence tests, TZ-16 secret redaction checks, TZ-17 PR-gate baseline.

---

# M1 — Canonical contracts, DTOs and PostgreSQL schema

## Goal

Implement the core domain model and durable persistence schema before worker behavior.

## TZ scope

TZ-01, TZ-02, TZ-09, TZ-10, TZ-11, TZ-12.

## Implementation artifacts

### Domain value objects

```text
DocumentId
DocumentVersion
ExternalRevision
JobId
ProcessingAttemptId
AccessZoneCode
AccessZoneId
KnowledgeType
TtlIntent
ProcessingStage
JobStatus
```

`AccessZoneCode` MUST validate `^[0-9]{4}$` and preserve leading zeroes.

Canonical `KnowledgeType` mapping:

```text
GENERAL    -> 0000
CORPORATE  -> 0100
REGULATORY -> 0200
LEGAL      -> 0300
FINANCE    -> 0400
HR         -> 0500
TECHNICAL  -> 0600
OPERATIONS -> 0700
SECURITY   -> 0800
ARCHIVE    -> 0900
```

### Persistence

Create versioned migrations for at least:

```text
indexation_job
indexation_attempt
ingestion_delivery_state
ingestion_batch_checkpoint
knowledge/audit support tables as needed later
```

Required constraints include:

- positive numeric `document_version`;
- valid job status;
- lease-generation monotonic semantics implemented by update predicates;
- uniqueness/idempotency constraints from TZ-01/TZ-02;
- requested + normalized Access Zone fields;
- requested TTL intent;
- processing fingerprint/version fields;
- session/batch downstream checkpoint fields.

### Canonical DTO schemas

Freeze serializable schemas for:

```text
IndexationJobRequest
IndexationJob
SourceReference
PreparedArtifactManifest
ParsedDocument
DocumentElement
LogicalFragment
```

## Definition of Done

- clean PostgreSQL initializes through migrations;
- schema constraints reject invalid canonical identities;
- all ten Access Zone root codes serialize exactly as strings;
- numeric document version is used throughout domain/persistence;
- no legacy `SOURCE/PARENT/SUB_*` downstream DTO remains in AstraIndexator domain.

## Mandatory verification

- DB migration test on empty PostgreSQL;
- migration replay/idempotency test;
- DTO round-trip tests;
- Access Zone root matrix tests;
- `0001 != 1` preservation test;
- invalid version and invalid code rejection tests.

---

# M2 — Job Coordinator: claim, lease, heartbeat and fencing

## Goal

Make PostgreSQL a functioning multi-replica durable work coordinator.

## TZ scope

TZ-02, TZ-13, TZ-17.

## Implementation artifacts

Implement:

```text
create_job
claim_next_job
renew_lease
checkpoint_stage
schedule_retry
mark_failed
mark_dead_letter
request_cancel
complete_job
reclaim_expired_job
```

Claim uses atomic transaction with:

```sql
FOR UPDATE SKIP LOCKED
```

Every authoritative worker mutation MUST include:

```text
job_id
worker_id
lease_generation
```

as a fencing predicate.

Add configurable:

```text
lease duration
heartbeat interval
retry policy
retry jitter
max attempts
```

## Definition of Done

- N replicas can safely share one queue;
- active leases are not stolen;
- expired leases can be reclaimed;
- stale workers cannot checkpoint/finalize after generation change;
- DB ownership uncertainty causes downstream mutation to stop.

## Mandatory verification

At least 3 workers against real PostgreSQL:

- single-job contention;
- many-job distribution;
- stale worker race;
- lease expiry/reclaim;
- heartbeat DB outage;
- cancel race;
- retry/dead-letter progression.

---

# M3 — SeaweedFS source storage and safe acquisition

## Goal

Implement immutable source acquisition and artifact storage abstraction.

## TZ scope

TZ-03, TZ-04, TZ-13.

## Implementation artifacts

Storage adapter contract:

```text
head
open_read(range)
put_stream
delete
list_prefix
```

Implement:

- source object verification;
- streaming download;
- SHA-256 calculation;
- bounded workspace;
- immutable source identity validation;
- attempt-isolated paths;
- staging publication primitives;
- orphan cleanup safeguards.

File validation baseline:

```text
PDF
JPEG
PNG
TIFF
DOCX
TXT
MARKDOWN
```

with hostile-container/image limits.

## Definition of Done

- source bytes are authoritative;
- partial acquisition never reaches parser;
- source mutation produces deterministic permanent failure;
- large source does not require full file duplication in memory;
- ZIP/image safety guards work.

## Mandatory verification

TZ-04 hostile-input matrix including fake PDF, ZIP bomb, traversal, excessive entries, encrypted unsupported files, pixel bombs and encoding failures.

---

# M4 — Canonical parser baseline

## Goal

Produce deterministic structural `ParsedDocument + DocumentElement[]` from supported sources.

## TZ scope

TZ-05, TZ-09, TZ-17.

## Implementation order

### Phase A

```text
PDF native text
DOCX
TXT
Markdown
JPEG/PNG/TIFF shell elements
```

### Phase B

```text
mixed/scanned PDF classification
multi-column reading order
bilingual layouts
tables
images/captions
```

## Implementation artifacts

Implement:

- file type handler registry;
- parser interfaces;
- element hierarchy/types;
- source location/provenance;
- reading-order reconstruction;
- deterministic element IDs;
- table cell/row representation;
- image elements as first-class canonical elements;
- parser version/profile fingerprinting.

## Definition of Done

Golden fixtures produce structurally stable output.

Parser never invents unavailable physical location data, e.g. fake DOCX page numbers.

## Mandatory verification

Golden corpus:

```text
native PDF
scanned PDF classification
mixed PDF classification
multi-column PDF
RU/KK bilingual layout
DOCX headings/lists/tables/images
TXT
Markdown
images
```

Assertions focus on structural fidelity and reading order.

---

# M5 — OCR CPU baseline and model delivery

## Goal

Implement conditional OCR enrichment with offline model execution.

## TZ scope

TZ-06, TZ-15, TZ-17, TZ-18.

## Implementation artifacts

Implement:

```text
OCR_DISABLED
OCR_IF_NEEDED
OCR_FORCE
```

Candidate scopes:

```text
PAGE
REGION
EMBEDDED_IMAGE
```

Add:

- page-at-a-time rendering;
- ru/kk/en mixed OCR;
- model manifest/checksum verification;
- offline runtime mode;
- Nexus preload/init workflow;
- native/OCR reconciliation;
- duplicate suppression;
- confidence/provenance recording;
- bounded OCR concurrency.

CPU is the required portable baseline. GPU is optional and added only after CPU correctness.

## Definition of Done

- native pages are not redundantly OCRed;
- scanned pages become searchable text candidates;
- mixed pages use selective OCR;
- Kazakh characters are preserved;
- missing/wrong model prevents OCR readiness;
- no document-time model download occurs.

## Mandatory verification

OCR golden corpus across:

```text
ru
kk
en
ru+kk
ru+en
kk+en
ru+kk+en
```

including Kazakh-specific letters and native/OCR duplicate tests.

---

# M6 — Normalization and multilingual logical splitter

## Goal

Produce deterministic semantic fragments without taking over AstraVector tokenizer/chunk ownership.

## TZ scope

TZ-07, TZ-08, TZ-09, TZ-17.

## Implementation artifacts

### Normalizer

Implement:

- Unicode NFC;
- element-aware whitespace cleanup;
- conservative dehyphenation;
- protected-span detection;
- page furniture suppression;
- preservation of tables/lists/code;
- no translation/transliteration/spell rewriting.

### Splitter

Implement structure-first boundaries:

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

Initial sizing baseline:

```text
min_chars       800
target_chars    5000
soft_max_chars  8000
hard_max_chars 12000
```

These values remain calibration inputs, not permanent constants.

Produce deterministic `LogicalFragment[]` with:

- numeric documentVersion;
- fragment IDs;
- normalizedText;
- contextPrefix;
- hierarchy;
- language metadata;
- provenance;
- split reason.

## Definition of Done

- no BGE-M3 tokenizer is loaded by AstraIndexator runtime;
- splitter is deterministic under identical processing fingerprint;
- legal/list/table context is preserved;
- blind raw overlap is disabled;
- mixed language remains intact when structurally coherent.

## Mandatory verification

Normalizer protected-span corpus and multilingual splitter golden suite, including table/list/OCR/legal/technical cases.

---

# M7 — Prepared artifacts and replay

## Goal

Create durable canonical processing output so downstream delivery and recovery do not require expensive parse/OCR replay.

## TZ scope

TZ-03, TZ-09, TZ-13.

## Implementation artifacts

Prepared artifact layout SHALL use sharded bounded files, e.g.:

```text
manifest.json
parts/elements-00000.jsonl
parts/fragments-00000.jsonl
...
```

Manifest includes:

```text
schemaVersion
documentId
documentVersion
sourceSha256
processingFingerprint
parser/ocr/normalizer/splitter versions
parts with sha256/bytes/recordCount
createdAt
```

Publication protocol:

```text
write staging parts
verify hashes/counts
publish parts
publish manifest last
persist manifest reference under current fence
```

## Definition of Done

- manifest is the commit marker;
- incomplete/corrupt artifacts are never consumed;
- worker restart can replay from prepared data;
- stale worker cannot install a prepared checkpoint after losing lease;
- parts can be streamed with bounded memory.

## Mandatory verification

Crash after part write, crash before manifest, corrupt part, stale checkpoint, orphan cleanup and replay tests.

---

# M8 — LogicalBlock mapper and AstraVector ingestion adapter

## Goal

Implement the actual integration boundary to `alimbetov/llm2`.

## TZ scope

TZ-09, TZ-10, TZ-11, TZ-12, TZ-13, TZ-17.

## Implementation artifacts

### Generated client

Use generated protobuf classes from the actual AstraVector proto revision.

Do not hand-maintain duplicate wire DTOs.

### LogicalBlock mapper

Map canonical fragments/elements to:

```text
LogicalBlock
  block_id
  parent_block_id
  block_type
  text
  order_index
  source_location
  source_links
  metadata
```

Validate:

```text
one DOCUMENT root
unique block IDs
valid parent references
acyclic hierarchy
nonblank required text
deterministic order
uint32 wire range guards
```

### Access Zone adapter

Input uses one approved canonical/subdivision `AccessZoneRef`.

Persist both where available:

```text
requested accessZoneCode
resolved accessZoneId
```

### Small document path

Implement `IndexLogicalDocument` where appropriate.

### Large document path

Implement:

```text
StartLogicalDocumentIngestion
AppendLogicalDocumentBlocks x N
FinalizeLogicalDocumentIngestion
GetLogicalDocumentIngestionStatus
GetDocumentVectorStatus
```

Persist session and batch checkpoints.

## P0 gate

Before strict session ingestion production readiness, resolve byte-exact:

```text
batch_content_hash
final_content_hash
```

using shared Rust/Python golden fixtures. AstraIndexator SHALL NOT invent its own serialization.

## Definition of Done

- real AstraVector receives deterministic LogicalBlock trees;
- Access Zone leading zeroes survive end-to-end;
- Start/Append/Finalize retries reuse same logical identities;
- session COMPLETED is not treated as document completion;
- local completion occurs only after `searchable=true`.

## Mandatory verification

Real/fake gRPC contract tests plus at least one real AstraVector E2E for small and large documents.

---

# M9 — Lifecycle, reconciliation and Knowledge Inventory

## Goal

Make document/indexing state operationally trustworthy.

## TZ scope

TZ-12, TZ-13, TZ-14.

## Implementation artifacts

Implement:

- version lifecycle;
- cancellation;
- async delete orchestration;
- session reconciliation;
- vector-status reconciliation;
- dead-letter/operator requeue flow;
- reconciliation worker;
- Knowledge Inventory projection.

Inventory should expose at least:

```text
documentId
documentVersion
externalRevision
jobId
source hash/format
processing fingerprint
knowledgeType
accessZoneCode
resolved accessZoneId
job status/stage
fragment/block counts
ingestion session state
AstraVector document state
searchable
sync evidence
TTL state
lastVerifiedAt
```

TTL states:

```text
UNKNOWN
INHERITED_UNRESOLVED
FINITE
NEVER_EXPIRES
EXPIRED
```

Do not compute inherited remaining lifetime locally when AstraVector does not expose authoritative effective expiry.

## Definition of Done

- inconsistent local/downstream states are visible and reconcilable;
- failed new version leaves old searchable version intact;
- deletion never talks directly to Qdrant;
- Inventory never claims freshness newer than its evidence.

## Mandatory verification

TZ-12/TZ-13 lifecycle and lost-ACK scenarios plus Inventory inconsistency tests.

---

# M10 — Internal API, health, observability and Smoke Test API

## Goal

Expose operator-safe control and verification surfaces.

## TZ scope

TZ-14, TZ-16, TZ-17, TZ-18.

## Implementation artifacts

Implement:

```text
GET  /internal/health/live
GET  /internal/health/ready
GET  /internal/health/capabilities

GET  /internal/v1/knowledge
GET  /internal/v1/knowledge/{documentId}
GET  /internal/v1/jobs/{jobId}
GET  /internal/v1/jobs/{jobId}/attempts
GET  /internal/v1/jobs/{jobId}/events

POST /internal/v1/smoke-tests
GET  /internal/v1/smoke-tests/{smokeTestId}
POST /internal/v1/smoke-tests/{smokeTestId}/cleanup
```

Smoke profiles:

```text
DEPENDENCIES
PIPELINE
E2E_RETRIEVAL
OCR_E2E
RECOVERY_E2E   # verification env only
```

Smoke configuration must use a dedicated approved Access Zone and normal production job path.

Add:

- Prometheus metrics;
- OpenTelemetry tracing;
- structured lifecycle events;
- high-cardinality guardrails;
- content/secret redaction.

## Definition of Done

- liveness is dependency-light;
- readiness is role/capability aware;
- smoke is not used as a K8s probe;
- smoke can prove source -> searchable -> retrieve -> cleanup;
- mutating smoke APIs are internal-only/config-enabled.

## Mandatory verification

Full TZ-17 smoke and telemetry safety tests.

---

# M11 — Recovery hardening, multi-replica proof and performance/RAG calibration

## Goal

Prove that the implementation actually satisfies the architecture under failure and load.

## TZ scope

TZ-13, TZ-17.

## Mandatory failure suite

```text
worker crash during OCR
worker crash after prepared publication
Start ACK lost
Append ACK lost
Finalize ACK lost
stale worker resumes after reclaim
PostgreSQL outage / ownership uncertainty
SeaweedFS outage
AstraVector unavailable
cancellation during active session
failed new version keeps previous searchable version
retry exhausted -> DEAD_LETTER
```

## Performance/resource suite

Measure:

```text
queue wait
source throughput
parser latency
OCR pages/sec
normalization
splitter
prepared publication
AstraVector ingestion
searchability latency
end-to-end latency
memory
workspace disk
```

Test workload classes:

```text
many small documents
few large documents
mixed workload
OCR-heavy workload
AstraVector slowdown
SeaweedFS slowdown
PostgreSQL contention
```

## RAG quality calibration

Compare:

```text
no logical fragmentation
naive fixed-size fragmentation
structure-aware fragmentation
```

Metrics:

```text
Recall@K
MRR
nDCG
citation correctness
identifier retrieval
duplicate-context rate
```

Corpus includes RU, KK, EN, mixed, OCR, legal, tables, lists and technical identifiers.

## Definition of Done

- mandatory recovery scenarios pass;
- resource limits are evidence-based;
- splitter calibration has real AstraVector tokenizer evidence;
- processing changes are covered by retrieval no-regression proof.

---

# M12 — Production packaging and operations

## Goal

Package the verified implementation for standalone/container/Kubernetes operation.

## TZ scope

TZ-15, TZ-16, TZ-18.

## Runtime roles

```text
CONTROL
WORKER_CPU
WORKER_GPU
ALL
```

Initial production recommendation:

```text
CONTROL       optional separate deployment
WORKER_CPU    required N replicas
WORKER_GPU    optional after verified need
```

## Kubernetes artifacts

Create:

```text
Deployment astra-indexator-control
Deployment astra-indexator-worker-cpu
Deployment astra-indexator-worker-gpu   optional
Service astra-indexator-internal
Job astra-indexator-db-migrate
model preload init/job
ConfigMap
Secret references
ServiceMonitor/PodMonitor
NetworkPolicy
PDB
HPA/KEDA only when evidence justifies it
```

## Operational requirements

- controlled serialized migrations;
- model preload/checksum before readiness;
- graceful SIGTERM drain;
- no new claims while draining;
- bounded termination behavior;
- immutable image digest;
- rollout/canary/smoke;
- rollback with known image/config/model revisions;
- backup/restore of AstraIndexator-owned PostgreSQL + SeaweedFS state;
- runbooks from TZ-18.

## Definition of Done

Release candidate passes:

```text
migration
readiness
DEPENDENCIES smoke
PIPELINE smoke
E2E_RETRIEVAL smoke
OCR_E2E when applicable
rollback smoke
backup/restore reconciliation proof
```

---

# 5. Recommended implementation order and dependencies

```text
M0 Foundation
 ↓
M1 Contracts + DB
 ↓
M2 Coordinator
 ↓
M3 SeaweedFS + Acquisition
 ↓
M4 Parser
 ↓
M5 OCR
 ↓
M6 Normalizer + Splitter
 ↓
M7 Prepared Artifacts
 ↓
M8 AstraVector Integration
 ↓
M9 Lifecycle + Inventory
 ↓
M10 Internal API + Smoke + Observability
 ↓
M11 Recovery + Performance + RAG Proof
 ↓
M12 Production Deployment
```

Some work may proceed in parallel after contracts freeze:

```text
M4 Parser       ┐
M5 OCR          ├─ after M1/M3 foundations
M8 gRPC adapter ┘  can begin contract tests before full parser exists
```

But no downstream integration PR may bypass canonical TZ-09 LogicalBlock mapping.

---

# 6. Suggested PR stack

A practical initial PR sequence:

```text
PR-01 project-foundation
PR-02 canonical-domain-and-migrations
PR-03 job-coordinator
PR-04 seaweedfs-acquisition-validation
PR-05 parser-native-baseline
PR-06 ocr-cpu-model-supply
PR-07 normalization-splitter
PR-08 prepared-artifacts
PR-09 astravector-proto-adapter
PR-10 lifecycle-reconciliation-inventory
PR-11 internal-api-observability-smoke
PR-12 recovery-performance-rag
PR-13 deployment-operations
```

Large milestones SHOULD be split further if review becomes difficult, but dependency order should remain intact.

---

# 7. Definition of Done applied to every PR

Every implementation PR SHALL include:

1. explicit TZ references;
2. no undocumented contract changes;
3. unit/component tests for changed invariants;
4. migration when persistence schema changes;
5. deterministic fixtures where output identity changes;
6. observability for new failure paths;
7. documented error classification;
8. no secret/content logging;
9. backward/recovery behavior where durable state is affected;
10. evidence that the PR does not introduce direct Qdrant/AstraVector DB access.

---

# 8. Production blockers to track explicitly

The following remain gates, not reasons to delay all implementation:

## P0-A — Cross-language session hashing

Need official golden canonical bytes/digests for:

```text
batch_content_hash
final_content_hash
```

Implementation can proceed with adapter structure, but production strict session ingestion is blocked until parity is proven.

## P0-B — Effective TTL observability

Current public AstraVector document status does not expose authoritative effective document expiry/never-expire sufficiently for exact remaining-lifetime reporting.

Indexing is not blocked; exact Inventory lifetime display remains `INHERITED_UNRESOLVED/UNKNOWN` until downstream contract is extended.

---

# 9. Recommended first implementation target

The first functional target should NOT be OCR or RAG splitting.

The correct first vertical slice is:

```text
Spring Boot-like test producer
  -> SeaweedFS immutable tiny TXT/PDF source
  -> PostgreSQL PENDING job
  -> one worker claim with lease/fencing
  -> minimal parser
  -> deterministic LogicalBlock tree
  -> fake AstraVector adapter
  -> local completion/retry semantics
```

This slice proves the backbone before expensive parser/OCR work.

Then replace the fake adapter with real AstraVector and expand supported document processing stages incrementally.

---

# 10. Final implementation readiness rule

AstraIndexator 1.0 implementation is considered complete only when this end-to-end invariant is executable:

```text
approved Access Zone assigned at upload
+
immutable source
+
durable job
+
safe multi-replica claim
+
validated acquisition
+
structural parse
+
conditional OCR
+
deterministic normalization/splitting
+
replayable prepared artifact
+
LogicalBlock[] mapping
+
idempotent AstraVector delivery
+
searchable=true proof
+
retrieval evidence
+
Knowledge Inventory visibility
+
normal lifecycle cleanup
+
crash/recovery proof
```

Documentation completion alone is not implementation completion.
