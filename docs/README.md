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
| TZ-10 | Access Zones & TTL | accessZone normalization/propagation and expiration semantics | Planned |
| TZ-11 | AstraVector Integration | Ingestion DTO, fragment/source-group identity, batching, idempotency, ACK/error handling | Planned |
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

AstraIndexator owns document acquisition, parsing, OCR, normalization and **logical semantic fragmentation**. AstraVector owns tokenizer/model execution, dense/sparse embedding generation, multi-granularity searchable chunking, vector persistence and retrieval.

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
              LogicalFragment[]
                    |
                    v
               AstraVector
                    |- SOURCE/PARENT/SUB_* chunking
                    |- tokenizer
                    |- BGE-M3
                    |- dense/sparse projection
                    |- PostgreSQL/Qdrant
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

Large-document recovery requires prepared-artifact checkpoints plus persisted fragment-delivery checkpoints so a crash does not force blind full reprocessing or duplicate downstream ingestion.

## Canonical document-model invariant

The internal semantic data path is:

```text
IndexationJob
  -> SourceObject
  -> ParsedDocument
  -> DocumentElement[]
  -> LogicalFragment[]
  -> AstraVector source-group ingestion
```

`LogicalFragment` is the stable AstraIndexator semantic source container. It MUST NOT be confused with AstraVector-generated `SOURCE`, `PARENT`, `SUB_180`, `SUB_260` chunks or their IDs.

The canonical model must preserve:

- `documentId/documentVersion`;
- deterministic `elementId/fragmentId`;
- multilingual content and language metadata;
- page/slide/sheet/layout provenance where available;
- image/OCR origin relationships;
- structured tables/lists;
- `originalText` versus synthetic `contextPrefix` versus downstream `embeddingText`;
- processing/schema versions needed for deterministic replay and diagnostics.

Prepared artifacts are expected to use a manifest plus streaming-friendly element/fragment collections such as JSONL so downstream delivery can be replayed without reparsing the original binary when the canonical schema version is supported.

## RAG fragmentation invariant

AstraIndexator MUST NOT duplicate AstraVector's embedding-size chunking. Its splitter is structure-first and tokenizer-free at runtime, using deterministic size guards calibrated against the real AstraVector model/tokenizer during verification.

Canonical path:

```text
ParsedDocument
  -> LogicalFragment[]
  -> AstraVector CreateMultiGranularityChunks
  -> SOURCE / PARENT / SUB_180 / SUB_260
```

For multilingual content, language switching alone is not a split boundary. Original language content is preserved; automatic translation/transliteration is not part of the indexing pipeline.

## Current critical design path

The next specifications should close the cross-service correctness chain in this order:

```text
TZ-10 Access Zones & TTL
  -> TZ-11 AstraVector Integration
  -> TZ-13 Reliability & Recovery
  -> TZ-17 failure/recovery E2E verification
```

This sequence ensures access/lifecycle semantics are fixed before the downstream ingestion protocol and that recovery is designed against the real coordinator and AstraVector contracts.
