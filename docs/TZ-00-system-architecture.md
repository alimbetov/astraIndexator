# TZ-00 — AstraIndexator 1.0 System Architecture

## 1. Purpose

Define the canonical architecture, service boundaries, terminology, invariants and end-to-end processing flow for AstraIndexator 1.0. This document is the parent specification for all subsystem technical specifications.

## 2. System objective

AstraIndexator transforms heterogeneous source documents into canonical logical text blocks and reliably delivers them to AstraVector for embedding generation and vector indexing.

AstraIndexator MUST NOT own embedding generation, BGE-M3 tokenization, sparse encoding, Qdrant vector projection, or retrieval logic.

## 3. Primary external systems

### 3.1 Spring Boot producer

Creates an indexation request and supplies document/business metadata, including access-zone and lifecycle information.

### 3.2 PostgreSQL

Stores indexation jobs, processing state, retries, leases, errors, artifact references and delivery metadata.

### 3.3 SeaweedFS

Stores source documents and prepared/recoverable artifacts.

### 3.4 Nexus

Stores versioned OCR/model artifacts required by AstraIndexator runtime.

### 3.5 AstraVector

Receives canonical document blocks and owns tokenization, BGE-M3 dense/sparse representation, persistence to its PostgreSQL/Qdrant projection and retrieval behavior.

## 4. Canonical end-to-end flow

```text
Spring Boot
    |
    | create indexation request
    v
PostgreSQL: PENDING job
    |
    | claim with lease
    v
AstraIndexator worker
    |
    | download source
    v
SeaweedFS
    |
    v
File validation + content hash
    |
    v
Document parser
    |-- native text/layout
    `-- image/page candidates
             |
             v
            OCR
             |
             v
Text normalization
    |
    v
Logical segmentation
    |
    v
DocumentContext + DocumentBlock[]
    |\
    | \ prepared recoverable artifact
    |  `----------------------------> SeaweedFS
    |
    `---- idempotent ingestion -----> AstraVector
                                        |
                                        v
                                      ACK
                                        |
                                        v
                                  COMPLETED job
```

## 5. Core invariants

1. A source document is identified independently from a processing attempt.
2. Processing attempts may repeat; externally visible ingestion MUST be idempotent.
3. `DocumentBlock` is the canonical semantic boundary between AstraIndexator and AstraVector.
4. `accessZoneId/accessZoneIds` and `accessZoneCode/accessZoneCodes` are normalized once but MUST remain semantically lossless through the pipeline.
5. Relative TTL received externally MUST be converted to a canonical absolute expiration (`expiresAt`) before delivery to AstraVector.
6. Every block MUST preserve provenance back to its source document and page/layout origin where available.
7. OCR MUST be conditional; a usable native PDF text layer takes precedence unless explicit policy requires OCR.
8. Logical splitting MUST NOT depend on AstraVector tokenizer or embedding model.
9. Worker crashes MUST NOT permanently orphan jobs. Lease expiry and recovery must make work reclaimable.
10. Completion is recorded only after AstraVector confirms durable acceptance according to the integration contract.

## 6. Processing stages

Canonical stages:

```text
PENDING
→ CLAIMED
→ ACQUIRING
→ PARSING
→ OCR_PROCESSING       (optional)
→ NORMALIZING
→ SPLITTING
→ PREPARED
→ DELIVERING
→ COMPLETED
```

Failure/control states:

```text
RETRY_WAIT
FAILED
DEAD_LETTER
CANCELLED
```

The exact state-transition contract is specified in TZ-02.

## 7. Canonical document hierarchy

```text
IndexationJob
    |
    v
DocumentContext
    |
    +-- source metadata
    +-- normalized ACL/access-zone context
    +-- lifecycle/expiration
    +-- parser/OCR/splitter versions
    `-- DocumentBlock[]
            |
            +-- deterministic blockId
            +-- sequence
            +-- text
            +-- page/layout range
            +-- extraction method
            +-- provenance
            `-- inherited access/lifecycle metadata
