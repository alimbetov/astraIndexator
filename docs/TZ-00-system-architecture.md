# TZ-00 — AstraIndexator 1.0 System Architecture

## 1. Purpose

Define the canonical architecture, service boundaries, terminology, invariants and end-to-end processing flow for AstraIndexator 1.0. This document is the parent specification for all subsystem technical specifications.

## 2. System objective

AstraIndexator transforms heterogeneous source documents into canonical logical text structures and reliably delivers them to AstraVector for tokenizer-aware chunking, embedding generation and vector indexing.

AstraIndexator MUST NOT own embedding generation, BGE-M3 tokenization, sparse encoding, Qdrant vector projection, or retrieval logic.

## 3. Primary external systems

### 3.1 Spring Boot producer

Creates an indexation request and supplies document/business metadata, including access-zone and lifecycle intent.

### 3.2 PostgreSQL

Stores durable indexation jobs, processing state, retries, leases/fencing, errors, artifact references, downstream checkpoints and operational inventory/audit state.

### 3.3 SeaweedFS

Stores immutable source documents and prepared/recoverable artifacts.

### 3.4 Nexus

Stores versioned OCR/model artifacts required by explicit preload/build workflows. Document-time model download is not part of normal runtime processing.

### 3.5 AstraVector

Receives ordered `LogicalBlock[]` through its public ingestion facade and owns tokenization, tokenizer-aware searchable chunking, BGE-M3 dense/sparse representations, canonical vector/document state, Qdrant projection, effective TTL lifecycle and retrieval behavior.

## 4. Canonical end-to-end flow

```text
Spring Boot
    |
    +--> SeaweedFS immutable source object
    |
    `--> PostgreSQL PENDING job
            |
            | atomic claim / renewable lease / fencing
            v
      AstraIndexator worker
            |
            v
      acquisition + validation
            |
            v
      structure-aware parser
            |
            +--> native content/layout
            `--> OCR candidates
                     |
                     v
              conditional OCR
                     |
                     v
              normalization
                     |
                     v
          logical semantic splitting
                     |
                     v
          LogicalFragment[] / LogicalBlock[]
             |                       |
             | prepared artifact     | public facade
             v                       v
          SeaweedFS             AstraVector
                                     |
                          tokenizer-aware chunking
                          embedding / projection
                                     |
                          status reconciliation
                                     |
                          searchable = true
                                     |
                                     v
                              COMPLETED job
```

## 5. Core invariants

1. A source document identity is independent from a processing attempt.
2. Processing attempts may repeat; externally visible mutations MUST be idempotent or reconcilable.
3. `LogicalFragment` is the canonical AstraIndexator semantic container; `LogicalBlock` is the AstraVector public ingestion representation. Neither is an AstraVector generated searchable chunk ID.
4. `accessZoneId/accessZoneIds` and `accessZoneCode/accessZoneCodes` are normalized according to TZ-10; one ingestion job resolves to exactly one effective ingestion zone.
5. AstraIndexator preserves producer TTL intent but does not become the authority for effective expiry. For session ingestion `ttl_days=0` means inherit zone/platform policy, not unconditional never-expire.
6. Every logical element/block MUST preserve provenance back to its source document and page/layout origin where available.
7. OCR MUST be conditional; a usable native PDF text layer takes precedence unless explicit policy requires OCR.
8. Logical splitting MUST NOT perform AstraVector tokenizer-aware final chunking at runtime.
9. Worker crashes MUST NOT permanently orphan jobs. Lease expiry, monotonic `lease_generation`, durable checkpoints and reconciliation make work reclaimable.
10. A local job becomes `COMPLETED` only after the downstream lifecycle evidence required by TZ-11/TZ-12 proves the document searchable; mutation RPC success alone is insufficient.
11. SeaweedFS is the binary/artifact store; PostgreSQL remains the AstraIndexator coordination authority.
12. AstraIndexator MUST NOT directly mutate Qdrant or AstraVector PostgreSQL.

## 6. Job lifecycle and processing stages

Top-level durable job lifecycle is defined by TZ-02:

```text
PENDING
PROCESSING
RETRY_WAIT
COMPLETED
FAILED
DEAD_LETTER
CANCELLED
```

`CLAIMED` is not a required durable top-level state. Claim ownership is represented by the current lease/fencing generation and attempt record.

While `status = PROCESSING`, `processing_stage` may carry progress such as:

```text
ACQUIRING
VALIDATING
PARSING
OCR_PROCESSING
NORMALIZING
SPLITTING
PREPARING_ARTIFACTS
DELIVERING / APPENDING_BLOCKS
FINALIZING
FINALIZING_VECTOR_STATE
```

Exact stage naming is diagnostic; authoritative lifecycle semantics remain in TZ-02/TZ-12/TZ-13.

## 7. Canonical semantic hierarchy

```text
IndexationJob
    |
    +-- DocumentIdentity
    +-- SourceReference
    +-- AccessScope
    +-- TtlIntent
    +-- processing/version identity
    |
    v
ParsedDocument
    |
    v
DocumentElement[]
    |
    v
LogicalFragment[]
    |
    v
AstraVector LogicalBlock[]
```

Canonical models preserve:

- stable document/version identity;
- deterministic element/fragment IDs where required;
- structural hierarchy/order;
- multilingual content;
- page/layout/source provenance;
- parser/OCR/normalizer/splitter/schema versions;
- source/content fingerprints required for replay/investigation.

