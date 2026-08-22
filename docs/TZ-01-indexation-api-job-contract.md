# TZ-01 — Indexation Request & Job Contract

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-01
- **Title:** Indexation Request & Job Contract
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Primary integration:** Spring Boot producer → PostgreSQL durable job → AstraIndexator workers
- **Related specifications:** TZ-02, TZ-03, TZ-04, TZ-05, TZ-06, TZ-08, TZ-09, TZ-10, TZ-11, TZ-12, TZ-13, TZ-14, TZ-16, TZ-17, TZ-18

---

## 2. Purpose

This specification defines the canonical contract by which a Spring Boot producer submits documents for asynchronous processing by AstraIndexator.

The baseline architecture is **database-coordinated asynchronous ingestion**, not a mandatory synchronous REST call to AstraIndexator.

The producer:

1. assigns or obtains the stable business `documentId`;
2. uploads the source file to SeaweedFS;
3. creates a durable `IndexationJob` record in PostgreSQL;
4. returns control to the caller without waiting for parsing, OCR, logical splitting or AstraVector indexing.

AstraIndexator:

1. runs as one or more active worker replicas;
2. claims eligible jobs from PostgreSQL;
3. loads the source object from SeaweedFS;
4. parses and OCR-processes the source when required;
5. creates canonical `DocumentBlock[]`;
6. sends them to AstraVector;
7. persists processing state and completion/failure information.

This specification defines the request/job boundary, identity, ownership of fields, source reference, access/lifecycle context, file-format baseline, document-image handling principles and acceptance requirements.

The detailed claim/lease/heartbeat/retry SQL and state machine are defined in TZ-02.

---

## 3. Architectural baseline

```text
Spring Boot producer
    |
    | 1. determine documentId/documentVersion
    | 2. upload source object
    v
SeaweedFS
    |
    | source reference
    v
PostgreSQL: astra_indexator.indexation_job
    |
    | durable PENDING work
    v
+----------------------+----------------------+----------------------+
|                      |                      |
v                      v                      v
Indexator-1        Indexator-2           Indexator-N
|                      |                      |
+---------- dynamic PostgreSQL claim/lease --+
                       |
                       v
                 processing pipeline
                       |
                       v
                DocumentBlock[]
                       |
                       v
                  AstraVector
                       |
              PostgreSQL + Qdrant
                       |
                       v
                 Retrieve by documentId
```

### 3.1 Core decision

AstraIndexator 1.0 SHALL support **N active replicas sharing one PostgreSQL coordination database**.

No replica owns a static partition of jobs. Jobs are dynamically claimed.

### 3.2 Durable queue principle

The PostgreSQL job table is the durable integration boundary between the producer and AstraIndexator.

If all AstraIndexator replicas are temporarily unavailable, accepted `PENDING` jobs remain durable and become processable after workers recover.

---

## 4. Business objective

A producer application must be able to submit a document once and preserve a stable document identity through:

```text
upload
→ indexation job
→ parsing/OCR
→ logical splitting
→ AstraVector ingestion
→ vector persistence
→ subsequent retrieve
```

The stable `documentId` is therefore a platform-wide identifier and must survive worker retries, reindexing and infrastructure failures.

---

## 5. Responsibility boundary

### 5.1 Spring Boot producer owns

- business `documentId`;
- producer-visible `documentVersion`;
- source upload to SeaweedFS;
- creation of the durable job intent;
- source object reference;
- original file metadata;
- access-zone input;
- TTL/expiration input;
- optional business metadata;
- initial `PENDING` state.

### 5.2 AstraIndexator owns

- claiming jobs;
- worker ownership/lease;
- processing attempts;
- acquisition/validation;
- parsing;
- OCR decisions/execution;
- normalization;
- logical splitting;
- prepared artifacts;
- delivery to AstraVector;
- retry/failure transitions;
- final completion state;
- processing diagnostics and metrics.

### 5.3 AstraVector owns

- tokenization;
- BGE-M3 model execution;
- dense/sparse representations;
- PostgreSQL/Qdrant projection;
- retrieval behavior;
- filtering using document/access/lifecycle metadata supplied by AstraIndexator.

---

## 6. Canonical identity model

