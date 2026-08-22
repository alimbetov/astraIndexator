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
| TZ-06 | OCR Pipeline | Page/region-aware OCR policy, multilingual recognition, native/OCR reconciliation and versioned model/runtime contract | Baseline |
| TZ-07 | Text Normalization | Deterministic multilingual-safe representation cleanup with protected spans, provenance and structure preservation | Baseline |
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
            |- conditional page/region-aware OCR
            |- deterministic normalization
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

`processing_stage` carries progress details such as acquisition/parsing/OCR/normalization/splitting/delivery while top-level status remains stable.

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

Prepared data uses bounded sharded JSONL parts. `manifest.json` is published last and acts as the prepared artifact-set commit marker. A prepared artifact becomes authoritative only after its manifest reference is persisted in PostgreSQL through the current TZ-02 lease/fencing generation.

Source, prepared artifacts, staging objects and AstraVector vector state have independent retention lifecycles.

## File validation & acquisition invariant

Producer file name, extension, declared MIME type, object content-type and metadata are hints rather than trusted parser-routing evidence.

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
```

Actual source bytes are authoritative for size and integrity. Partial downloads never become parser input. ZIP-based Office formats require bounded container inspection; generic archive ingestion remains disabled by default.

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

The parser preserves headings, paragraphs, lists, tables, images, captions, page/section provenance and coordinates when available. PDF processing is page-aware and may classify pages independently as `NATIVE_TEXT`, `SCANNED_IMAGE`, `MIXED`, `LOW_SIGNAL` or `EMPTY`.

Multi-column and bilingual layouts must be spatially reconstructed before logical splitting. Tables remain structured. Repeated headers/footers/page numbers are page-furniture candidates for TZ-07 rather than blindly indexed or irreversibly deleted.

`alimbetov/llm-indexator` remains an implementation-learning source but its old flat extraction, local embeddings/chunking and vector ownership are not normative.

## OCR invariant

OCR is a conditional enrichment stage after native parsing.

```text
ParsedDocument + OcrCandidate[]
  -> versioned OCR decision policy
  -> bounded page/image/region rendering
  -> verified local OCR model
  -> OcrObservation{text,bbox,confidence,model provenance}
  -> native/OCR reconciliation
  -> canonical DocumentElement[]
```

`OCR_IF_NEEDED` is the production default. Mixed PDFs are handled page/region-wise and overlapping native/OCR copies are deterministically suppressed rather than concatenated.

The baseline OCR language capability is `ru/kk/en`. Models are versioned deployment artifacts supplied by TZ-15 from the internal Nexus target `https://nexus.astrabase.asia` into a local manifest/checksum-verified cache. Runtime model downloads and silent model/engine fallbacks are forbidden.

CPU OCR is the portable baseline; GPU OCR is optional and explicitly profiled. OCR resource use is bounded by page count, pixels, memory, concurrency and timeouts.

## Text normalization invariant

TZ-07 is a deterministic, non-destructive representation-cleanup stage between parser/OCR reconciliation and TZ-08 logical fragmentation.

```text
native/OCR evidence
      -> originalText
      -> NFC + structure-aware safe cleanup
      -> normalizedText
      -> TZ-08
      -> contextPrefix + normalized source text
      -> embeddingText
```

The governing rule is **normalize representation, not meaning**.

Baseline rules:

- Unicode normalization is `NFC`; global NFKC/NFKD is not the default;
- Kazakh letters `ә ғ қ ң ө ұ ү һ і` and their uppercase forms remain distinct and unchanged;
- `ё` is not converted to `е`;
- translation, transliteration, stemming, lemmatization, spell correction and LLM rewriting are out of scope;
- prose whitespace/line-wrap cleanup is element-aware and deterministic;
- code/preformatted indentation and punctuation are preserved;
- dehyphenation is conservative and requires line/paragraph continuity evidence;
- URLs, UUIDs, IPs, package/method names, versions, access-zone codes, hashes, dates, amounts and clause numbers are protected spans;
- page headers/footers/page numbers are suppressed from index text only after layout/repetition evidence and remain auditable in provenance;
- TABLE/LIST/CODE structure is never flattened merely by normalization;
- low-confidence OCR characters are not guessed (`0/O`, `1/l`, dictionary correction are not baseline rules);
- normalizer version/profile participates in `processingFingerprint`;
- large-document normalization is bounded-memory and may use a deterministic two-pass evidence/normalization flow.

Canonical representations remain semantically distinct:

```text
originalText     = accepted parser/OCR source evidence before TZ-07 cleanup
normalizedText   = deterministic source representation used by TZ-08
contextPrefix    = synthetic structural context added by TZ-08
embeddingText    = downstream materialization of context + normalized source text
```

