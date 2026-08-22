# AstraIndexator 1.0 — Design Documentation

This directory contains the canonical architecture and technical specifications for AstraIndexator 1.0.

## Design principle

The system is decomposed by responsibility and contract boundary. Each subsystem specification must define:

- purpose and ownership;
- inputs and outputs;
- DTOs and persistence contracts;
- state transitions;
- idempotency and concurrency rules;
- retry and recovery semantics;
- observability requirements;
- security/access-zone propagation rules;
- TTL/lifecycle semantics where applicable;
- acceptance criteria and verification evidence.

## Planned technical specifications

| ID | Subsystem | Primary responsibility | Status |
|---|---|---|---|
| TZ-00 | System Architecture | Overall boundaries, terminology, invariants, end-to-end flow | Baseline |
| TZ-01 | Indexation Request & Job Contract | Spring Boot → PostgreSQL durable job contract and document identity propagation | Baseline |
| TZ-02 | Job Coordinator & PostgreSQL | Durable queue, multi-replica claim/lease/fencing, retries, attempts, recovery checkpoints and state machine | Baseline |
| TZ-03 | Object Storage / SeaweedFS | Source and prepared artifact lifecycle and object layout | Planned |
| TZ-04 | File Validation & Acquisition | Download, MIME/type validation, hashing, temporary workspace | Planned |
| TZ-05 | Document Parser | Native PDF/text extraction, page/layout representation | Planned |
| TZ-06 | OCR Pipeline | OCR decision rules, model acquisition/versioning, OCR results | Planned |
| TZ-07 | Text Normalization | Canonical cleanup while preserving provenance and structure | Planned |
| TZ-08 | Multilingual Logical Splitter | Structure-aware, tokenizer-calibrated logical fragmentation before AstraVector | Baseline |
| TZ-09 | Canonical Document Model | ParsedDocument, DocumentElement, LogicalFragment, provenance, deterministic IDs, prepared artifacts | Baseline |
| TZ-10 | Access Zones & TTL | AstraVector-compatible access-zone selectors, one-zone ingestion scope, registry-owned TTL semantics | Baseline |
| TZ-11 | AstraVector Integration | Public ingestion facade, LogicalBlock mapping, single/session ingestion, batching, idempotency, reconciliation and readiness | Baseline |
| TZ-12 | Document Lifecycle | Create/update/reindex/delete/version replacement semantics | Planned |
| TZ-13 | Reliability & Recovery | Crash recovery, poison jobs, dead-letter, reconciliation | Planned |
| TZ-14 | Observability | Logs, metrics, tracing, health/readiness, audit fields | Planned |
| TZ-15 | Configuration & Model Delivery | Nexus model delivery, config schema, startup validation | Planned |
| TZ-16 | Security | Trust boundary, secrets, storage/network policies, validation | Planned |
| TZ-17 | Testing & Verification | Unit/integration/E2E/recovery/performance/RAG-quality acceptance evidence | Planned |
| TZ-18 | Deployment & Operations | Docker, Kubernetes readiness, scaling and operational runbooks | Planned |

## Implementation rule

No production implementation should introduce a contract that contradicts these specifications without first updating the relevant specification and recording the decision.

## Canonical responsibility boundary

AstraIndexator owns document acquisition, parsing, OCR, normalization and **logical semantic fragmentation**. AstraVector owns tokenizer/model execution, tokenizer-aware searchable chunking, dense/sparse embedding generation, canonical vector/document state, Qdrant projection, activation/reconciliation and retrieval.

```text
Spring Boot
    |
    +--> SeaweedFS source object
    |
    `--> PostgreSQL PENDING job
            |
            v
      AstraIndexator replicas
            |- atomic claim/lease/fencing
            |- SeaweedFS acquisition
            |- parsing/layout
            |- conditional OCR
            |- normalization
            `- multilingual logical splitting
                    |
                    v
            canonical logical blocks
                    |
                    v
        AstraVectorIngestionFacade
                    |- tokenizer-aware chunking
                    |- BGE-M3
                    |- dense/sparse projection
                    |- PostgreSQL/Qdrant
                    |- activation/reconciliation
                    `- retrieval
```

## Coordinator invariant

AstraIndexator uses PostgreSQL as the durable queue and distributed coordination authority for one or N active replicas.

The reliability model is:

```text
at-least-once processing
+
short transactional claim with FOR UPDATE SKIP LOCKED
+
renewable lease
+
monotonic lease_generation fencing token
+
append-oriented processing attempts
+
idempotent/reconcilable downstream effects
```

The system does not claim distributed exactly-once execution.

A stale worker whose lease generation was superseded MUST NOT be able to finalize or overwrite the current job state.

Canonical lifecycle:

```text
PENDING
  -> PROCESSING
      -> COMPLETED
      -> RETRY_WAIT -> PROCESSING
      -> FAILED
      -> DEAD_LETTER
      -> CANCELLED