The following identities MUST remain distinct:

```text
documentId
    != documentVersion
    != jobId
    != processingAttemptId
    != blockId
    != ingestionId
```

### 6.1 documentId

`documentId` is the stable logical identifier of the business document.

Requirements:

- generated/assigned before AstraIndexator processing;
- stable across retries;
- SHOULD remain stable across versions of the same logical document;
- MUST be propagated to every canonical block and AstraVector ingestion;
- MUST be usable as a retrieval filter;
- MUST NOT depend on SeaweedFS object key, worker identity or processing attempt.

Example:

```text
contract-2026-000123
```

### 6.2 documentVersion

`documentVersion` identifies a producer-visible revision of the logical document.

AstraIndexator treats it as an opaque string.

Examples:

```text
1
2
rev-17
sha256:...
```

Replacement/reindex semantics are defined in TZ-12.

### 6.3 jobId

`jobId` identifies one durable work item.

Recommended canonical type: UUID v7 or ULID. One implementation SHALL choose one format consistently.

One `documentId` MAY have multiple jobs over time:

```text
DOC-100
  |- JOB-1 initial index
  |- JOB-2 reindex
  `- JOB-3 new processing profile
```

### 6.4 processingAttemptId

One job may have multiple processing attempts because of retry or crash recovery.

Attempt identity is internal to AstraIndexator and must never replace the business `documentId`.

### 6.5 blockId

Each `DocumentBlock` must have a deterministic identifier scoped to document/version/processing contract as defined in TZ-09.

### 6.6 ingestionId

Delivery to AstraVector must have deterministic idempotency semantics so repeated delivery after ambiguous timeout does not duplicate logical content.

Exact construction belongs to TZ-11.

---

## 7. Source object contract

Spring Boot uploads the source before creating a processable job.

Canonical source reference:

```text
storage     = SEAWEEDFS
bucket      = documents          (when deployment uses logical buckets)
object_key  = original/DOC-100/v2/source.pdf
version_id  = optional
etag        = optional
```

`documentId` and `objectKey` MUST NOT be treated as the same identity.

A document may keep the same `documentId` while its storage location changes between versions.

AstraIndexator SHALL NOT accept unrestricted arbitrary HTTP URLs as a baseline source mechanism. This avoids turning the service into a generic downloader and avoids an unnecessary SSRF trust boundary.

---

## 8. Canonical job contract

A logical job record contains four groups of information:

```text
IndexationJob
|- identity
|- source
|- business/security/lifecycle intent
`- worker processing state
```

Recommended logical shape:

```json
{
  "jobId": "0198d2aa-7f00-7c23-9b10-000000000001",
  "documentId": "contract-2026-000123",
  "documentVersion": "2",
  "source": {
    "storage": "SEAWEEDFS",
    "bucket": "documents",
    "objectKey": "original/contract-2026-000123/v2/source.pdf",
    "versionId": null,
    "etag": null
  },
  "file": {
    "fileName": "contract.pdf",
    "declaredContentType": "application/pdf",
    "fileSize": 5230012
  },
  "access": {
    "accessZoneId": null,
    "accessZoneIds": ["6c84a7a8-1460-4fa7-a544-57c472667f51"],
    "accessZoneCode": null,
    "accessZoneCodes": ["LEGAL", "INTERNAL"]
  },
  "lifecycle": {
    "expiresAt": "2026-09-01T00:00:00Z"
  },
  "indexing": {
    "profile": "default"
  },
  "status": "PENDING",
  "createdAt": "2026-08-22T07:56:00Z"
}
```

This JSON is conceptual. PostgreSQL physical schema belongs to TZ-02.

---

## 9. Producer-owned fields

Spring Boot may populate only producer-contract fields.

Baseline producer-owned values:

```text
job_id
document_id
document_version
source_storage
source_bucket
source_object_key
source_version_id
source_etag
file_name
declared_content_type
file_size
indexing_profile
access_zone_id
access_zone_ids
access_zone_code
access_zone_codes
expires_at
business_metadata
status = PENDING
created_at
```

The producer MUST NOT set internal runtime values such as:

```text
worker_id
locked_at
lease_until
last_heartbeat_at
processing_stage
attempt_count
started_at
completed_at
last_error_code
last_error_message
```