Ambiguity defaults to preserving the recovered source form plus diagnostics, rather than making an irreversible lexical guess.

## Canonical document-model invariant

The internal semantic data path is:

```text
IndexationJob
  -> AcquiredSource
  -> ParsedDocument
  -> DocumentElement[]
  -> normalized DocumentElement[]
  -> LogicalFragment[] / AstraVector LogicalBlock mapping
  -> AstraVector public ingestion facade
```

`LogicalFragment` is the stable AstraIndexator semantic source container and MUST NOT be confused with AstraVector-generated tokenizer-aware chunks or internal chunk IDs.

The canonical model preserves document identity/version, deterministic element/fragment IDs, multilingual content, layout provenance, OCR origin, structured tables/lists and processing/schema versions required for deterministic replay.

## RAG fragmentation invariant

AstraIndexator MUST NOT duplicate AstraVector embedding-size chunking. Its splitter is structure-first and tokenizer-free at runtime, using deterministic size guards calibrated against the real AstraVector tokenizer/model contract during verification.

```text
normalized ParsedDocument
  -> LogicalFragment[]
  -> AstraVector LogicalBlock[]
  -> AstraVector tokenizer-aware chunking
  -> searchable parent/child representations
```

Language switching alone is not a split boundary. Original multilingual source content is preserved.

## Access-zone and TTL invariant

AstraIndexator mirrors the current AstraVector contract.

```text
accessZoneCode = exactly four ASCII digits: 0000..9999
accessZoneId   = UUID-backed zone identity
```

One indexed document version belongs to exactly one effective access zone. Plural selectors are retained for producer/retrieval compatibility but a single indexing job normalizes to one distinct effective zone.

The code-to-default-TTL matrix in TZ-10 is compatibility/reference data; AstraVector Access Zone Registry remains the runtime authority. For session ingestion `ttl_days=0` means inherit effective zone/platform policy and does not mean unconditional never-expire.

## AstraVector integration invariant

AstraIndexator uses the generated `astravector.embedding.v1.AstraVectorIngestionFacade` client and does not introduce a parallel custom ingestion API.

```text
canonical AstraIndexator model
        -> anti-corruption mapping
        -> LogicalBlock[]
        -> IndexLogicalDocument
           OR session Start/Append/Finalize
        -> GetLogicalDocumentIngestionStatus
        -> GetDocumentVectorStatus
        -> searchable=true
        -> AstraIndexator job COMPLETED
```

Mutating RPC timeouts are ambiguous outcomes and are reconciled before unsafe replay/replacement. Session delivery is deterministic, bounded-memory and checkpointed.

Two P0 integration decisions remain explicit: byte-exact cross-language hashing fixtures and deterministic mapping from opaque producer `documentVersion` to AstraVector numeric version where necessary.

## Document lifecycle invariant

AstraIndexator separates local job lifecycle from AstraVector document/vector lifecycle.

```text
new source revision -> same documentId + new immutable version + new job
new version builds while previous searchable version remains intact
partial/finalizing downstream state != COMPLETED
COMPLETED -> downstream searchable=true
lost mutation ACK -> reconcile before replay/new operation
```

Reindex is explicit/auditable. Delete is asynchronous and uses AstraVector facade; AstraIndexator never deletes Qdrant points directly. Source, prepared artifacts and vector state have independent lifecycles.

## Reliability and recovery invariant

Recovery is state-driven rather than a blind whole-pipeline restart.

```text
reclaim expired lease
  -> validate fencing generation
  -> load durable checkpoints
  -> validate source/prepared compatibility
  -> reconcile downstream state
  -> resume from earliest safe stage
  -> execute idempotently
  -> prove searchable or enter terminal failure/dead-letter
```

AstraIndexator never repairs Qdrant directly. Finite retry budgets are mandatory and dead-letter requeue is explicit/auditable.

## Current critical design path

The complete processing/control baseline now includes:

```text
TZ-00 System Architecture                  ✅
TZ-01 Indexation Request & Job Contract    ✅
TZ-02 Job Coordinator & PostgreSQL         ✅
TZ-03 Object Storage / SeaweedFS            ✅
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
```

The remaining specifications are now cross-cutting operability/proof layers:

```text
TZ-14 Observability
  -> TZ-15 Configuration & Model Delivery
  -> TZ-16 Security
  -> TZ-17 Testing & Verification
  -> TZ-18 Deployment & Operations
```

TZ-17 SHALL convert the golden failure scenarios from TZ-13, storage publication/recovery criteria from TZ-03, hostile-input criteria from TZ-04, structure/reading-order criteria from TZ-05, OCR criteria from TZ-06 and multilingual/structural/RAG-quality normalization criteria from TZ-07 into executable evidence.
