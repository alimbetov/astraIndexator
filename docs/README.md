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
| TZ-17 | Testing & Verification | Unit/integration/E2E/recovery/performance/RAG-quality proof | Planned |
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

## Coordinator invariant

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

## Storage and acquisition invariant

SeaweedFS stores immutable source and replayable prepared artifacts; PostgreSQL is the coordinator authority.

```text
SOURCE
  -> immutable bytes

PREPARED
  -> manifest.json
  -> elements/part-xxxxx.jsonl
  -> fragments/part-xxxxx.jsonl

LOCAL WORKSPACE
  -> ephemeral attempt-scoped files
```

`manifest.json` is the commit marker and is published last. A prepared artifact becomes authoritative only after a fenced PostgreSQL checkpoint.

Source acquisition is bounded and streaming:

```text
source ref
 -> HEAD/preflight
 -> streamed byte limit + SHA-256
 -> trusted format/container validation
 -> source.validated
 -> AcquiredSource
```

Producer filename/MIME/extension are hints, not parser-routing authority.

## Processing-plane invariant

```text
AcquiredSource
  -> TZ-05 Parser
  -> TZ-06 OCR enrichment
  -> TZ-07 Normalization
  -> TZ-08 Logical Splitter
  -> TZ-09 Canonical LogicalFragment/LogicalBlock mapping
```

Parser reconstructs document structure and reading order rather than flattening documents to plain text. Mixed PDFs are page-aware. Tables/lists/images remain first-class structures.

OCR defaults to `OCR_IF_NEEDED`, supports baseline `ru/kk/en`, uses page/region candidates, suppresses native/OCR duplication and consumes only locally verified model bundles. Target internal model registry: `https://nexus.astrabase.asia` through TZ-15; runtime model downloads are forbidden.

Normalization follows **normalize representation, not meaning**: Unicode NFC, Kazakh letters and `ё` preserved, technical/legal identifiers protected, conservative dehyphenation, no translation/transliteration/spell rewriting.

Logical splitting is multilingual, structure-first and tokenizer-free at runtime. AstraVector remains responsible for model/tokenizer-aware chunking.

## Configuration & model-delivery invariant

TZ-15 separates typed application configuration, secret configuration and immutable model artifacts.

Production OCR model flow is:

```text
approved immutable model revision
  -> https://nexus.astrabase.asia
  -> explicit image-build / init-container / preload step
  -> manifest + SHA-256 verification
  -> immutable local model directory
  -> startup/readiness capability validation
  -> document-time offline inference
```

Nexus is an artifact source, not a document-time inference dependency. Runtime workers SHALL NOT download models on demand and SHALL NOT silently fall back to a different model, runtime or device.

Production model identity includes `modelId`, `artifactRevision`, engine/runtime/device compatibility and checksum/manifest evidence. OCR model revision and preprocessing profile participate in the processing fingerprint so reindex/recovery can distinguish outputs created by different model revisions.

CPU OCR remains the portable baseline; GPU capability is an explicit deployment profile with compatible CUDA/runtime and bounded GPU memory/concurrency. Missing GPU capability cannot silently degrade to CPU inside the same worker profile.

Model artifacts SHOULD be supplied through immutable revision paths rather than mutable `latest` references. Rollback selects a previously approved revision; it does not rewrite files under an existing revision.

Configuration has deterministic precedence and startup validation. Mandatory safety limits, model registry entries and cross-field invariants such as `heartbeat < lease` are validated before readiness. Effective non-secret configuration may be fingerprinted (`effectiveConfigSha256`) for diagnostics, while secret values never enter logs or ordinary manifests.

Nexus credentials SHOULD be scoped to image-build/init/preload components where possible; document-processing workers SHOULD consume verified model directories read-only without holding Nexus credentials.

## Access-zone and TTL invariant

```text
accessZoneCode = exactly four ASCII digits 0000..9999
accessZoneId   = UUID-backed identity
```

One indexed document version belongs to exactly one effective ingestion zone. AstraIndexator does not silently fan-out one indexing job across zones.

AstraVector Access Zone Registry owns authoritative code resolution and effective TTL. The TZ-10 code→TTL matrix is compatibility/reference information and MUST NOT be duplicated as runtime policy in AstraIndexator.

For session ingestion:

```text
ttl_days = 0 -> inherit AstraVector zone/platform policy

ttl_days > 0 -> explicit finite relative lifetime
```