---

## 10. Consumer-owned fields

AstraIndexator exclusively controls runtime processing fields such as:

```text
worker_id
locked_at
lease_until
last_heartbeat_at
processing_stage
attempt_count
max_attempts
next_retry_at
started_at
completed_at
last_error_code
last_error_message
updated_at
```

Spring Boot MUST NOT update job state after creation except through explicitly defined lifecycle operations introduced later (for example cancellation), if supported by TZ-12.

---

## 11. Job state abstraction

TZ-01 uses the following high-level states:

```text
PENDING
CLAIMED
PROCESSING
RETRY_WAIT
COMPLETED
FAILED
DEAD_LETTER
CANCELLED
```

Detailed processing stage may include:

```text
ACQUIRING
VALIDATING
PARSING
OCR_PROCESSING
NORMALIZING
SPLITTING
PREPARED
DELIVERING
```

`status` represents lifecycle. `processing_stage` represents pipeline progress.

Example:

```text
status = PROCESSING
processing_stage = OCR_PROCESSING
```

Exact transition rules belong to TZ-02.

---

## 12. Multi-replica processing requirement

AstraIndexator SHALL support multiple active replicas:

```text
Indexator-1 --\
Indexator-2 ----> PostgreSQL
Indexator-3 --/
```

Requirements:

1. only one active worker may own a specific job lease at a time;
2. job claim must be transactional;
3. the preferred PostgreSQL strategy uses `FOR UPDATE SKIP LOCKED`;
4. long-running processing must use a renewable lease;
5. a crashed worker must not permanently orphan work;
6. expired work must become recoverable;
7. AstraVector delivery must be idempotent because delivery may be replayed after ambiguous failures.

Illustrative claim principle:

```sql
SELECT id
FROM astra_indexator.indexation_job
WHERE status = 'PENDING'
   OR (status = 'RETRY_WAIT' AND next_retry_at <= now())
ORDER BY created_at
FOR UPDATE SKIP LOCKED
LIMIT :batch_size;
```

This SQL is illustrative only. Atomic claim/update, lease and race-condition handling are defined in TZ-02.

---

## 13. Access-zone contract

The producer compatibility contract may provide:

```text
accessZoneId
accessZoneIds[]
accessZoneCode
accessZoneCodes[]
```

AstraIndexator must normalize singular and plural forms without losing information:

```text
accessZoneId + accessZoneIds[]
        -> normalizedAccessZoneIds[]

accessZoneCode + accessZoneCodes[]
        -> normalizedAccessZoneCodes[]
```

Rules:

1. absent values are ignored;
2. invalid values are rejected according to contract validation;
3. duplicate exact values are deduplicated;
4. deterministic ordering is used for canonical hashing/persistence when needed;
5. a singular value MUST NOT be silently discarded when plural values are present;
6. the normalized access context must follow the entire pipeline;
7. every resulting `DocumentBlock` must retain the effective access context required by AstraVector;
8. AstraIndexator does not reinterpret business authorization semantics beyond validation/normalization/propagation.

Detailed ID/code relationship rules belong to TZ-10.

---

## 14. TTL and expiration

The producer may express lifecycle using a relative TTL or an absolute expiration depending on the producer-facing integration API.

Before processing, the durable job contract SHALL contain canonical absolute:

```text
expires_at TIMESTAMPTZ
```

If Spring Boot receives:

```text
ttlSeconds = 86400
```

it or the integration layer must normalize this exactly once using the accepted creation timestamp:

```text
acceptedAt + ttlSeconds -> expiresAt
```

Retries MUST NOT restart TTL.

If the document is already expired before useful processing can begin, behavior must be deterministic and defined in TZ-10/TZ-12.

---

## 15. File type detection principle

AstraIndexator MUST distinguish:

```text
file extension
declared MIME type
detected MIME/content signature
```

The producer-provided file name and MIME type are hints only.

Parser routing SHALL be based on trusted content detection plus explicit format policy.

Mismatch handling belongs to TZ-04.

---

## 16. Supported file-format baseline

AstraIndexator SHALL use tiered format support rather than claiming universal file compatibility.

### 16.1 Tier 1 — AstraIndexator 1.0 mandatory

