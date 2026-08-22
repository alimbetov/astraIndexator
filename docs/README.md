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
| TZ-03 | Object Storage / SeaweedFS | Immutable source objects, sharded prepared artifacts, manifest publication, retention and recovery | Baseline |
| TZ-04 | File Validation & Acquisition | Bounded source acquisition, SHA-256, trusted type detection, container/image safety and parser admission | Baseline |
| TZ-05 | Document Parser | Structure reconstruction, reading order, native extraction, tables/images and OCR-candidate handoff | Baseline |
| TZ-06 | OCR Pipeline | OCR decision rules, model acquisition/versioning, OCR results | Planned |
| TZ-07 | Text Normalization | Canonical cleanup while preserving provenance and structure | Planned |
| TZ-08 | Multilingual Logical Splitter | Structure-aware, tokenizer-calibrated logical fragmentation before AstraVector | Baseline |
| TZ-09 | Canonical Document Model | ParsedDocument, DocumentElement, LogicalFragment, provenance, deterministic IDs, prepared artifacts | Baseline |
| TZ-10 | Access Zones & TTL | AstraVector-compatible access-zone selectors, one-zone ingestion scope, registry-owned TTL semantics | Baseline |
| TZ-11 | AstraVector Integration | Public ingestion facade, LogicalBlock mapping, single/session ingestion, batching, idempotency, reconciliation and readiness | Baseline |
| TZ-12 | Document Lifecycle | Create/new-version/reindex/delete, cancellation, TTL expiry, replacement and searchability semantics | Baseline |
| TZ-13 | Reliability & Recovery | Crash recovery, replay, reconciliation, poison jobs, dead-letter, fencing and operator recovery | Baseline |
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
    +--> SeaweedFS immutable source object
    |
    `--> PostgreSQL PENDING job
            |
            v
      AstraIndexator replicas
            |- atomic claim/lease/fencing
            |- bounded source acquisition + validation
            |- structure-aware parsing/layout
            |- conditional OCR
            |- normalization
            |- multilingual logical splitting
            `- prepared artifact publication
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

`processing_stage` carries progress details such as acquisition/parsing/OCR/splitting/delivery while top-level status remains stable.

Large-document recovery requires prepared-artifact checkpoints plus persisted downstream/session checkpoints so a crash does not force blind full reprocessing or duplicate downstream ingestion.

## Object storage invariant

SeaweedFS is the durable binary/artifact store, while PostgreSQL remains the AstraIndexator coordination authority.

Canonical storage model:

```text
SOURCE
  -> immutable producer-uploaded bytes

PREPARED
  -> manifest.json
  -> elements/part-xxxxx.jsonl
  -> fragments/part-xxxxx.jsonl
  -> optional bounded derived assets

STAGING
  -> temporary publication objects only

LOCAL WORKSPACE
  -> ephemeral attempt-scoped files only
```

Prepared data uses bounded sharded JSONL parts. AstraIndexator does not create one object per logical fragment and does not require one unbounded JSONL object for arbitrarily large documents.

`manifest.json` is published last and acts as the prepared artifact-set commit marker. Recovery considers an artifact valid only when its manifest schema is supported and all required parts pass existence/size/hash validation.

A prepared artifact becomes authoritative for a job only after its manifest reference is persisted in PostgreSQL through the current TZ-02 lease/fencing generation. A stale worker may leave orphan staging data but cannot replace the authoritative checkpoint.

Source, prepared artifacts, staging objects and AstraVector vector state have independent retention lifecycles. Vector deletion/TTL expiry never implicitly deletes SeaweedFS source/prepared objects, and SeaweedFS cleanup never directly mutates AstraVector/Qdrant.

## File validation & acquisition invariant

A producer file name, extension, declared MIME type, object content-type and object metadata are hints rather than trusted parser-routing evidence.

Canonical acquisition boundary:

```text
immutable SeaweedFS source reference
        -> HEAD/preflight
        -> bounded streaming download
        -> actual byte-count enforcement
        -> SHA-256 over acquired bytes
        -> signature/container/type detection
        -> decompression/image/resource safety checks
        -> supported-format admission
        -> attempt-local source.validated
        -> AcquiredSource
        -> TZ-05/TZ-06
```

The actual byte stream is authoritative for size and integrity. Metadata size is checked early but the hard source-size limit is also enforced continuously while reading. Partial downloads never become parser input.

Parser routing uses the detected canonical format, not the extension alone. Declared MIME, extension and detected type remain distinct evidence and mismatches are explicit. Unknown, ambiguous, executable-masquerading or unsupported content fails closed.

ZIP-based Office formats are admitted only through bounded container inspection. Generic archive ingestion remains disabled by default. Container admission protects against excessive entry count, expansion size, compression ratio, nesting and path traversal; image admission applies dimensions/pixel/page guards before OCR decoding.

Source acquisition computes the SHA-256 used by TZ-03 prepared-artifact identity and TZ-13 recovery. A retry of the same immutable source under the same validation profile is expected to reproduce size, hash, detected format and admission result.

Local source files are attempt-scoped and non-authoritative. A reclaimed worker reacquires the source unless a later compatible prepared checkpoint makes reacquisition unnecessary.

