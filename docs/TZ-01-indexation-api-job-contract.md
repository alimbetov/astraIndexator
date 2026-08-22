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
4. returns control to the caller without waiting for parsing, OCR, logical fragmentation or AstraVector indexing.

AstraIndexator:

1. runs as one or more active worker replicas;
2. claims eligible jobs from PostgreSQL;
3. loads the source object from SeaweedFS;
4. parses and OCR-processes the source when required;
5. creates canonical `ParsedDocument`/`DocumentElement[]` and `LogicalFragment[]` according to TZ-09;
6. maps the canonical model to AstraVector public-facade `LogicalBlock[]` according to TZ-11;
7. sends the document through `AstraVectorIngestionFacade`;
8. persists processing state and completion/failure information.

The detailed claim/lease/heartbeat/retry SQL and state machine are defined in TZ-02. Access-zone and TTL semantics are defined in TZ-10. AstraVector transport/session behavior is defined in TZ-11.

---

## 3. Architectural baseline

```text
Spring Boot producer
    |
    | determine documentId/documentVersion
    | upload source object
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
          parse / OCR / normalize / split
                       |
                       v
              LogicalFragment[]
                       |
                       v
         map to AstraVector LogicalBlock[]
                       |
                       v
          AstraVectorIngestionFacade
                       |
              PostgreSQL + Qdrant
                       |
                       v
                 Retrieve by documentId
```

### 3.1 Multi-replica decision

AstraIndexator 1.0 SHALL support **N active replicas sharing one PostgreSQL coordination database**.

No replica owns a static partition of jobs. Jobs are dynamically claimed according to TZ-02.

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
→ logical fragmentation
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
- access-zone assignment supplied by the trusted platform/business boundary;
- optional TTL request intent that is representable by the selected AstraVector ingestion path;
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
- multilingual logical fragmentation;
- prepared artifacts;
- mapping canonical elements/fragments to AstraVector `LogicalBlock[]`;
- AstraVector ingestion/session orchestration;
- retry/failure transitions;
- final completion state;
- processing diagnostics and metrics.

### 5.3 AstraVector owns

- access-zone registry resolution and effective zone validation;
- effective TTL policy/lifecycle enforcement;
- tokenizer-aware chunking;
- BGE-M3 model execution;
- dense/sparse representations;
- canonical vector/document state;
- outbox/Qdrant projection;
- activation/reconciliation;
- expired-content search exclusion and cleanup;
- retrieval behavior.

AstraIndexator SHALL NOT duplicate AstraVector's code-to-TTL policy, tokenizer-aware chunking or vector lifecycle logic.

---

## 6. Canonical identity model

The following identities MUST remain distinct:

```text
documentId
    != documentVersion
    != jobId
    != processingAttemptId
    != elementId
    != fragmentId
    != AstraVector ingestionSessionId
    != AstraVector chunk IDs
```

### 6.1 documentId

`documentId` is the stable logical identifier of the business document.

Requirements:

- generated/assigned before AstraIndexator processing;
- stable across retries;
- SHOULD remain stable across versions of the same logical document;
- MUST be propagated to the canonical document and AstraVector ingestion;
- MUST be usable for later retrieval/document lifecycle operations;
- MUST NOT depend on SeaweedFS object key, worker identity or processing attempt.

### 6.2 documentVersion

`documentVersion` identifies a producer-visible revision of the logical document.

AstraVector public ingestion uses a positive numeric version. Therefore the producer/AstraIndexator contract SHOULD use a positive integer version for direct interoperability. If an upstream system has an opaque revision string, its mapping to numeric `documentVersion` must be explicit and durable rather than recomputed ad hoc.

Replacement/reindex semantics are defined in TZ-12.

### 6.3 jobId

`jobId` identifies one durable AstraIndexator work item.

One `documentId` MAY have multiple jobs over time.

### 6.4 processingAttemptId

One job may have multiple processing attempts because of retry or crash recovery.

Attempt identity is internal to AstraIndexator and never replaces `documentId`.

### 6.5 elementId / fragmentId

Deterministic canonical identities are defined in TZ-09. They are not AstraVector internal chunk IDs.

