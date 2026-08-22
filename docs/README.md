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

| ID | Subsystem | Primary responsibility |
|---|---|---|
| TZ-00 | System Architecture | Overall boundaries, terminology, invariants, end-to-end flow |
| TZ-01 | Indexation API & Job Contract | Spring Boot → AstraIndexator request contract and job creation |
| TZ-02 | Job Coordinator & PostgreSQL | Queue, claim/lease, worker concurrency, retries, state machine |
| TZ-03 | Object Storage / SeaweedFS | Source and prepared artifact lifecycle and object layout |
| TZ-04 | File Validation & Acquisition | Download, MIME/type validation, hashing, temporary workspace |
| TZ-05 | Document Parser | Native PDF/text extraction, page/layout representation |
| TZ-06 | OCR Pipeline | OCR decision rules, model acquisition/versioning, OCR results |
| TZ-07 | Text Normalization | Canonical cleanup while preserving provenance and structure |
| TZ-08 | Logical Splitter | Logical segmentation into stable document blocks |
| TZ-09 | Canonical Document Model | DocumentContext, DocumentBlock, provenance, deterministic IDs |
| TZ-10 | Access Zones & TTL | accessZone normalization/propagation and expiration semantics |
| TZ-11 | AstraVector Integration | Ingestion DTO, batching, idempotency, ACK/error handling |
| TZ-12 | Document Lifecycle | Create/update/reindex/delete/version replacement semantics |
| TZ-13 | Reliability & Recovery | Crash recovery, poison jobs, dead-letter, reconciliation |
| TZ-14 | Observability | Logs, metrics, tracing, health/readiness, audit fields |
| TZ-15 | Configuration & Model Delivery | Nexus model delivery, config schema, startup validation |
| TZ-16 | Security | Trust boundary, secrets, storage/network policies, validation |
| TZ-17 | Testing & Verification | Unit/integration/E2E/recovery/performance acceptance evidence |
| TZ-18 | Deployment & Operations | Docker, Kubernetes readiness, scaling and operational runbooks |

## Implementation rule

No production implementation should introduce a contract that contradicts these specifications without first updating the relevant specification and recording the decision.

## Canonical responsibility boundary

AstraIndexator owns document acquisition, parsing, OCR, normalization and logical segmentation. AstraVector owns tokenizer/model execution, dense/sparse embedding generation, vector persistence and retrieval.

```text
Spring Boot
    |
    v
AstraIndexator
    |- job coordination
    |- SeaweedFS acquisition
    |- parsing
    |- OCR
    |- normalization
    |- logical splitting
    `- DocumentBlock[]
            |
            v
       AstraVector
            |- tokenizer
            |- BGE-M3
            |- dense/sparse projection
            |- PostgreSQL/Qdrant
            `- retrieval
```
