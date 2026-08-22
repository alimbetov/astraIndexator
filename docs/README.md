# AstraIndexator 1.0 — Design Documentation

This directory contains the canonical architecture and technical specifications for AstraIndexator 1.0.

## Design principle

The system is decomposed by responsibility and contract boundary. Each subsystem specification must define purpose/ownership, inputs/outputs, persistence and DTO contracts, concurrency/idempotency, recovery, observability, trust scope and testable acceptance criteria.

No production implementation should introduce a contract that contradicts these specifications without first updating the relevant specification and recording the decision.

## Technical specifications

| ID | Subsystem | Primary responsibility | Status |
|---|---|---|---|
| TZ-00 | System Architecture | Overall boundaries, terminology, invariants, end-to-end flow | Baseline |
| TZ-01 | Indexation Request & Job Contract | Spring Boot → PostgreSQL durable job contract and document identity propagation | Baseline |
| TZ-02 | Job Coordinator & PostgreSQL | Durable queue, multi-replica claim/lease/fencing, retries, attempts and state machine | Baseline |
| TZ-03 | Object Storage / SeaweedFS | Immutable source, sharded prepared artifacts, manifest publication, retention/recovery | Baseline |
| TZ-04 | File Validation & Acquisition | Bounded acquisition, SHA-256, type detection, hostile-container/image guards | Baseline |
| TZ-05 | Document Parser | Structure reconstruction, reading order, native extraction, tables/images and OCR handoff | Baseline |
| TZ-06 | OCR Pipeline | Page/region-aware OCR, ru/kk/en, native/OCR reconciliation, versioned model contract | Baseline |
| TZ-07 | Text Normalization | Deterministic multilingual-safe cleanup with protected spans and provenance | Baseline |
| TZ-08 | Multilingual Logical Splitter | Structure-aware, tokenizer-calibrated logical fragmentation mapped through TZ-09 `LogicalBlock[]` | Baseline |
| TZ-09 | Canonical Document Model | ParsedDocument, DocumentElement, LogicalFragment, LogicalBlock mapping, provenance, deterministic IDs | Baseline |
| TZ-10 | Access Zones & TTL | AstraVector-compatible zone selectors, canonical `0000–0999` knowledge catalog and registry-owned TTL semantics | Baseline |
| TZ-11 | AstraVector Integration | Public ingestion facade, LogicalBlock mapping, sessions, idempotency/readiness | Baseline |
| TZ-12 | Document Lifecycle | Numeric versions, reindex/delete/cancel/expiry/searchability semantics | Baseline |
| TZ-13 | Reliability & Recovery | Crash recovery, replay, reconciliation, dead-letter and fencing | Baseline |
| TZ-14 | Observability & Knowledge Inventory | Logs, metrics, traces, health, audit, loaded-knowledge inventory and lifetime visibility | Baseline |
| TZ-15 | Configuration & Model Delivery | Typed config, Nexus artifact supply, model manifests/checksums, offline runtime, rollout/rollback | Baseline |
| TZ-16 | Internal Trust Boundary & Secrets | Internal-service trust model, approved storage/network boundaries, secret handling and no privilege broadening | Baseline |
| TZ-17 | Testing & Verification | Executable architecture proof: unit/integration/E2E/recovery/RAG quality plus internal Smoke Test API | Baseline |
| TZ-18 | Deployment & Operations | Portable/Kubernetes topology, probes, model preload, resource classes, scaling, migrations, rollout/rollback, DR and runbooks | Baseline |

## Canonical system boundary

```text
Spring Boot
    |
    +--> SeaweedFS immutable source object
    |
    `--> PostgreSQL PENDING job
            |
            v
      AstraIndexator replicas
            |- atomic claim / lease / fencing
            |- source acquisition + validation
            |- structure-aware parsing
            |- conditional page/region OCR
            |- deterministic normalization
            |- multilingual logical splitting
            |- LogicalFragment -> LogicalBlock mapping
            |- prepared artifact publication
            |- AstraVector delivery/reconciliation
            `- Knowledge Inventory projection
                    |
                    v
        AstraVectorIngestionFacade
            |- tokenizer-aware chunking
            |- BGE-M3 dense/sparse
            |- PostgreSQL canonical vector state
            |- outbox/Qdrant projection
            |- activation/TTL/reconciliation
            `- retrieval
