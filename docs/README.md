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
| TZ-15 | Configuration & Model Delivery | Nexus model delivery, config schema, startup validation | Planned |
| TZ-16 | Security | Trust boundary, secrets, storage/network policies, authorization | Planned |
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

A support/operator view must answer:

```text
WHAT is loaded?
  -> document/version/source hash/format/processing fingerprint

WHERE is it scoped?
  -> accessZoneId/accessZoneCode

IS it usable?
  -> AstraVector operation state + searchable + sync evidence

HOW MUCH was produced?
  -> pages/elements/fragments/blocks/bindings/vectors

WHEN was it loaded?
  -> accepted/searchable/completed/last-verified timestamps

HOW LONG does it remain?
  -> authoritative effectiveExpiresAt - now
     OR authoritative NEVER_EXPIRES
     OR honest UNKNOWN / INHERITED_UNRESOLVED
```

The current AstraVector `GetDocumentVectorStatus` exposes operation state, progress, `searchable` and vector/outbox/Qdrant synchronization evidence, but not authoritative document effective expiry. Therefore exact remaining-lifetime display requires a stable AstraVector service-level `effective_expires_at`/never-expire lifecycle field. AstraIndexator MUST NOT solve this by querying AstraVector PostgreSQL directly or by recalculating the access-zone TTL matrix locally.

`GetLogicalDocumentIngestionStatus.expires_at` is session lifetime and MUST NOT be displayed as knowledge TTL.

Knowledge Inventory is a denormalized operational projection, not a lifecycle source of truth. It exposes freshness (`lastVerifiedAt`, `FRESH/STALE/UNVERIFIED/DOWNSTREAM_UNAVAILABLE`) and reconciles against supported AstraVector APIs.

Prometheus labels remain bounded-cardinality; document/job/session IDs belong in structured logs/traces/Inventory rather than metric labels. Raw document/OCR/embedding text and credentials are excluded from normal telemetry.

## Current design status

The full functional/control/observability baseline is now:

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
TZ-10 Access Zones & TTL                   ✅
TZ-11 AstraVector Integration              ✅
TZ-12 Document Lifecycle                   ✅
TZ-13 Reliability & Recovery               ✅
TZ-14 Observability & Knowledge Inventory  ✅
```

Remaining cross-cutting specifications:

```text
TZ-15 Configuration & Model Delivery
  -> TZ-16 Security
  -> TZ-17 Testing & Verification
  -> TZ-18 Deployment & Operations
```

TZ-17 SHALL turn the acceptance criteria and golden failure/quality/TTL/observability scenarios from TZ-03..TZ-14 into executable evidence.