## Document parser invariant

TZ-05 defines parsing as **canonical structure reconstruction**, not flat text extraction.

```text
AcquiredSource
  -> FileTypeHandlerRegistry
  -> format-specific parser
  -> structure/layout reconstruction
  -> deterministic logical reading order
  -> ParsedDocument / DocumentElement[]
  -> OCR candidates for TZ-06
```

The parser preserves headings, paragraphs, lists, tables, images, captions, page/section provenance and coordinates when available. Native text is preferred over OCR; embedded images remain first-class elements even when OCR is not performed.

PDF processing is page-aware and may classify pages independently as `NATIVE_TEXT`, `SCANNED_IMAGE`, `MIXED`, `LOW_SIGNAL` or `EMPTY`. One mixed PDF is therefore not forced into an all-native or all-OCR mode.

Multi-column and bilingual layouts must be spatially reconstructed before logical splitting. The parser must not naïvely interleave RU/KK columns or other parallel reading flows. Reading-order rules are versioned because they influence deterministic element/fragment identity and RAG quality.

Tables are preserved structurally rather than immediately flattened to pipe-delimited prose. Repeated headers/footers/page numbers are classified as page-furniture candidates for TZ-07 rather than blindly indexed or irreversibly deleted.

`alimbetov/llm-indexator` remains a source of implementation lessons (parser versioning, low-signal/OCR-required diagnostics, table-aware extraction and smoke fixtures), but its old flat extraction, local embeddings/chunking and vector ownership are not normative for AstraIndexator.

Parser-core is independent from OCR model delivery. TZ-06/TZ-15 may obtain approved OCR/ML artifacts from the internal Nexus service at `https://nexus.astrabase.asia`; TZ-05 only emits deterministic OCR candidates and provenance.

## Canonical document-model invariant

The internal semantic data path is:

```text
IndexationJob
  -> AcquiredSource
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

Prepared artifacts are streamable through the TZ-03 manifest/parts contract so downstream delivery can be replayed without reparsing the original binary when the canonical schema and processing fingerprint are compatible.

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

## Document lifecycle invariant

AstraIndexator separates local job lifecycle from AstraVector document/vector lifecycle.

Baseline rules:

```text
new source revision -> same documentId + new immutable version + new job
new version builds while previous searchable version remains intact
partial/finalizing downstream state != COMPLETED
COMPLETED -> downstream searchable=true
lost mutation ACK -> reconcile before replay/new operation
```

Reindex is explicit and auditable. Destructive same-version replacement is not the default until AstraVector `replace_existing_version` behavior is verified end-to-end.

Deletion uses `DeleteDocumentVectorsFacade` and is treated as asynchronous/reconcilable (`DELETE_SCHEDULED -> DELETING -> DELETED`). AstraIndexator never deletes Qdrant points directly.

Session expiry and document TTL expiry are distinct lifecycle events. AstraVector remains authoritative for effective TTL expiration/search exclusion.

Source objects, prepared canonical artifacts and AstraVector vector state are separate resources and therefore have separate retention/deletion policies.

## Reliability and recovery invariant

Recovery is **state-driven**, not a blind restart of the whole pipeline.

Canonical recovery model:

```text
reclaim expired lease
  -> validate fencing generation
  -> load durable checkpoints
  -> validate source/prepared artifact compatibility
  -> reconcile downstream ingestion/vector state
  -> resume from earliest safe stage
  -> execute idempotently
  -> prove searchable or enter terminal failure/dead-letter
```

A mutating RPC timeout is an ambiguous outcome, not proof of failure. Start/Append/Finalize/Delete are reconciled before unsafe replacement or replay. Compatible prepared artifacts and deterministic session checkpoints are reused across worker crashes.

AstraIndexator never repairs Qdrant directly; AstraVector PostgreSQL remains the downstream canonical state and Qdrant is a rebuildable search projection.

Finite retry budgets are mandatory. Poison work transitions to `DEAD_LETTER`, and requeue is explicit, auditable and history-preserving.

## Current critical design path

The processing/control baselines now include:

```text
TZ-02 Job Coordinator & PostgreSQL       ✅
TZ-03 Object Storage / SeaweedFS          ✅
TZ-04 File Validation & Acquisition       ✅
TZ-05 Document Parser                     ✅
TZ-10 Access Zones & TTL                  ✅
TZ-11 AstraVector Integration             ✅
TZ-12 Document Lifecycle                  ✅
TZ-13 Reliability & Recovery              ✅
```

The remaining processing-plane specifications should now be completed in order:

```text
TZ-06 OCR Pipeline
  -> TZ-07 Text Normalization
```

Then the cross-cutting specifications should close operability and proof:

```text
TZ-14 Observability
TZ-15 Configuration & Model Delivery
TZ-16 Security
TZ-17 Testing & Verification
TZ-18 Deployment & Operations
```

TZ-17 SHALL convert the golden failure scenarios from TZ-13, storage publication/recovery criteria from TZ-03, hostile-input criteria from TZ-04 and structure/reading-order/RAG-quality criteria from TZ-05 into executable evidence.
