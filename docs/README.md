# AstraIndexator 1.0 — Design Documentation

This directory contains the canonical architecture and technical specifications for AstraIndexator 1.0.

## Design principle

The system is decomposed by responsibility and contract boundary. Each subsystem specification must define purpose/ownership, inputs/outputs, persistence and DTO contracts, concurrency/idempotency, recovery, observability, security/access scope and testable acceptance criteria.

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
| TZ-08 | Multilingual Logical Splitter | Structure-aware, tokenizer-calibrated logical fragmentation | Baseline |
| TZ-09 | Canonical Document Model | ParsedDocument, DocumentElement, LogicalFragment, provenance, deterministic IDs | Baseline |
| TZ-10 | Access Zones & TTL | AstraVector-compatible zone selectors and registry-owned TTL semantics | Baseline |
| TZ-11 | AstraVector Integration | Public ingestion facade, LogicalBlock mapping, sessions, idempotency/readiness | Baseline |
| TZ-12 | Document Lifecycle | New versions/reindex/delete/cancel/expiry/searchability semantics | Baseline |
| TZ-13 | Reliability & Recovery | Crash recovery, replay, reconciliation, dead-letter and fencing | Baseline |
| TZ-14 | Observability & Knowledge Inventory | Logs, metrics, traces, health, audit, loaded-knowledge inventory and lifetime visibility | Baseline |
| TZ-15 | Configuration & Model Delivery | Typed config, Nexus artifact supply, model manifests/checksums, offline runtime, rollout/rollback | Baseline |
| TZ-16 | Internal Trust Boundary & Secrets | Internal-service trust model, approved storage/network boundaries, secret handling and no privilege broadening | Baseline |
| TZ-17 | Testing & Verification | Executable architecture proof: unit/integration/E2E/recovery/RAG quality plus internal Smoke Test API | Baseline |
| TZ-18 | Deployment & Operations | Docker/Kubernetes readiness, scaling and runbooks | Planned |

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

AstraIndexator owns acquisition, parsing, OCR, normalization and semantic logical fragmentation. AstraVector owns tokenizer/model execution, searchable chunking, embeddings, vector state, Qdrant projection, effective TTL and retrieval.

## Core runtime invariants

### Coordinator

```text
at-least-once processing
+
FOR UPDATE SKIP LOCKED
+
renewable lease
+
monotonic lease_generation fencing
+
idempotent/reconcilable downstream effects
```

Canonical local lifecycle:

```text
PENDING
  -> PROCESSING
      -> COMPLETED
      -> RETRY_WAIT -> PROCESSING
      -> FAILED
      -> DEAD_LETTER
      -> CANCELLED
```

A stale worker whose lease generation was superseded cannot authoritatively finalize or overwrite current state.

### Storage and acquisition

SeaweedFS stores immutable source and replayable prepared artifacts; PostgreSQL is coordinator authority.

```text
SOURCE -> immutable bytes
PREPARED -> manifest.json + bounded JSONL parts
LOCAL WORKSPACE -> ephemeral attempt-scoped files
```

`manifest.json` is the prepared-artifact commit marker. Source acquisition is bounded/streaming and establishes actual byte count plus SHA-256 before parser admission.

### Processing plane

```text
AcquiredSource
  -> TZ-05 Parser
  -> TZ-06 OCR enrichment
  -> TZ-07 Normalization
  -> TZ-08 Logical Splitter
  -> TZ-09 Canonical LogicalFragment/LogicalBlock mapping
```

Parser reconstructs structure/reading order rather than flat text. OCR defaults to `OCR_IF_NEEDED`, supports baseline `ru/kk/en`, suppresses native/OCR duplication and uses locally verified model bundles. Normalization follows **normalize representation, not meaning**. AstraVector remains responsible for tokenizer/model-aware searchable chunking.

### Configuration and model delivery

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

### Access zone and TTL