```

`processing_stage` carries progress details such as parsing/OCR/splitting/delivery while top-level status remains stable.

Large-document recovery requires prepared-artifact checkpoints plus persisted downstream/session checkpoints so a crash does not force blind full reprocessing or duplicate downstream ingestion.

## Canonical document-model invariant

The internal semantic data path is:

```text
IndexationJob
  -> SourceObject
  -> ParsedDocument
  -> DocumentElement[]
  -> LogicalFragment[] / AstraVector LogicalBlock mapping
  -> AstraVector public ingestion facade
```

`LogicalFragment` is the stable AstraIndexator semantic source container. It MUST NOT be confused with AstraVector-generated tokenizer-aware chunks or their internal IDs.

The canonical model must preserve:

- `documentId/documentVersion`;
- deterministic `elementId/fragmentId`;
- multilingual content and language metadata;
- page/slide/sheet/layout provenance where available;
- image/OCR origin relationships;
- structured tables/lists;
- `originalText` versus synthetic `contextPrefix` versus downstream embedding representation;
- processing/schema versions needed for deterministic replay and diagnostics.

Prepared artifacts are expected to use a manifest plus streaming-friendly element/fragment collections such as JSONL so downstream delivery can be replayed without reparsing the original binary when the canonical schema version is supported.

## RAG fragmentation invariant

AstraIndexator MUST NOT duplicate AstraVector's embedding-size chunking. Its splitter is structure-first and tokenizer-free at runtime, using deterministic size guards calibrated against the real AstraVector model/tokenizer during verification.

Canonical path:

```text
ParsedDocument
  -> LogicalFragment[]
  -> AstraVector LogicalBlock[]
  -> AstraVector tokenizer-aware chunking
  -> searchable parent/child representations
```

For multilingual content, language switching alone is not a split boundary. Original language content is preserved; automatic translation/transliteration is not part of the indexing pipeline.

## Access-zone and TTL invariant

AstraIndexator mirrors the current AstraVector contract instead of inventing a parallel security/lifecycle model.

```text
accessZoneCode = exactly four ASCII digits: 0000..9999
accessZoneId   = UUID-backed zone identity
```

One indexed document version belongs to exactly one effective access zone. Plural zone selectors are retained for producer/retrieval compatibility, but a single indexing job MUST normalize to one distinct effective zone; AstraIndexator does not silently fan-out one job into multiple zones.

The current AstraVector code-to-default-TTL matrix is documented in TZ-10 for compatibility and verification, but the Access Zone Registry remains the runtime authority. AstraIndexator MUST NOT independently implement the matrix as business policy.

For session ingestion:

```text
ttl_days = 0 -> inherit effective zone/platform policy

ttl_days > 0 -> explicit relative finite lifetime in days
```

`0` MUST NOT be interpreted as unconditional never-expire. AstraVector owns effective expiry, search exclusion, cleanup and Qdrant reconciliation.

## AstraVector integration invariant

AstraIndexator uses the generated client for:

```text
astravector.embedding.v1.AstraVectorIngestionFacade
```

and does not introduce a parallel custom ingestion API.

Canonical integration model:

```text
canonical AstraIndexator model
        -> anti-corruption mapping
        -> LogicalBlock[]
        -> single-call IndexLogicalDocument
           OR
           StartLogicalDocumentIngestion
             -> AppendLogicalDocumentBlocks x N
             -> FinalizeLogicalDocumentIngestion
        -> GetLogicalDocumentIngestionStatus
        -> GetDocumentVectorStatus
        -> searchable=true
        -> AstraIndexator job COMPLETED
```

Mutating RPC timeouts are ambiguous outcomes. Start retries reuse the same logical idempotency key, Append retries reuse the same batch index/hash, and Finalize timeout is reconciled through status before any replacement operation is considered.

Session delivery is deterministic, bounded-memory and checkpointed durably so a reclaimed job can continue without blind full re-ingestion.

Two P0 integration decisions remain explicit rather than guessed:

1. exact cross-language canonicalization/golden vectors for `batch_content_hash` and `final_content_hash`;
2. deterministic mapping of producer-visible `documentVersion` to AstraVector numeric `uint64 document_version` when the producer version is not already numeric.

## Current critical design path

The cross-service contract chain now has baseline specifications for coordination, access/lifecycle and downstream ingestion:

```text
TZ-02 Job Coordinator & PostgreSQL       ✅
TZ-10 Access Zones & TTL                 ✅
TZ-11 AstraVector Integration            ✅
```

The next critical design work is:

```text
TZ-12 Document Lifecycle
  -> TZ-13 Reliability & Recovery
  -> TZ-17 failure/recovery E2E verification
```

TZ-12 should define create/new-version/reindex/delete/replacement semantics against the public AstraVector facade. TZ-13 must then combine TZ-02 fencing, TZ-09 prepared artifacts, TZ-11 session checkpoints and downstream status reconciliation into deterministic crash recovery.