### 6.6 downstream idempotency identity

Retries of the same logical AstraVector operation MUST reuse the same idempotency identity. Exact construction and session/batch hashing belong to TZ-11.

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

AstraIndexator SHALL NOT accept unrestricted arbitrary HTTP URLs as a baseline source mechanism.

---

## 8. Canonical job contract

Conceptual shape:

```json
{
  "jobId": "0198d2aa-7f00-7c23-9b10-000000000001",
  "documentId": "a4d2c7b1-83d6-4f82-a1b6-7a2f5143a012",
  "documentVersion": 2,
  "source": {
    "storage": "SEAWEEDFS",
    "bucket": "documents",
    "objectKey": "original/a4d2c7b1-83d6-4f82-a1b6-7a2f5143a012/v2/source.pdf",
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
    "accessZoneIds": [],
    "accessZoneCode": "1500",
    "accessZoneCodes": []
  },
  "ttl": {
    "mode": "INHERIT_ZONE_POLICY",
    "ttlDays": null
  },
  "indexing": {
    "profile": "default"
  },
  "status": "PENDING",
  "createdAt": "2026-08-22T07:56:00Z"
}
```

The JSON is conceptual. Physical PostgreSQL schema belongs to TZ-02.

---

## 9. Producer-owned fields

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
requested_ttl_mode
requested_ttl_days
requested_ttl_seconds      optional compatibility field
requested_expires_at       optional compatibility field
business_metadata
status = PENDING
created_at
```

Spring Boot MUST NOT populate worker runtime fields.

---

## 10. Consumer-owned fields

AstraIndexator exclusively controls runtime processing fields such as:

```text
worker_id
locked_at
lease_until
lease_generation
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

Spring Boot MUST NOT update job state after creation except through explicitly defined lifecycle operations introduced by TZ-12.

---

## 11. Job state abstraction

Top-level lifecycle follows TZ-02:

```text
PENDING
PROCESSING
RETRY_WAIT
COMPLETED
FAILED
DEAD_LETTER
CANCELLED
```

Detailed processing stages may include:

```text
ACQUIRING
VALIDATING
PARSING
OCR_PROCESSING
NORMALIZING
SPLITTING
PREPARING_ARTIFACTS
REGISTERING_DOCUMENT
DELIVERING_FRAGMENTS / APPENDING_BLOCKS
FINALIZING
FINALIZING_VECTOR_STATE
```

`status` represents lifecycle. `processing_stage` represents pipeline progress.

---

## 12. Multi-replica processing requirement

AstraIndexator SHALL support multiple active replicas using the TZ-02 claim/lease/fencing model.

Core requirements:

1. one current lease owner per job;
2. transactional claim;
3. `FOR UPDATE SKIP LOCKED`-based coordination;
4. renewable lease and heartbeat;
5. monotonic fencing generation;
6. crashed-worker recovery;
7. idempotent/reconcilable AstraVector delivery.

---

## 13. Access-zone contract

The producer compatibility surface MAY provide:

```text
accessZoneId
accessZoneIds[]
accessZoneCode
accessZoneCodes[]
```

### 13.1 Access-zone code format

`accessZoneCode` is a four-character numeric string:

```regex
^[0-9]{4}$
```

Valid range:

```text
0000 .. 9999
```

Leading zeroes are significant. Codes such as `LEGAL` or `INTERNAL` are invalid under the current AstraVector contract.

### 13.2 Normalization

Singular and plural values are combined within their representation and deduplicated. If IDs and codes are both supplied, they must resolve to the same effective zone set; they are not independent unions.

### 13.3 One-zone ingestion invariant

AstraVector ingestion is scoped to exactly one effective access zone per indexed document version.

Therefore one AstraIndexator job MUST normalize to exactly one distinct effective zone before downstream ingestion.

A request such as:

```json
{"accessZoneCodes":["1500","2500"]}
```

is invalid for one ingestion job. AstraIndexator SHALL NOT silently fan-out one job into multiple zones.

Plural selectors remain useful for producer/retrieval compatibility, but multi-zone retrieval is a separate read concern.

Detailed semantics, registry states and code/TTL matrix are defined in TZ-10.

---