```text
accessZoneCode = exactly four ASCII digits 0000..9999
accessZoneId   = UUID-backed identity
```

One indexed document version belongs to exactly one effective ingestion zone. AstraVector Access Zone Registry owns authoritative zone resolution and effective TTL. `ttl_days=0` means inherit effective zone/platform policy; session expiry is not document expiry.

### AstraVector integration/lifecycle

```text
LogicalBlock stream
  -> IndexLogicalDocument
     OR Start / Append x N / Finalize
  -> GetLogicalDocumentIngestionStatus
  -> GetDocumentVectorStatus
  -> searchable=true
  -> local COMPLETED
```

Mutating RPC timeouts are ambiguous outcomes and are reconciled before replay/replacement. New versions build without destroying the previous searchable version. Delete is asynchronous through AstraVector facade; AstraIndexator never mutates Qdrant directly.

### Reliability

```text
reclaim expired lease
  -> validate fencing generation
  -> load durable checkpoints
  -> validate source/prepared compatibility
  -> reconcile AstraVector state
  -> resume earliest safe stage
  -> prove searchable or enter terminal failure/dead-letter
```

Finite retry budgets are mandatory.

### Observability and Knowledge Inventory

TZ-14 makes loaded knowledge queryable: document/version/source hash, processing fingerprint, access zone, counts, AstraVector state, `searchable`, freshness and lifecycle visibility.

Exact remaining knowledge lifetime is shown only from authoritative downstream effective expiry/never-expire state. `GetLogicalDocumentIngestionStatus.expires_at` is session lifetime and is never presented as document TTL.

### Internal trust boundary

AstraIndexator is an internal service and intentionally does not implement end-user OAuth2/OIDC/JWT/RBAC in 1.0. It accepts only approved internal storage/network paths, externalizes least-privilege secrets, does not broaden access zones, and has no direct AstraVector PostgreSQL/Qdrant dependency.

## Testing & verification invariant

TZ-17 converts the architectural baseline into executable evidence.

Verification layers are:

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

The minimum production proof is not merely that a worker is alive. It is:

```text
known immutable fixture
  -> normal durable PostgreSQL job
  -> normal worker claim/lease path
  -> acquisition/parser/OCR/normalization/splitting
  -> prepared artifact
  -> AstraVector ingestion
  -> searchable=true
  -> RetrieveContext returns expected marker/citation
  -> Knowledge Inventory reflects the result
  -> normal lifecycle cleanup succeeds or remains reconcilably pending
```

### Internal Smoke Test API

The approved control surface is conceptually:

```http
POST /internal/v1/smoke-tests
GET  /internal/v1/smoke-tests/{smokeTestId}
POST /internal/v1/smoke-tests/{smokeTestId}/cleanup
```

Smoke profiles include `DEPENDENCIES`, `PIPELINE`, `E2E_RETRIEVAL`, `OCR_E2E` and test-environment-only `RECOVERY_E2E`.

The Smoke API is **not** a second ingestion API. It uses allowlisted immutable fixtures, a configured dedicated smoke access zone and reserved document namespace, creates a normal durable job, waits/reconciles through normal production states and performs cleanup through normal AstraVector lifecycle APIs. It does not accept arbitrary caller-provided URLs, zones, models or document text.

Full E2E smoke is mutating/expensive and MUST NOT be used as Kubernetes liveness/readiness. Ordinary health probes remain non-mutating.

TZ-17 also makes shared Rust/Python golden vectors for AstraVector session hashing and deterministic document-version mapping explicit production gates where those contracts are required.

## Current design status

The architecture and verification baseline is now complete through TZ-17:

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
```

Only the operational packaging/deployment specification remains:

```text
TZ-18 Deployment & Operations
```

TZ-18 SHALL turn these contracts into Docker/Kubernetes topology, probes, resource classes, worker pools, init/preload model flow, scaling, migrations, rollout/rollback and operational runbooks without weakening TZ-17 verification gates.