## 8. Storage principle

SeaweedFS is an object/artifact store, not a substitute for relational job state. PostgreSQL is authoritative for orchestration state and references.

Prepared artifacts use the TZ-03 publication model:

```text
prepared/<document-key>/v<version>/<processingFingerprint>/
    manifest.json
    elements/part-xxxxx.jsonl
    fragments/part-xxxxx.jsonl
    derived/...
```

Prepared data is bounded/sharded rather than one object per logical block and rather than one unbounded monolith. `manifest.json` is published last as the artifact-set commit marker; the authoritative manifest reference is persisted through the current lease/fencing generation.

## 9. Concurrency principle

AstraIndexator MUST support multiple workers/replicas. Job acquisition uses short PostgreSQL transactions with `FOR UPDATE SKIP LOCKED`, plus renewable lease ownership and monotonically increasing `lease_generation` fencing.

Correctness does not depend on replica count or pod survival.

## 10. Idempotency principle

The system distinguishes:

```text
documentId
!= documentVersion
!= jobId
!= processingAttemptId
!= elementId
!= fragmentId
!= AstraVector ingestionSessionId
!= AstraVector generated chunk IDs
```

Downstream retries reuse deterministic operation identities/checkpoints. Exact session/batch idempotency and canonical hash semantics belong to TZ-11/TZ-17.

## 11. Access-zone principle

External compatibility may expose:

```text
accessZoneId
accessZoneIds[]
accessZoneCode
accessZoneCodes[]
```

`accessZoneCode` is a four-digit string `0000..9999` and leading zeroes are significant.

For ingestion, compatible singular/plural fields MUST normalize to exactly one effective zone. Code/ID combinations must resolve consistently; AstraVector Access Zone Registry remains authoritative. Retrieval may use multiple zones as a separate read concern.

Detailed rules are defined in TZ-10.

## 12. TTL principle

AstraIndexator preserves the original lifecycle intent and propagates only semantics supported by the selected AstraVector ingestion path.

For the production-oriented session path:

```text
ttl_days = 0  -> inherit effective zone/platform policy
ttl_days > 0  -> explicit relative finite lifetime in days
```

AstraIndexator MUST NOT duplicate the AstraVector access-zone code→TTL matrix as runtime policy and MUST NOT silently approximate an absolute expiry timestamp into days.

Exact effective expiry, search exclusion and cleanup are AstraVector-owned lifecycle state. Session `expires_at` is not document knowledge expiry.

## 13. OCR principle

For PDFs and images:

```text
page/region
  |- usable native text -> preserve native evidence
  `- absent/insufficient text -> OCR candidate
```

OCR is page/region-aware. Mixed PDFs may contain native, scanned and mixed regions in one document. Native/OCR overlap is reconciled deterministically so duplicate content is not blindly concatenated.

Baseline language capability is `ru/kk/en`.

## 14. Versioning principle

Processing output must be reproducible and attributable. Applicable identity includes, as relevant:

- parser version/profile;
- OCR engine/model/artifact revision;
- normalization profile/version;
- logical splitter version/profile;
- canonical contract/schema version;
- processing fingerprint;
- effective non-secret configuration fingerprint where defined.

Changing a processing-affecting model/profile may make prepared artifacts incompatible and may require explicit reindex/version semantics.

## 15. Non-functional baseline

AstraIndexator 1.0 shall be designed for:

- horizontal worker scaling;
- bounded concurrency, memory and local storage;
- retryable infrastructure failures;
- deterministic crash recovery and stale-worker fencing;
- structured observability and Knowledge Inventory;
- contract-versioned external integration;
- large-document handling without unbounded in-memory accumulation;
- controlled local temporary storage with cleanup;
- offline document-time model execution after verified preload;
- executable E2E/recovery/RAG-quality/smoke evidence.

## 16. Out of scope for AstraIndexator

The following are explicitly outside AstraIndexator ownership:

- BGE-M3 tokenization and embedding computation;
- AstraVector tokenizer-aware final searchable chunking;
- dense/sparse representation generation;
- Qdrant collection/state management owned by AstraVector;
- retrieval/ranking;
- public client-facing vector search API;
- independent access-zone/TTL policy authority;
- end-user OAuth2/OIDC/JWT/RBAC for the internal-service baseline.

## 17. Child specifications

Implementation detail is defined by TZ-01 through TZ-18 listed in `docs/README.md`.

Child specifications are more specific than this parent document. If a child contract is intentionally changed, TZ-00 SHALL be updated when the change affects a parent-level invariant or terminology.

## 18. Acceptance criteria for TZ-00

TZ-00 is accepted when:

1. all subsystem boundaries are represented by a child TZ;
2. no ownership overlap exists between AstraIndexator and AstraVector for tokenization/embeddings/retrieval/vector projection;
3. identity, access-zone and TTL responsibilities match TZ-01/TZ-10/TZ-11;
4. crash recovery, idempotency, lease/fencing and horizontal scaling are architectural requirements rather than later implementation details;
5. canonical data flow traces `IndexationJob -> ParsedDocument -> DocumentElement[] -> LogicalFragment[] -> LogicalBlock[] -> AstraVector`;
6. top-level job lifecycle matches TZ-02 and does not introduce `CLAIMED` as a competing durable state;
7. local completion requires the downstream searchability proof specified by TZ-11/TZ-12;
8. prepared artifact terminology/storage matches TZ-03 rather than the obsolete one-file `blocks.jsonl` model.