```

AstraIndexator owns acquisition, parsing, OCR, normalization, semantic logical fragmentation and deterministic mapping into the public `LogicalBlock[]` ingestion tree. AstraVector owns tokenizer/model execution, searchable chunking, embeddings, vector state, Qdrant projection, effective TTL and retrieval.

## Canonical identity contract

AstraIndexator 1.0 uses one positive numeric immutable `documentVersion` end-to-end:

```text
documentId
!= documentVersion (>0, numeric)
!= externalRevision (optional metadata)
!= jobId
!= processingAttemptId
!= fragmentId
!= LogicalBlock.blockId
!= ingestionSessionId
!= AstraVector generated chunk IDs
```

Current AstraVector wire widths differ by message (`uint64` for DocumentIdentity/DocumentRef, `uint32` for session Start), therefore range validation belongs at the adapter boundary. Opaque source revisions such as `rev-17` are stored as metadata and are not a second canonical version identity.

## Core correctness model

```text
immutable source
+
durable PostgreSQL coordination
+
at-least-once processing
+
FOR UPDATE SKIP LOCKED
+
renewable lease
+
monotonic lease_generation fencing
+
replayable prepared artifacts
+
versioned processing/model identity
+
idempotent/reconcilable AstraVector delivery
+
observable knowledge lifecycle
+
executable verification
```

A stale worker whose lease generation was superseded cannot authoritatively finalize or overwrite current state.

## Processing plane

```text
AcquiredSource
  -> TZ-05 Parser
  -> TZ-06 OCR enrichment
  -> TZ-07 Normalization
  -> TZ-08 Logical Splitter
  -> LogicalFragment[]
  -> TZ-09 LogicalBlockMapper
  -> LogicalBlock[]
  -> TZ-11 AstraVectorIngestionFacade
```

Parser reconstructs structure/reading order rather than flat text. OCR defaults to `OCR_IF_NEEDED`, supports baseline `ru/kk/en`, suppresses native/OCR duplication and uses locally verified model bundles. Normalization follows **normalize representation, not meaning**. AstraVector remains responsible for tokenizer/model-aware searchable chunking.

Legacy `CreateMultiGranularityChunks`, `SOURCE`, `PARENT`, `SUB_180` and `SUB_260` vocabulary is not the AstraIndexator 1.0 public integration boundary.

## Configuration and model delivery

Production OCR model flow:

```text
approved immutable revision
  -> https://nexus.astrabase.asia
  -> explicit image-build/init-container/preload
  -> manifest + SHA-256 verification
  -> immutable local model directory
  -> startup/readiness validation
  -> document-time offline inference
```

Runtime workers do not download models on demand and do not silently fall back to a different model/runtime/device. Model identity participates in processing fingerprint.

## Access zone, TTL and knowledge visibility

Wire/registry code contract:

```text
accessZoneCode = exactly four ASCII digits 0000..9999
accessZoneId   = UUID-backed identity
```

Leading zeroes are significant; `0001` is a string code and MUST NOT become integer `1`.

AstraIndexator 1.0 additionally defines ten canonical knowledge-zone root codes inside the valid `0000–0999` range:

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

Each root reserves its `xx00–xx99` family for future explicit subdivisions. Normal catalog-mode ingestion assigns exactly one approved root/subdivision at producer upload/job-creation time. AstraIndexator validates and preserves the code but does not infer it from document contents.

Catalog approval and AstraVector Registry validity are separate requirements:

```text
catalog-approved
+
AstraVector Registry ACTIVE
=
eligible ingestion zone
```

The catalog is an ingestion taxonomy/allowlist, not a replacement for AstraVector Registry or `AccessLevel`.

All ten canonical roots currently fall inside the real `llm2` `0000–0999` matrix. `ttl_days=0` still means **inherit effective zone/platform policy**, never a locally computed forever/expiration decision. AstraVector Access Zone Registry owns authoritative zone resolution and effective TTL.

One indexed document version belongs to exactly one effective ingestion zone. Retrieval may use multiple zones according to AstraVector read contracts.

Knowledge Inventory exposes what is loaded, document/version/source identity, processing fingerprint, access zone, optional knowledge type/catalog version, counts, AstraVector state, `searchable`, freshness and lifecycle visibility. Exact remaining knowledge lifetime is shown only from authoritative downstream effective expiry/never-expire state.

## Reliability and lifecycle

```text
reclaim expired lease
  -> validate fencing generation
  -> load durable checkpoints
  -> validate source/prepared compatibility
  -> reconcile AstraVector state
  -> resume earliest safe stage
  -> prove searchable or enter terminal failure/dead-letter
```

Mutating AstraVector RPC timeouts are ambiguous outcomes and are reconciled before replay/replacement. New versions build without destroying the previous searchable version. Delete is asynchronous through AstraVector facade; AstraIndexator never mutates Qdrant directly.

Canonical indexing processing stages are owned by TZ-02 and reused by TZ-12/TZ-14/TZ-17 rather than duplicated as competing enums.

## Internal trust boundary

AstraIndexator is an internal service and intentionally does not implement end-user OAuth2/OIDC/JWT/RBAC in 1.0. It accepts only approved internal storage/network paths, externalizes least-privilege secrets, does not broaden access zones, and has no direct AstraVector PostgreSQL/Qdrant dependency.

## Testing and smoke proof

TZ-17 converts the architecture into executable evidence:

```text
unit
 -> component
 -> PostgreSQL/SeaweedFS integration
 -> multi-replica race tests
 -> crash/recovery/fault injection
 -> real AstraVector E2E
 -> retrieval/RAG-quality proof
 -> deployed smoke proof