| Format | Baseline processing |
|---|---|
| PDF with native text | native PDF/layout extraction |
| scanned PDF | page rendering + OCR |
| mixed PDF | per-page native extraction or OCR |
| JPEG/JPG | OCR |
| PNG | OCR |
| TIFF/TIF including multi-page | image/page OCR |
| DOCX | OOXML structured extraction |
| TXT | text decoding + normalization |
| Markdown | structure-aware text extraction |

### 16.2 Tier 2 — planned near-term

| Format | Processing |
|---|---|
| XLSX | spreadsheet-aware extraction |
| PPTX | slide-aware extraction |
| CSV | tabular extraction |
| HTML | DOM/content extraction from stored file |

### 16.3 Tier 3 — extended structured/image formats

```text
JSON
XML
WEBP
BMP
RTF
```

### 16.4 Tier 4 — legacy/conversion

```text
DOC
XLS
PPT
ODT
ODS
ODP
```

Legacy binary formats SHOULD be handled through an explicit conversion layer rather than contaminating the core parser contract with format-specific legacy behavior.

### 16.5 Explicitly not baseline 1.0

```text
ZIP / RAR / 7Z container ingestion
audio transcription
video transcription
arbitrary website crawling
arbitrary remote URL download
```

Archive extraction and ASR introduce different security/resource/model concerns and require separate decisions.

---

## 17. Parser extensibility contract

Format support must be plugin/handler-oriented conceptually:

```text
source
  -> FileTypeDetector
  -> FileTypeHandlerRegistry
  -> format-specific handler
  -> ParsedDocument
```

Representative handlers:

```text
PdfHandler
ImageHandler
DocxHandler
TextHandler
MarkdownHandler
XlsxHandler
PptxHandler
CsvHandler
HtmlHandler
```

The processing pipeline after parsing MUST NOT depend directly on the original file type.

All handlers produce the canonical intermediate representation defined in TZ-05/TZ-09.

---

## 18. Canonical parsed-document principle

All formats converge to a common structural representation such as:

```text
ParsedDocument
|- metadata
`- elements[]
    |- HEADING
    |- PARAGRAPH
    |- LIST
    |- TABLE
    |- IMAGE
    |- OCR_TEXT
    `- other explicitly defined structural elements
```

This allows the downstream flow to remain format-independent:

```text
PDF ----\
DOCX ----\
IMAGE ----> ParsedDocument -> Normalizer -> Logical Splitter -> DocumentBlock[]
TXT ------/
MD -------/
```

Exact element DTOs belong to TZ-05/TZ-09.

---

## 19. Images inside documents

Images embedded inside PDF, DOCX, PPTX and future rich formats are first-class document elements, not disposable parser side effects.

### 19.1 Core rule

The parser must preserve image existence and provenance even when OCR is not executed.

Representative conceptual element:

```json
{
  "elementId": "img-17",
  "type": "IMAGE",
  "page": 5,
  "order": 17,
  "bbox": [100, 200, 900, 700],
  "mimeType": "image/png",
  "width": 1200,
  "height": 800,
  "ocrCandidate": true,
  "origin": {
    "documentId": "DOC-100"
  }
}
```

### 19.2 PDF image scenarios

AstraIndexator must distinguish:

1. scanned page represented mainly by an image and without usable native text;
2. mixed page with usable native text plus embedded images;
3. image-only diagram/table/screenshot inside an otherwise textual page.

For scanned pages:

```text
page -> render -> OCR whole page
```

For mixed pages:

```text
native text -> preserve
embedded image -> OCR candidate decision
```

AstraIndexator MUST avoid blindly OCR-processing all PDF images because that would create noise and duplicates from logos, decoration, repeated headers, signatures and background assets.

### 19.3 DOCX images

DOCX parsing should preserve ordering such as:

```text
Heading
Paragraph
Image
Caption
Paragraph
```

The system MUST NOT flatten DOCX to plain text while silently dropping image context.

### 19.4 PPTX images

When PPTX support is introduced, image provenance should preserve at least:

```text
slide number
shape/order
bounding box
associated caption/text where identifiable
```

### 19.5 Image OCR policy

The baseline policy model should support:

```text
OFF
OCR_IF_NEEDED
OCR_ALL
```

Recommended production default:

```text
OCR_IF_NEEDED
```

### 19.6 OCR candidate heuristics

Deterministic 1.0 heuristics may include:

```text
very small decorative image       -> skip
repeated logo on many pages       -> skip
background/decorative asset       -> skip
large screenshot                  -> OCR candidate
diagram with labels               -> OCR candidate
image containing table            -> OCR candidate
full-page scan                    -> OCR required
```

Detailed thresholds and OCR model behavior belong to TZ-06.

### 19.7 OCR result provenance

OCR text must remain linked to the source image/page:

```json
{
  "type": "OCR_TEXT",
  "originElementId": "img-17",
  "text": "recognized text",
  "ocrModelVersion": "...",
  "confidence": 0.94
}
```

### 19.8 Duplicate-content prevention

Native text and OCR output may overlap. The pipeline must have deterministic duplicate/overlap handling so the same textual region is not indexed twice merely because both native extraction and OCR saw it.

Possible signals include page identity, region overlap, normalized-text similarity and image/hash provenance. Exact algorithm belongs to TZ-06/TZ-07.

### 19.9 Image is not a separate business document by default

An embedded image normally inherits the parent `documentId`.

OCR-derived blocks should remain retrievable through the parent document identity:

```json
{
  "documentId": "DOC-100",
  "blockId": "DOC-100:000042",
  "sourceType": "IMAGE_OCR",
  "pageFrom": 7,
  "pageTo": 7,
  "originElementId": "img-17",
  "text": "..."
}
```

---

## 20. Retrieve continuity requirement

The entire ingestion chain must preserve the producer's document identity so AstraVector can support retrieval constrained by one or more documents.

Conceptual retrieve request:

```json
{
  "query": "Какой срок действия договора?",
  "documentIds": ["contract-2026-000123"]
}
```

Conceptual AstraVector filtering:

```text
semantic query
AND documentId IN (...)
AND access-zone constraints
AND expiration constraints
```

TZ-01 does not define the AstraVector public retrieve API, but it establishes the identity metadata required for that API to work reliably.

---

## 21. Transactional producer submission

The producer workflow should guarantee that a processable job does not point to a source object that was never successfully published.

Canonical order:

```text
1. generate/resolve documentId
2. upload source to SeaweedFS
3. verify successful upload/object reference
4. begin PostgreSQL transaction
5. insert IndexationJob(status=PENDING)
6. commit
```

If source upload fails, the job must not become processable.

If DB insertion fails after a successful upload, the source object may become orphaned; object cleanup/reconciliation rules belong to TZ-03/TZ-13.

The system must not rely on a distributed XA transaction between PostgreSQL and SeaweedFS.

---

## 22. Job immutability principle

Once accepted, the original processing intent must not be silently mutated.

In particular, the following values SHOULD be immutable for a given job:

```text
documentId
documentVersion
source reference
normalized access context
canonical expiresAt
indexing profile
```

A business change should normally create a new version/job rather than modify historical processing intent in place.

---

## 23. Idempotency requirements

The architecture requires idempotency at multiple boundaries.

### 23.1 Producer job creation

Spring Boot should use a stable producer request/business key or enforce an equivalent unique database constraint so client-level retries do not create accidental duplicate jobs.

Exact database key strategy is defined in TZ-02/TZ-12.

### 23.2 Worker execution

A processing attempt may repeat after worker failure. Re-execution must not corrupt durable state.

### 23.3 AstraVector delivery

AstraIndexator may resend the same logically prepared document if AstraVector accepted data but the acknowledgement was lost.

Therefore AstraVector ingestion MUST be idempotent using deterministic ingestion identity.

---

## 24. Validation requirements

Before a job becomes eligible for processing, the contract must validate at least:

- non-empty `documentId`;
- valid/allowed source storage;
- non-empty object key;
- bounded file name and metadata lengths;
- valid indexing profile;
- valid access-zone syntax;
- valid canonical expiration;
- initial status exactly `PENDING`;
- job identifier uniqueness;
- producer-owned fields only.

During acquisition AstraIndexator additionally validates:

- source existence;
- source readability;
- actual size;
- magic/content signature;
- supported format;
- MIME/extension inconsistencies;
- configured maximum size/page/image limits.

Those checks belong to TZ-04.

---

## 25. Security requirements

The job contract must not contain storage credentials, bearer tokens, passwords or other secrets.

AstraIndexator obtains SeaweedFS/PostgreSQL/Nexus/AstraVector credentials from runtime secret configuration, not per-job payload.

Object keys must be treated as untrusted data and validated against storage policy.

Original file names must never be used directly as unrestricted local filesystem paths.

Detailed threat model belongs to TZ-16.

---

## 26. Observability requirements

Every processing log/event should be correlatable using bounded identifiers such as:

```text
jobId
documentId
processingAttemptId
workerId
correlation/trace ID where available
processingStage
```

High-cardinality identifiers should not be blindly used as metric labels.

Useful metrics include:

```text
jobs_pending
jobs_processing
jobs_retry_wait
jobs_failed
job_claim_total
job_recovery_total
processing_duration_seconds
ocr_duration_seconds
source_download_bytes
astravector_delivery_total
```

Detailed metrics/logging/tracing contract belongs to TZ-14.

---

## 27. Non-functional requirements

AstraIndexator 1.0 design SHALL support:

- horizontal scaling with multiple active workers;
- no static worker partition ownership;
- bounded job concurrency per replica;
- separate resource limits for heavy OCR work where necessary;
- durable queue semantics through PostgreSQL;
- crash recovery through lease expiration;
- idempotent delivery to AstraVector;
- large-document processing without unbounded memory use;
- deterministic temporary workspace cleanup;
- testable operation with real PostgreSQL and object storage;
- versioned parser/OCR/splitter contracts;
- no dependence on BGE-M3 tokenizer/model inside AstraIndexator.

---

## 28. Out of scope of TZ-01

This specification deliberately does not define:

- exact PostgreSQL DDL;
- exact indexes/constraints;
- exact claim/update SQL transaction;
- lease duration;
- heartbeat interval;
- retry backoff formula;
- poison-job policy;
- SeaweedFS directory/object lifecycle implementation;
- precise file-size/page-count thresholds;
- PDF parser library;
- OCR engine/model selection;
- OCR classifier implementation;
- exact `ParsedDocument` DTO;
- exact `DocumentBlock` DTO;
- AstraVector wire transport/batching;
- delete/reindex replacement algorithm;
- retrieval endpoint implementation.

These are intentionally delegated to TZ-02 through TZ-18.

---

## 29. End-to-end sequence

```text
Spring Boot          SeaweedFS         PostgreSQL        Indexator-N        AstraVector
    |                    |                  |                  |                  |
    | resolve docId      |                  |                  |                  |
    |------------------->| upload source    |                  |                  |
    |<-------------------| object ref       |                  |                  |
    |                                       |                  |                  |
    |-------------------------------------->| INSERT PENDING   |                  |
    |<--------------------------------------| committed        |                  |
    |                                       |                  |                  |
    |                                       |<-----------------| claim eligible   |
    |                                       |----------------->| job + lease      |
    |                                       |                  |                  |
    |                    |<------------------------------------| download source  |
    |                    |------------------------------------>| source bytes     |
    |                                       |                  |                  |
    |                                       |                  | parse/OCR/split  |
    |                                       |                  |----------------->|
    |                                       |                  | DocumentBlock[]  |
    |                                       |                  |<-----------------|
    |                                       |                  | durable ACK      |
    |                                       |<-----------------| COMPLETED        |
```

---

## 30. Acceptance criteria

TZ-01 implementation is accepted when the following can be demonstrated.

### AC-01 — Durable producer submission

After Spring Boot reports successful job creation, a committed `PENDING` job exists in PostgreSQL and references a successfully uploaded source object.

### AC-02 — Stable document identity

The same `documentId` is traceable from producer job through AstraIndexator output into AstraVector document metadata.

### AC-03 — Job/document separation

A single `documentId` can have multiple distinct jobs without identity collision.

### AC-04 — Multi-replica compatibility

At least three AstraIndexator replicas can compete for jobs without concurrently owning the same job lease.

### AC-05 — Worker crash recoverability