## 14. TTL intent

AstraIndexator SHALL preserve producer TTL intent but SHALL NOT independently become the authority for effective document expiry.

For the production-oriented session ingestion path, the interoperable field is:

```text
ttl_days
```

Semantics:

```text
0  -> inherit effective access-zone/platform TTL policy
>0 -> request explicit relative finite lifetime in days
```

`ttl_days=0` MUST NOT be documented or interpreted as unconditional never-expire.

The full single-call facade contains `TtlPolicy` with relative/absolute wire shapes, but exact absolute-expiry persistence is not yet a stable cross-service guarantee. AstraIndexator SHALL NOT silently convert an absolute timestamp to approximate days.

The access-zone code matrix is owned by AstraVector registry/configuration. AstraIndexator MAY document it for compatibility tests but MUST NOT independently calculate authoritative TTL from the code.

Detailed lifecycle semantics are defined in TZ-10/TZ-11/TZ-12.

---

## 15. File type detection principle

AstraIndexator MUST distinguish:

```text
file extension
declared MIME type
detected MIME/content signature
```

Producer file name and MIME type are hints only. Parser routing is based on trusted content detection plus explicit format policy.

---

## 16. Supported file-format baseline

### Tier 1 — AstraIndexator 1.0 mandatory

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

### Tier 2 — planned near-term

```text
XLSX
PPTX
CSV
HTML
```

### Tier 3 — extended

```text
JSON
XML
WEBP
BMP
RTF
```

### Tier 4 — legacy/conversion

```text
DOC
XLS
PPT
ODT
ODS
ODP
```

Not baseline 1.0:

```text
ZIP / RAR / 7Z container ingestion
audio/video transcription
arbitrary website crawling
arbitrary remote URL download
```

---

## 17. Parser extensibility contract

Format support must be handler-oriented:

```text
source
  -> FileTypeDetector
  -> FileTypeHandlerRegistry
  -> format-specific handler
  -> ParsedDocument
```

All handlers converge to the canonical model defined by TZ-09.

---

## 18. Canonical parsed-document principle

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

Downstream:

```text
ParsedDocument
  -> Normalizer
  -> Logical Splitter
  -> LogicalFragment[]
  -> AstraVector LogicalBlock[] mapping
```

---

## 19. Images inside documents

Images embedded inside PDF, DOCX, PPTX and future rich formats are first-class document elements.

The parser must preserve image existence/provenance even when OCR is not executed.

Baseline OCR policy:

```text
OFF
OCR_IF_NEEDED
OCR_ALL
```

Recommended production default:

```text
OCR_IF_NEEDED
```

The system must distinguish scanned pages, mixed PDF pages, embedded screenshots/tables/diagrams and decorative/repeated images. OCR-derived text remains linked to the source image/page and inherits the parent `documentId`.

Native extraction and OCR overlap must not create uncontrolled duplicate text.

Detailed thresholds/heuristics belong to TZ-06/TZ-07.

---

## 20. Retrieve continuity requirement

The ingestion chain must preserve the original `documentId`, `documentVersion`, source provenance and effective access-zone identity so retrieval can return correct citations and scope.

AstraIndexator does not define the public retrieval API, but it must provide the metadata required by AstraVector's retrieval facade.

---

## 21. Transactional producer submission

Canonical order:

```text
1. generate/resolve documentId + numeric documentVersion
2. upload source to SeaweedFS
3. verify successful upload/object reference
4. begin PostgreSQL transaction
5. insert IndexationJob(status=PENDING)
6. commit
```

If source upload fails, the job must not become processable.

If DB insertion fails after upload, the object may become orphaned; cleanup/reconciliation belongs to TZ-03/TZ-13.

No XA transaction between PostgreSQL and SeaweedFS is assumed.

---

## 22. Job immutability principle

Once accepted, the original intent must not be silently mutated.

Immutable for one job:

```text
documentId
documentVersion
source reference
requested access-zone selectors
normalized one-zone ingestion scope
requested TTL intent
indexing profile
```

A business change should normally create a new version/job.

---

## 23. Idempotency requirements

Idempotency is required at three boundaries:

1. producer job creation;
2. worker crash/retry execution;
3. AstraVector ingestion/session operations.

Same logical operation retries MUST reuse deterministic downstream idempotency/session identities according to TZ-11.

---

## 24. Validation requirements

Before processing, validate at least:

- non-empty valid `documentId` for the chosen downstream mapping;
- `documentVersion > 0`;
- source storage and object key;
- bounded file/metadata fields;
- valid indexing profile;
- access-zone code syntax `^[0-9]{4}$` when code supplied;
- UUID syntax when ID supplied;
- exactly one distinct effective ingestion zone after normalization/resolution;
- supported TTL intent for the selected downstream path;
- initial status `PENDING`;
- job identifier uniqueness;
- producer-owned fields only.

During acquisition AstraIndexator additionally validates source existence, size, content signature, supported format and configured resource limits.

---

## 25. Security requirements

The job contract must not contain storage credentials, bearer tokens, passwords or secrets.

Access-zone assignment comes from the trusted platform/business boundary and is never inferred from document text/OCR.

Missing/invalid/mismatched/disabled/deleted zones fail closed according to TZ-10.

Detailed trust/gateway/secret requirements belong to TZ-16.

---

## 26. Observability requirements

Logs/events should be correlatable using bounded identifiers such as:

```text
jobId
documentId
processingAttemptId
workerId
correlation/trace ID
processingStage
accessZoneCode or accessZoneId where security policy permits
AstraVector ingestionSessionId where applicable
```

High-cardinality identifiers must not be blindly used as metric labels.

---

## 27. Non-functional requirements

AstraIndexator 1.0 SHALL support:

- horizontal scaling with multiple active workers;
- bounded concurrency/resource use;
- durable PostgreSQL queue semantics;
- lease/fencing crash recovery;
- idempotent AstraVector delivery;
- large-document streaming/session ingestion;
- deterministic prepared artifacts and temporary cleanup;
- testability with real PostgreSQL/object storage/AstraVector contracts;
- no BGE-M3 tokenizer/model dependency inside AstraIndexator.

---

## 28. Out of scope of TZ-01

TZ-01 does not define exact PostgreSQL DDL, SeaweedFS lifecycle, parser library, OCR model, exact canonical DTOs, exact AstraVector gRPC mapping/session hashes, document replacement/deletion or retrieval ranking.

Those belong to TZ-02 through TZ-18.

---

## 29. End-to-end sequence

```text
Spring Boot      SeaweedFS      PostgreSQL      Indexator-N      AstraVector
    |                |              |                |                |
    | upload source  |              |                |                |
    |--------------->|              |                |                |
    |<---------------| object ref   |                |                |
    |                               |                |                |
    |------------------------------>| INSERT PENDING |                |
    |<------------------------------| committed      |                |
    |                               |                |                |
    |                               |<---------------| claim/lease    |
    |                               |--------------->| job            |
    |                |<------------------------------| download       |
    |                |------------------------------>| bytes          |
    |                               |                |                |
    |                               |                | parse/OCR      |
    |                               |                | normalize/split|
    |                               |                | map blocks     |
    |                               |                |--------------->|
    |                               |                | facade/session |
    |                               |                |<---------------|
    |                               |                | status/result  |
    |                               |<---------------| COMPLETED      |
```

`FinalizeLogicalDocumentIngestion` success alone MUST NOT be assumed to mean searchable; TZ-11 defines status reconciliation with `GetLogicalDocumentIngestionStatus` and `GetDocumentVectorStatus`.

---

## 30. Acceptance criteria

### AC-01 — Durable producer submission

A committed `PENDING` job references a successfully uploaded source object.

### AC-02 — Stable identity

The same `documentId`/`documentVersion` is traceable from producer through AstraIndexator into AstraVector.

### AC-03 — Multi-replica compatibility

At least three replicas can compete without concurrently owning the same current lease.

### AC-04 — Crash recoverability

A worker crash cannot permanently orphan a job.

### AC-05 — Idempotent downstream delivery

Replaying the same logical downstream operation does not create duplicate logical content.

### AC-06 — Numeric access-zone code compatibility

Four-digit codes such as `0001`, `1500`, `9999` remain strings and survive without losing leading zeroes.