```

## 8. Storage principle

SeaweedFS is an object/artifact store, not a substitute for relational job state. PostgreSQL is authoritative for orchestration state and references.

Recommended prepared layout:

```text
original/<documentId>/<source-object>
prepared/<documentId>/<processingVersion>/manifest.json
prepared/<documentId>/<processingVersion>/blocks.jsonl
```

A separate object per logical block SHOULD NOT be the default design because it multiplies object count and complicates lifecycle operations.

## 9. Concurrency principle

AstraIndexator MUST support multiple workers/replicas. Job acquisition must use an atomic claim/lease mechanism and prevent simultaneous ownership of the same active attempt.

The preferred PostgreSQL coordination approach is based on transactionally claiming eligible rows using `FOR UPDATE SKIP LOCKED`, plus `worker_id`, `locked_at` and `lease_until`.

## 10. Idempotency principle

The system must distinguish:

- document identity;
- document content identity (`contentHash`);
- document/indexing version;
- processing attempt;
- AstraVector ingestion identity.

A deterministic ingestion key MUST be defined before implementation. A representative conceptual form is:

```text
ingestionKey = hash(documentId + contentHash + indexingProfileVersion)
```

The exact algorithm belongs to TZ-09/TZ-11.

## 11. Access-zone contract principle

External compatibility may expose both singular and plural fields:

```text
accessZoneId
accessZoneIds[]
accessZoneCode
accessZoneCodes[]
```

Internally these MUST be normalized to deduplicated plural collections while preserving the semantics required by the integration contract.

Conflict behavior, empty/null behavior and validation are specified in TZ-10.

## 12. TTL principle

A relative TTL alone is insufficient for retries and delayed processing. At request acceptance the service must derive an absolute expiration timestamp when applicable:

```text
ttlSeconds -> expiresAt
```

Subsequent retries MUST NOT restart the TTL clock unless an explicit new document version/request requires it.

## 13. OCR principle

For PDFs:

```text
page
  |- usable native text -> parser result
  `- absent/insufficient text -> rendered page/image -> OCR
```

Embedded images may be OCR candidates, but OCR must be policy-driven to avoid duplicate/noisy content such as logos, decorative assets and repeated headers.

## 14. Versioning principle

Processing output must be reproducible enough for investigation and reindexing. The resulting document context/manifests must record applicable versions such as:

- parser version;
- OCR engine/model version;
- normalization profile version;
- logical splitter version;
- contract/schema version.

## 15. Non-functional baseline

AstraIndexator 1.0 shall be designed for:

- horizontal worker scaling;
- bounded concurrency and resource control;
- retryable infrastructure failures;
- deterministic crash recovery;
- structured observability;
- contract-versioned external integration;
- large-document handling without loading all artifacts into unbounded memory;
- controlled local temporary storage with cleanup;
- testable E2E operation against real PostgreSQL/object-storage/AstraVector-compatible dependencies.

## 16. Out of scope for AstraIndexator

The following are explicitly outside AstraIndexator ownership:

- BGE-M3 tokenization;
- dense embedding computation;
- sparse representation computation;
- Qdrant collection schema management owned by AstraVector;
- retrieval/ranking;
- client-facing vector search API;
- interpretation of access zones beyond normalization/validation/propagation required by contract.

## 17. Child specifications

All implementation detail is delegated to TZ-01 through TZ-18 listed in `docs/README.md`. Any child specification that conflicts with a core invariant in this document requires an explicit architecture decision and update to this document.

## 18. Acceptance criteria for TZ-00

TZ-00 is accepted when:

1. all subsystem boundaries are represented by a child TZ;
2. no ownership overlap exists between AstraIndexator and AstraVector for tokenization/embeddings/retrieval;
3. access-zone and TTL propagation are explicit cross-cutting concerns;
4. crash recovery, idempotency and horizontal scaling are architectural requirements rather than later implementation details;
5. every later DTO can be traced to the `IndexationJob -> DocumentContext -> DocumentBlock[] -> AstraVector` flow.