Session expiry and document TTL expiry are different concepts.

## AstraVector integration and lifecycle invariant

AstraIndexator uses generated `astravector.embedding.v1.AstraVectorIngestionFacade` clients.

```text
LogicalBlock stream
  -> IndexLogicalDocument
     OR Start / Append x N / Finalize
  -> GetLogicalDocumentIngestionStatus
  -> GetDocumentVectorStatus
  -> searchable=true
  -> local COMPLETED
```

Mutating RPC timeouts are ambiguous outcomes and are reconciled before replay/replacement. A new document version builds while the previous searchable version remains intact. Delete is asynchronous and goes through AstraVector facade; AstraIndexator never mutates Qdrant directly.

## Reliability invariant

Recovery is state-driven:

```text
reclaim expired lease
  -> validate fencing generation
  -> load durable checkpoints
  -> validate prepared/source compatibility
  -> reconcile AstraVector state
  -> resume earliest safe stage
  -> prove searchable or enter terminal failure/dead-letter
```

Finite retry budgets are mandatory. Compatible prepared artifacts and session checkpoints are reused across crashes.

## Observability & Knowledge Inventory invariant

TZ-14 makes loaded knowledge queryable as an operational read model rather than relying only on logs/Prometheus.

A support/operator view must answer what is loaded, where it is scoped, whether it is searchable, how many fragments/vectors exist, when it became usable, and how long it remains valid.

Exact remaining lifetime is computed only from authoritative downstream `effectiveExpiresAt`; inherited/unknown lifetime is shown honestly as unresolved rather than reconstructed from the local access-zone matrix.

Knowledge Inventory is a denormalized operational projection, not a lifecycle source of truth. It exposes freshness and reconciles against supported AstraVector APIs. Prometheus labels remain bounded-cardinality and raw document/OCR/embedding text is excluded from normal telemetry.

## Internal trust-boundary invariant

TZ-16 intentionally keeps AstraIndexator simple as an internal service.

AstraIndexator 1.0 does **not** implement end-user OAuth2/OIDC/JWT/RBAC and does not require a public upload API for normal processing. Caller/user authentication belongs to upstream platform/gateway boundaries if ever required.

The minimal trust model is:

```text
trusted internal producer/platform
  -> PostgreSQL + SeaweedFS
  -> AstraIndexator internal worker
  -> AstraVector
```

Baseline controls are limited to:

- approved internal source/storage adapters; arbitrary external URL fetching is disabled;
- externalized least-privilege service secrets;
- separation of model-preload/Nexus credentials from runtime worker credentials where practical;
- no access-zone broadening or inference from content;
- no direct AstraVector PostgreSQL or Qdrant access from AstraIndexator;
- internal-only Knowledge Inventory/admin exposure;
- model integrity verification;
- no secret/raw-document leakage in normal telemetry.

NetworkPolicy/firewall/TLS/mTLS remain deployment capabilities for TZ-18 rather than reasons to add application user-authentication machinery.

If AstraIndexator is ever exposed directly to untrusted/public clients, TZ-16 MUST be revised before such deployment is allowed.

## Current design status

The full architecture/cross-cutting baseline is now:

```text
TZ-00 System Architecture                  ✅
TZ-01 Indexation Request & Job Contract    ✅
TZ-02 Job Coordinator & PostgreSQL         ✅
TZ-03 Object Storage / SeaweedFS           ✅
TZ-04 File Validation & Acquisition        ✅
TZ-05 Document Parser                      ✅
TZ-06 OCR Pipeline                         ✅
TZ-07 Text Normalization                   ✅
TZ-08 Multilingual Logical Splitter        ✅
TZ-09 Canonical Document Model             ✅
TZ-10 Access Zones & TTL                    ✅
TZ-11 AstraVector Integration               ✅
TZ-12 Document Lifecycle                    ✅
TZ-13 Reliability & Recovery                ✅
TZ-14 Observability & Knowledge Inventory  ✅
TZ-15 Configuration & Model Delivery       ✅
TZ-16 Internal Trust Boundary & Secrets    ✅
```

Only verification and operations specifications remain:

```text
TZ-17 Testing & Verification
  -> TZ-18 Deployment & Operations
```

TZ-17 SHALL convert the acceptance criteria and golden failure/quality/TTL/observability/configuration/trust-boundary scenarios from TZ-03..TZ-16 into executable evidence.