### AC-07 — One-zone ingestion

Each processable job resolves to exactly one effective access zone. Multiple distinct zones are rejected rather than silently fanned out.

### AC-08 — ID/code consistency

When both are supplied, access-zone IDs and codes must resolve to the same effective zone.

### AC-09 — TTL inherit semantics

Session `ttl_days=0` is propagated/interpreted as inherit zone/platform policy, never unconditional never-expire.

### AC-10 — No duplicated TTL matrix

AstraIndexator does not compute authoritative effective TTL from access-zone code; AstraVector registry/policy remains authoritative.

### AC-11 — No silent absolute-TTL approximation

Unsupported absolute/second precision is not silently converted to approximate session days.

### AC-12 — Format detection

Parser selection uses detected content, not extension alone.

### AC-13 — Tier 1 format coverage

Native/scanned/mixed PDF, JPEG/PNG/TIFF, DOCX, TXT and Markdown have integration fixtures.

### AC-14 — Embedded image preservation

Embedded images retain provenance even when OCR is skipped.

### AC-15 — Conditional OCR and duplicate control

Useful image text can be OCR'd while decorative images can be skipped, and native/OCR duplicates are controlled.

### AC-16 — Retrieve continuity

After successful indexing, retrieval can return the original document identity/provenance within the correct access-zone scope.

### AC-17 — Ownership enforcement

Producer code cannot legitimately overwrite worker lease/attempt/runtime state through the normal submission contract.

### AC-18 — No tokenization leakage

AstraIndexator processing tests do not require AstraVector's BGE-M3 tokenizer/model.

---

## 31. Required verification evidence

Implementation should provide:

- PostgreSQL producer-submission tests;
- multi-worker contention and crash/recovery tests;
- access-zone format/leading-zero/one-zone/mismatch tests;
- TTL inherit/positive-days/unsupported-absolute tests;
- generated-protobuf or protocol-compatible AstraVector contract tests;
- source/MIME mismatch tests;
- Tier 1 format fixtures;
- OCR/image/provenance fixtures;
- large-document session ingestion tests;
- replay/idempotency tests;
- retrieve continuity evidence.

---

## 32. Architectural invariants established by TZ-01

1. Spring Boot uploads the source before submitting processable work.
2. PostgreSQL is the durable asynchronous job coordination boundary.
3. AstraIndexator supports multiple active replicas.
4. `documentId` is the stable cross-system document identity.
5. Job/attempt/storage identities remain separate from document identity.
6. Access-zone code is a four-digit string `0000..9999` under the current AstraVector contract.
7. One indexing job/document version resolves to one effective access zone.
8. AstraIndexator does not silently fan-out one job across zones.
9. AstraVector access-zone registry owns effective zone validation and TTL policy.
10. Session `ttl_days=0` means inherit policy, not unconditional never-expire.
11. Exact absolute-expiry behavior is not assumed until stabilized downstream.
12. File type is detected from content rather than trusted from extension alone.
13. Embedded images are first-class parsed elements with provenance.
14. OCR is conditional; native/OCR overlap must not create uncontrolled duplicates.
15. AstraIndexator owns structural/logical processing; AstraVector owns tokenizer-aware chunking, embeddings, vector lifecycle and retrieval.
16. Idempotency is required at producer, recovery and AstraVector boundaries.

---

## 33. Next specification dependency

TZ-02, TZ-08, TZ-09 and TZ-10 are now baseline prerequisites.

The next critical specification is **TZ-11 — AstraVector Integration**.

TZ-11 must map AstraIndexator's canonical model to the actual public facade and define:

```text
IndexLogicalDocument vs session ingestion selection
LogicalFragment/DocumentElement -> LogicalBlock mapping
Start/Append/Finalize/Abort/Status flow
idempotency_key
batch_content_hash
final_content_hash
ingestion session recovery
access_zone_id/access_zone_code mapping
ttl_days/TtlPolicy mapping
gRPC deadlines/retry classification
operation status reconciliation
GetDocumentVectorStatus readiness/searchability proof
```

No production AstraVector adapter should be implemented against an invented REST/DTO contract when the public protobuf facade already exists.
