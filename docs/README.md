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
| TZ-02 | Job Coordinator & PostgreSQL | Queue, claim/lease, worker concurrency, retries, state machine | Planned |
| TZ-03 | Object Storage / SeaweedFS | Source and prepared artifact lifecycle and object layout | Planned |
| TZ-04 | File Validation & Acquisition | Download, MIME/type validation, hashing, temporary workspace | Planned |
| TZ-05 | Document Parser | Native PDF/text extraction, page/layout representation | Planned |
| TZ-06 | OCR Pipeline | OCR decision rules, model acquisition/versioning, OCR results | Planned |
| TZ-07 | Text Normalization | Canonical cleanup while preserving provenance and structure | Planned |
| TZ-08 | Multilingual Logical Splitter | Structure-aware, tokenizer-calibrated logical fragmentation before AstraVector | Baseline |
| TZ-09 | Canonical Document Model | DocumentContext, LogicalFragment/DocumentBlock, provenance, deterministic IDs | Planned |
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
            |- claim/lease
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