```

Mandatory consistency proofs now include:

```text
positive numeric documentVersion + wire range guards
LogicalFragment[] -> deterministic LogicalBlock[] mapping
all ten canonical Access Zone roots
leading-zero preservation
catalog approval + Registry ACTIVE dual validation
0000–0999 ttl_days=0 inheritance without local expiry calculation
canonical TZ-02 processing-stage vocabulary
```

The internal Smoke Test API is conceptually:

```http
POST /internal/v1/smoke-tests
GET  /internal/v1/smoke-tests/{smokeTestId}
POST /internal/v1/smoke-tests/{smokeTestId}/cleanup
```

Smoke profiles include `DEPENDENCIES`, `PIPELINE`, `E2E_RETRIEVAL`, `OCR_E2E` and test-environment-only `RECOVERY_E2E`. Smoke is not a second ingestion API; it drives the normal durable production path using reserved immutable fixtures and performs cleanup through normal lifecycle APIs. Mutating smoke endpoints are internal-only and should be enabled explicitly.

## Deployment and operations invariant

TZ-18 keeps correctness independent of deployment technology. The same contracts apply to standalone, Docker and Kubernetes execution.

Recommended production topology separates roles when scale requires it:

```text
astra-indexator-control
astra-indexator-worker-cpu x N
astra-indexator-worker-gpu x N   # optional
```

PostgreSQL, SeaweedFS, Nexus and AstraVector remain external dependencies. Model artifacts are provisioned before worker readiness and mounted/consumed as verified local state.

Kubernetes probes are role-aware:

```text
liveness  -> process health only
readiness -> ability to accept the configured role/capability
smoke     -> explicit post-deploy verification, never liveness/readiness
```

Horizontal scale uses PostgreSQL claim/lease/fencing for correctness. Autoscaling should prefer backlog age/depth and capacity/latency signals rather than CPU alone. SIGTERM first stops new claims, then drains/checkpoints; unfinished work remains recoverable through lease expiry/reclaim.

Database migrations are controlled/serialized before compatible rollout. Release identity includes image digest, config revision/fingerprint, DB schema compatibility and immutable model revision. Rollback selects known immutable revisions and is followed by reconciliation/smoke.

Production operations require bounded workspace/resources, dependency-aware readiness, dashboards/alerts, backup/restore verification, housekeeping and runbooks for critical dependency, recovery, model, migration, resource and smoke failures.

Kubernetes is a scheduling/scaling mechanism; it is not the source of indexing correctness.

## Design baseline complete

The AstraIndexator 1.0 specification set is now complete:

```text
TZ-00 System Architecture                  ✅
TZ-01 Indexation Request & Job Contract    ✅
TZ-02 Job Coordinator & PostgreSQL         ✅
TZ-03 Object Storage / SeaweedFS           ✅
TZ-04 File Validation & Acquisition         ✅
TZ-05 Document Parser                       ✅
TZ-06 OCR Pipeline                          ✅
TZ-07 Text Normalization                    ✅
TZ-08 Multilingual Logical Splitter         ✅
TZ-09 Canonical Document Model              ✅
TZ-10 Access Zones & TTL                    ✅
TZ-11 AstraVector Integration               ✅
TZ-12 Document Lifecycle                    ✅
TZ-13 Reliability & Recovery                ✅
TZ-14 Observability & Knowledge Inventory  ✅
TZ-15 Configuration & Model Delivery       ✅
TZ-16 Internal Trust Boundary & Secrets    ✅
TZ-17 Testing & Verification                ✅
TZ-18 Deployment & Operations               ✅
```

## Next phase: implementation planning

Documentation completion is not production readiness. The next phase SHALL convert the baseline into an implementation backlog and executable vertical slices.

Recommended implementation order:

```text
1. contracts + PostgreSQL schema/migrations
2. coordinator claim/lease/fencing
3. SeaweedFS acquisition + validation
4. canonical parser model + PDF/DOCX baseline
5. OCR CPU baseline + Nexus preload
6. normalization + logical splitter
7. prepared artifact publication/replay
8. AstraVector integration + reconciliation
9. Knowledge Inventory + health/smoke API
10. multi-replica/recovery verification
11. container/Kubernetes packaging
12. performance/RAG-quality gates + production runbooks
```

Any implementation deviation that changes a baseline contract should update the relevant TZ before becoming the new canonical behavior.