A worker crash cannot permanently orphan a job; ownership can be recovered according to TZ-02 lease semantics.

### AC-06 — Idempotent downstream delivery

Replaying a logically identical AstraVector ingestion after an ambiguous failure does not create duplicate logical document content.

### AC-07 — Access-zone losslessness

All supplied distinct `accessZoneId(s)` and `accessZoneCode(s)` values survive normalization and appear in the canonical downstream document context as required.

### AC-08 — TTL stability

A relative TTL is canonicalized once to absolute expiration and retries do not extend it.

### AC-09 — Source identity separation

Changing SeaweedFS object location/version does not require changing the logical `documentId`.

### AC-10 — Format detection

AstraIndexator does not trust extension/MIME hint alone and validates actual content type before parser selection.

### AC-11 — Tier 1 format coverage

Automated integration tests demonstrate supported processing for native PDF, scanned PDF, mixed PDF, JPEG/PNG/TIFF, DOCX, TXT and Markdown.

### AC-12 — Embedded image preservation

A rich document containing an embedded image preserves that image as a document element/provenance record even when OCR is skipped.

### AC-13 — Conditional image OCR

A scanned page or useful screenshot/table image can produce OCR text while a repeated decorative/logo image can be skipped according to configured policy.

### AC-14 — OCR/native duplicate control

A mixed PDF test demonstrates that the same textual region is not blindly indexed twice when native extraction and OCR overlap.

### AC-15 — Retrieve continuity

After successful indexing, a downstream retrieval request constrained by the original producer `documentId` can target the indexed document and its OCR-derived blocks.

### AC-16 — Ownership enforcement

Producer code cannot legitimately overwrite worker-owned lease/attempt/state fields through the normal submission contract.

### AC-17 — No tokenization leakage

AstraIndexator processing and splitting tests do not require the AstraVector BGE-M3 tokenizer or embedding model.

---

## 31. Required verification evidence

Implementation work based on TZ-01 should provide:

- PostgreSQL integration tests for producer submission;
- multi-worker contention test;
- worker crash/recovery test;
- duplicate/replayed delivery test;
- access-zone normalization tests;
- TTL canonicalization/retry tests;
- source/MIME mismatch tests;
- Tier 1 format fixture tests;
- scanned/native/mixed PDF fixtures;
- DOCX embedded-image fixture;
- image OCR candidate/skip fixtures;
- provenance assertions;
- retrieve-by-documentId E2E evidence against AstraVector-compatible integration;
- evidence that no embedding/tokenizer dependency exists in AstraIndexator.

---

## 32. Architectural invariants established by TZ-01

The following are now normative for AstraIndexator 1.0:

1. Spring Boot uploads the source object before submitting processable work.
2. PostgreSQL is the durable asynchronous job coordination boundary.
3. AstraIndexator supports multiple active replicas.
4. `documentId` is the stable cross-system document identity used later for retrieval.
5. `jobId` and processing-attempt identity are separate from `documentId`.
6. SeaweedFS physical object identity is separate from business document identity.
7. Access-zone and expiration context are immutable processing intent once accepted.
8. File type is detected from content rather than trusted from extension alone.
9. Tier 1 format support includes PDF, images, DOCX, TXT and Markdown.
10. Embedded images are first-class parsed elements with provenance.
11. OCR is conditional by policy; OCR-all is not the default.
12. OCR/native overlap must not create uncontrolled duplicate text.
13. AstraIndexator produces logical document content only; AstraVector owns tokenization, embeddings and retrieval.
14. Idempotency is required at producer, worker recovery and AstraVector delivery boundaries.

---

## 33. Next specification dependency

The next mandatory specification is **TZ-02 — Job Coordinator & PostgreSQL**.

TZ-02 must turn this logical job contract into an implementation-ready persistence/coordinator design covering:

```text
DDL
constraints
indexes
producer/consumer ownership
FOR UPDATE SKIP LOCKED claim transaction
lease
heartbeat
lease renewal
expired-lease recovery
attempt history
retry/backoff
DEAD_LETTER
cancellation races
worker shutdown
multi-replica contention tests
```

No production coordinator implementation should begin until TZ-02 resolves those concurrency semantics explicitly.
