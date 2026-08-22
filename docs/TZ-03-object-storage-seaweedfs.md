# TZ-03 — Object Storage / SeaweedFS

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-03
- **Title:** Object Storage / SeaweedFS
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-02, TZ-04, TZ-05, TZ-06, TZ-07, TZ-09, TZ-11, TZ-12, TZ-13, TZ-14, TZ-16, TZ-17, TZ-18
- **Primary storage:** SeaweedFS

---

## 2. Purpose

This specification defines how AstraIndexator uses SeaweedFS for:

1. immutable source-document acquisition;
2. prepared canonical artifacts;
3. crash-safe processing checkpoints;
4. replay without reparsing/OCR when compatible artifacts exist;
5. retention and cleanup independent from AstraVector vector state.

SeaweedFS is an object/blob storage dependency. It is not the AstraIndexator job coordinator, not the business document database and not the AstraVector source of truth.

The canonical ownership model is:

```text
PostgreSQL
  -> authoritative job/coordinator state

SeaweedFS
  -> source binaries
  -> prepared/replayable processing artifacts

AstraVector PostgreSQL
  -> authoritative indexed/vector document state

Qdrant
  -> rebuildable search projection owned by AstraVector
```

---

## 3. Architectural goals

The object-storage design MUST:

1. preserve immutable source identity for one accepted document version;
2. support large files without loading them fully into RAM;
3. support multiple AstraIndexator replicas concurrently;
4. avoid one object per logical fragment as the default storage model;
5. avoid one unbounded monolithic artifact for very large documents;
6. provide deterministic, versioned prepared artifacts;
7. provide manifest-based integrity validation;
8. allow recovery from another worker/pod/node;
9. distinguish published artifacts from partially written artifacts;
10. avoid dependence on worker-local disk for durable recovery;
11. keep source retention separate from prepared-artifact retention;
12. avoid coupling SeaweedFS deletion to AstraVector/Qdrant deletion;
13. keep object references credential-free;
14. support future replacement of SeaweedFS through a narrow storage adapter.

---

## 4. Non-goals

TZ-03 does not define:

- producer REST upload API;
- exact SeaweedFS cluster topology;
- filer/volume-server replication configuration;
- parser libraries;
- OCR model selection;
- document MIME validation rules;
- logical splitter behavior;
- AstraVector gRPC ingestion details;
- Kubernetes PersistentVolume configuration;
- enterprise backup/RPO/RTO policy.

Those concerns belong to TZ-04/TZ-05/TZ-06/TZ-08/TZ-11/TZ-18 or platform operations.

---

## 5. Storage abstraction boundary

AstraIndexator SHALL NOT spread SeaweedFS-specific API calls throughout processing modules.

A narrow application port SHOULD expose operations conceptually equivalent to:

```text
head(ref)
open_read(ref, optional_range)
put_stream(target, stream, metadata)
delete(ref)
list_prefix(prefix)          operator/reconciliation only
```

The implementation MAY use a SeaweedFS-supported access interface selected by deployment profile. The domain layer MUST depend on `ObjectStorage`, not on SeaweedFS SDK/protocol classes.

Requirements:

- read/write operations MUST support streaming;
- timeouts and retries MUST be bounded;
- object references MUST remain serializable into PostgreSQL job/checkpoint records;
- transport credentials MUST come from runtime secret configuration;
- unsupported storage semantics MUST not be assumed implicitly.

---

## 6. Object classes

AstraIndexator distinguishes four storage classes.

### 6.1 SOURCE

Original immutable producer-uploaded file.

Examples:

```text
PDF
DOCX
PNG
TIFF
TXT
Markdown
```

SOURCE is authoritative input bytes for the accepted job.

### 6.2 PREPARED

Durable canonical artifacts generated after parsing/OCR/normalization/logical fragmentation.

Typical contents:

```text
manifest.json
elements/part-00000.jsonl
fragments/part-00000.jsonl
...
```

PREPARED artifacts are replay accelerators and diagnostic evidence. They are not the original business document.

### 6.3 DERIVED_BINARY

Optional extracted binary assets required for provenance/replay, for example selected embedded images or rendered pages.

These MUST be bounded by policy. AstraIndexator SHALL NOT automatically persist every parser intermediate image indefinitely.

### 6.4 STAGING

Temporary object-space used only while publishing a prepared artifact set.

STAGING is never considered a valid recovery checkpoint by itself.

---

## 7. Canonical source reference

The durable job stores a logical source reference similar to:

```json
{
  "storage": "SEAWEEDFS",
  "bucket": "documents",
  "objectKey": "original/<document-key>/v2/source.pdf",
  "versionId": null,
  "etag": null
}
```

Required semantics:

- `documentId` and `objectKey` are distinct identities;
- object location MUST NOT define business identity;
- raw upstream IDs MUST NOT be assumed path-safe;
- object key generation must use a documented path-safe representation;
- `bucket`/namespace names are deployment configuration, not business data;
- storage credentials are never persisted in the job.

TZ-01 remains authoritative for producer submission ordering.

---

## 8. Source immutability contract

Once a processable job is accepted, its source bytes SHALL be treated as immutable for that document version.

AstraIndexator SHALL record/verify the strongest available source identity signals:

```text
object reference
size
etag when meaningful
versionId when available
SHA-256 content hash after acquisition
```

Rules:

1. content hash is the cross-storage integrity identity;
2. ETag MUST NOT automatically be treated as a SHA-256 digest;
3. if storage supports native object version identity, it SHOULD be preserved;
4. source replacement under the same accepted version is prohibited;
5. changed source bytes require a new lifecycle/version decision under TZ-12.

If a retry fetches different bytes:

```text
SOURCE_CONTENT_MISMATCH
→ fail closed
```

as required by TZ-13.

---

## 9. Source publication ordering

Producer workflow remains:

```text
1. determine documentId/documentVersion
2. upload source to SeaweedFS
3. verify successful object publication
4. create PostgreSQL PENDING job referencing the object
5. commit job transaction
```

AstraIndexator SHALL NOT process a job whose source publication is incomplete.

There is no distributed XA transaction between PostgreSQL and SeaweedFS.

Therefore:

- successful upload + failed DB insert may leave an orphan source object;
- orphan cleanup is performed asynchronously through retention/reconciliation;
- deletion of an orphan MUST be age-gated to avoid racing a producer transaction.

---

## 10. Source acquisition model

AstraIndexator SHALL download source data using bounded-memory streaming.

Canonical flow:

```text
SeaweedFS object stream
        ↓
size / hash accounting
        ↓
attempt-scoped local workspace
        ↓
validation / parser
```

Requirements:

- do not `read()` an entire arbitrary source into memory;
- enforce configured maximum byte size during stream consumption, not only from metadata;
- calculate SHA-256 while streaming when practical;
- verify final byte count;
- abort on unexpected overrun;
- local file creation must use safe generated paths, not original file names;
- source acquisition retry must not create mixed partial files.

Detailed MIME/type validation belongs to TZ-04.

---

## 11. Local temporary workspace

Worker-local filesystem is ephemeral and non-authoritative.

Recommended shape:

```text
/work/astra-indexator/<jobId>/<processingAttemptId>/
    source.bin
    parse/
    ocr/
    output/
```

Rules:

1. workspace is attempt-scoped;
2. a new attempt SHALL NOT trust stale local files from a previous attempt;
3. paths are generated internally;
4. original file name is metadata only;
5. cleanup is idempotent;
6. disk quota/free-space checks SHOULD happen before expensive extraction/OCR;
7. local workspace is never a durable recovery checkpoint.

---

## 12. Prepared artifact identity

A prepared artifact set belongs to one immutable processing result.

Its identity MUST include enough information to prevent reuse across incompatible processing contracts.

Conceptually:

```text
PreparedArtifactIdentity =
    documentId
  + documentVersion
  + sourceContentHash
  + canonicalSchemaVersion
  + parserProfile/version
  + OCR profile/model version when applicable
  + normalizer version
  + splitter profile/version
```

The implementation MAY materialize this as a deterministic `processingFingerprint`.

A `processingFingerprint` is an artifact compatibility identity, not a business document ID.

---

## 13. Prepared object layout

Baseline namespace:

```text
prepared/
  <document-key>/
    v<document-version-key>/
      <processing-fingerprint>/
        manifest.json
        elements/
          part-00000.jsonl
          part-00001.jsonl
          ...
        fragments/
          part-00000.jsonl
          part-00001.jsonl
          ...
        derived/
          ... optional bounded assets ...
```

A separate staging namespace SHOULD be used while publishing:

```text
staging/
  <jobId>/
    <processingAttemptId>/
      <publicationId>/
        ...
```

Important rules:

- raw `documentId` or producer version MUST NOT be used unescaped if not path-safe;
- object layout is not a public API;
- consumers rely on manifest/object references, not on reconstructing keys from business IDs;
- changing object-layout version requires explicit migration/compatibility handling.

---

## 14. Why prepared data is sharded

AstraIndexator SHALL NOT use either extreme as the baseline:

```text
one SeaweedFS object per fragment
```

or:

```text
one unlimited fragments.jsonl for every document size
```

Instead, prepared collections are split into bounded immutable parts.

Example:

```text
fragments/part-00000.jsonl
fragments/part-00001.jsonl
fragments/part-00002.jsonl
```

Part boundaries SHALL be deterministic for the same canonical output and configuration.

Partitioning SHOULD consider configurable limits such as:

```text
max records per part
max uncompressed bytes per part
```

Exact production values belong to configuration/performance testing, not protocol semantics.

Benefits:

- bounded memory;
- bounded retry scope;
- parallelizable reads where useful;
- easier integrity verification;
- no millions of small objects;
- large-document scalability.

---

## 15. JSONL artifact rules

`elements/*.jsonl` and `fragments/*.jsonl` SHALL be stream-readable.

Rules:

1. UTF-8 encoding;
2. one canonical record per line;
3. line order is deterministic;
4. schema version is carried in manifest and, where useful, record envelope;
5. no unbounded nested binary/base64 payloads;
6. newline representation is deterministic for hashing;
7. record serialization used for artifact hash MUST be canonicalized by an explicitly tested implementation;
8. JSONL artifact hashing is independent from AstraVector session `batch_content_hash` unless TZ-11 explicitly makes them identical.

This last rule prevents accidental coupling of two different hash contracts.

---

## 16. Manifest contract

`manifest.json` is the authoritative descriptor of one prepared artifact set.

Conceptual example:

```json
{
  "schemaVersion": "astra-indexator-prepared-v1",
  "documentId": "DOC-100",
  "documentVersion": 3,
  "source": {
    "contentHash": "sha256:...",
    "sizeBytes": 1827341
  },
  "processingFingerprint": "sha256:...",
  "processing": {
    "parser": "pdf-parser@1.0.0/default",
    "ocr": "ocr-model@v1/multilingual-v1",
    "normalizer": "normalizer-v1",
    "splitter": "logical-v1/multilingual-general-v1"
  },
  "collections": {
    "elements": {
      "recordCount": 18420,
      "parts": [
        {
          "key": "elements/part-00000.jsonl",
          "recordCount": 5000,
          "sizeBytes": 4200011,
          "sha256": "..."
        }
      ]
    },
    "fragments": {
      "recordCount": 3120,
      "parts": []
    }
  },
  "createdAt": "..."
}
```

The actual JSON schema SHALL be versioned and tested.

Manifest MUST contain at least:

```text
artifact schema version
document identity
source content hash
processing compatibility fingerprint
part list in deterministic order
per-part record counts
per-part byte sizes
per-part SHA-256
collection total record counts
creation timestamp
```

---

## 17. Manifest-as-commit-marker protocol

Prepared publication SHALL be crash-safe without requiring multi-object transactions.

Baseline publication protocol:

```text
1. generate deterministic canonical output
2. write part objects under staging/publication namespace
3. close each upload successfully
4. verify size/hash of written parts
5. construct final manifest referencing exactly those parts
6. publish/copy immutable part objects to final namespace when needed
7. publish final `manifest.json` LAST
8. persist final manifest reference in PostgreSQL using lease/fencing
```

A prepared artifact set is considered **PUBLISHED** only when:

```text
manifest exists
AND manifest schema supported
AND all referenced required parts exist
AND required hashes/counts validate
```

Objects without a valid published manifest are incomplete/orphan candidates.

The design MUST NOT rely on directory rename being atomic.

---

## 18. Stale-worker publication fencing

SeaweedFS itself is not the lease authority. PostgreSQL/TZ-02 is.

A stale worker may have uploaded harmless immutable staging objects before discovering ownership loss.

It MUST NOT be able to make its artifact the authoritative job checkpoint.

Therefore the critical final step is PostgreSQL persistence:

```text
UPDATE indexation_job
SET prepared_manifest_ref = :ref,
    ...
WHERE job_id = :job
  AND worker_id = :worker
  AND lease_generation = :generation
```

If the fenced update affects zero rows:

- artifact MUST NOT be considered authoritative for that job;
- uploaded objects become orphan/reconciliation candidates;
- the stale worker MUST stop authoritative processing.

This provides fencing without pretending SeaweedFS participates in the PostgreSQL lease transaction.

---

## 19. Immutable artifact rule

Published prepared artifact parts SHALL be immutable.

A worker MUST NOT overwrite:

```text
part-00010.jsonl
```

under the identity of an already published processing fingerprint with different bytes.

If canonical output changes materially:

```text
new processingFingerprint
→ new prepared artifact set
```

This makes replay and diagnostics deterministic.

---

## 20. Prepared artifact recovery

Recovery follows TZ-13.

Before reuse, AstraIndexator SHALL validate:

```text
manifest schema supported
source content hash matches job/source
processingFingerprint compatible
all required parts present
part sizes/hash match manifest
documentId/version match
parser/OCR/normalizer/splitter contracts compatible
```

If valid:

```text
reuse artifact
→ stream fragments/elements
→ continue AstraVector ingestion
```

If invalid/corrupt:

```text
mark artifact unusable
→ regenerate from immutable source when policy permits
```

Artifact corruption MUST be distinguishable from source corruption.

---

## 21. Artifact read model

Consumers MUST read through the manifest.

They SHALL NOT discover a prepared document by listing all objects under a prefix and guessing which files belong to it.

Canonical read flow:

```text
prepared_manifest_ref from PostgreSQL
        ↓
read manifest
        ↓
validate manifest
        ↓
iterate ordered part descriptors
        ↓
stream/verify each part
        ↓
consume records
```

Prefix listing is reserved for operator/reconciliation/garbage-collection tasks.

---

## 22. Large-document bounded-memory invariant

For a very large document:

```text
source -> parser stream/workspace
      -> sharded elements
      -> logical processing
      -> sharded fragments
      -> streaming AstraVector batches
```

No stage may require loading every prepared fragment into memory solely because storage representation is monolithic.

The implementation SHOULD expose iterators/async iterators over artifact records.

---

## 23. Compression

Prepared text artifacts MAY use compression when implementation/performance testing proves value.

If compression is enabled:

- compression algorithm/version is recorded in manifest;
- hashes clearly specify whether they cover stored compressed bytes or canonical uncompressed bytes;
- readers validate consistently;
- random access assumptions must not rely on unsupported compressed seeking;
- compression must not prevent streaming.

AstraIndexator 1.0 does not require compression as a protocol invariant.

---

## 24. Derived images and rendered pages

Parser/OCR may create large temporary image data.

Default policy:

```text
local temporary rendering
→ OCR/extraction
→ discard after canonical artifact is safely published
```

Persist a derived binary only when it is required for:

- citation/preview contract;
- deterministic recovery that materially avoids expensive recomputation;
- approved audit/debug use;
- another explicitly documented downstream requirement.

Controls MUST include:

```text
max derived object count
max derived bytes per document
max image dimensions/pixels
allowed MIME types
retention period
```

AstraIndexator SHALL NOT persist every PDF page rendering by default.

---

## 25. Retention model

Retention is resource-specific.

### 25.1 SOURCE retention

Owned by platform/business policy. AstraIndexator MUST NOT assume source can be deleted merely because indexing completed.

### 25.2 PREPARED retention

Prepared artifacts SHOULD survive long enough to support:

- downstream retry;
- worker crash recovery;
- operational diagnostics;
- controlled replay.

Exact duration is deployment policy.

### 25.3 STAGING retention

Short-lived. Staging objects older than a configured safety threshold and not referenced by an active publication may be garbage-collected.

### 25.4 DERIVED_BINARY retention

Shortest practical policy unless explicitly required for product functionality.

### 25.5 Vector lifecycle independence

AstraVector deletion/TTL expiry does not automatically delete SeaweedFS source or prepared artifacts.

Likewise SeaweedFS cleanup MUST NOT directly mutate AstraVector/Qdrant state.

TZ-12 remains authoritative for document lifecycle coordination.

---

## 26. Orphan reconciliation

Because there is no cross-system transaction, orphan objects are expected operational possibilities.

Types include:

```text
source uploaded but job insert failed
staging artifact after worker crash
published artifact whose fenced DB checkpoint failed
old prepared artifact after reindex
expired derived binary
```

A reconciliation/GC process SHALL:

1. use age safety windows;
2. distinguish source/prepared/staging namespaces;
3. consult PostgreSQL references before destructive cleanup;
4. never delete an object referenced by an active/current job checkpoint;
5. record cleanup metrics/results;
6. support dry-run/audit mode before production deletion;
7. be idempotent.

GC is not allowed to infer business deletion merely from absence of a recent worker heartbeat.

---

## 27. Delete semantics

Deletion is intentionally decoupled.

```text
DeleteDocumentVectorsFacade
```

does not mean:

```text
delete SeaweedFS source
```

and SeaweedFS deletion does not mean:

```text
delete AstraVector vectors
```

Any operation that requires both must be implemented as an orchestrated lifecycle workflow with independent confirmations, not an XA-style assumption.

---

## 28. Consistency assumptions

AstraIndexator SHALL not depend on undocumented strong-consistency behavior for multi-object publication.

Correctness is obtained by:

```text
immutable objects
+
manifest publication marker
+
per-object hashes
+
PostgreSQL authoritative checkpoint reference
```

When an object write returns ambiguous transport failure, the client SHALL reconcile with `head`/read/hash where safe before choosing a new publication identity.

Repeated immutable uploads of identical bytes under a deterministic publication key MAY be treated idempotently when the storage adapter can verify equivalence.

---

## 29. Error taxonomy

Suggested storage-domain error codes:

```text
OBJECT_STORAGE_UNAVAILABLE
SOURCE_NOT_FOUND
SOURCE_ACCESS_DENIED
SOURCE_TOO_LARGE
SOURCE_CONTENT_MISMATCH
SOURCE_READ_INCOMPLETE
OBJECT_WRITE_FAILED
OBJECT_WRITE_AMBIGUOUS
PREPARED_MANIFEST_NOT_FOUND
PREPARED_MANIFEST_INVALID
PREPARED_SCHEMA_UNSUPPORTED
PREPARED_PART_MISSING
PREPARED_PART_HASH_MISMATCH
PREPARED_PART_SIZE_MISMATCH
PREPARED_ARTIFACT_INCOMPATIBLE
STAGING_PUBLICATION_INCOMPLETE
LOCAL_WORKSPACE_EXHAUSTED
```

Classification into transient/permanent/resource-limit follows TZ-13.

---

## 30. Security requirements

Object storage is a trust boundary.

AstraIndexator MUST:

1. obtain credentials from runtime secret management;
2. use least-privilege credentials;
3. avoid credentials in `objectKey`, metadata, URLs or logs;
4. validate object-key namespace/prefix against configured policy;
5. reject path traversal assumptions in local workspace mapping;
6. avoid following arbitrary external HTTP URLs as source objects;
7. encrypt transport according to deployment security profile;
8. treat producer-provided object metadata as untrusted;
9. prevent one tenant/security context from selecting an unauthorized storage prefix when multi-tenancy is introduced;
10. avoid exposing raw internal storage endpoints as durable public download links.

Detailed threat model belongs to TZ-16.

---

## 31. Observability requirements

Useful metrics include:

```text
object_read_total
object_read_bytes_total
object_read_duration_seconds
object_write_total
object_write_bytes_total
object_write_duration_seconds
object_storage_error_total
source_hash_mismatch_total
prepared_publish_total
prepared_publish_failure_total
prepared_reuse_total
prepared_regeneration_total
prepared_part_hash_failure_total
staging_orphan_detected_total
staging_orphan_deleted_total
```

Logs/events SHOULD include bounded correlation fields:

```text
jobId
documentId
documentVersion
processingAttemptId
storage operation
object class
object key hash/redacted reference
leaseGeneration
```

Full potentially sensitive object paths SHOULD NOT automatically become high-cardinality metric labels.

---

## 32. Configuration requirements

Configuration SHOULD include:

```text
storage endpoint/access mode
source namespace/bucket
prepared namespace/bucket
staging namespace/prefix
connection timeout
read timeout
write timeout
retry policy
max source bytes
local workspace root
local workspace quota guards
prepared max records per part
prepared max bytes per part
staging retention age
prepared retention policy
derived binary retention/limits
hash algorithm = SHA-256 baseline
```

Configuration values are deployment policy, not hard-coded business constants.

Startup validation SHALL fail fast on malformed mandatory storage configuration, but temporary dependency unavailability should be represented through readiness according to TZ-18 rather than necessarily terminating forever.

---

## 33. Performance and scalability requirements

The storage layer SHALL support N AstraIndexator replicas without shared local-disk coordination.

Requirements:

- object streaming with bounded buffers;
- connection pool limits;
- bounded concurrent downloads/uploads;
- backpressure when storage latency increases;
- sharded prepared artifacts for large documents;
- no prefix-wide listing on the normal processing hot path;
- no requirement to deserialize all artifact records before processing;
- configurable concurrency distinct from OCR concurrency;
- test coverage for large-object and many-part artifacts.

---

## 34. Multi-replica concurrency

Two workers must not intentionally publish competing authoritative artifacts for one job generation.

Correctness uses:

```text
PostgreSQL lease/fencing
+
immutable artifact publication
+
fenced prepared_manifest_ref commit
```

If two workers nevertheless upload equivalent or competing staging data due to lease transition, only the successfully fenced PostgreSQL checkpoint becomes authoritative.

No worker may delete another worker's recent staging prefix solely because it cannot see local ownership state; cleanup requires age/reconciliation safeguards.

---

## 35. Prepared artifact vs AstraVector session batches

Prepared SeaweedFS parts and AstraVector ingestion batches are related but not required to have identical boundaries.

```text
prepared fragment parts
        ↓ streaming mapper
AstraVector LogicalBlock batches
```

Reasons:

- SeaweedFS artifact size targets and AstraVector gRPC request limits differ;
- protobuf overhead differs from JSONL bytes;
- server runtime limits are configuration values;
- batch replay requires the exact TZ-11 hash contract.

Therefore storage partitioning SHALL NOT hard-code the AstraVector batch size.

---

## 36. Backup and disaster-recovery boundary

AstraIndexator itself does not redefine SeaweedFS cluster backup semantics.

Platform recovery policy SHOULD classify:

- source objects as business-critical according to owning platform policy;
- prepared artifacts as reproducible but operationally valuable;
- staging objects as disposable;
- local workspace as disposable.

If prepared artifacts are lost but immutable source remains available, AstraIndexator may rebuild them according to TZ-13.

If the only source copy is lost, AstraIndexator cannot reconstruct the original document from vectors and MUST fail rather than pretend recovery is possible.

---

## 37. Acceptance criteria

### AC-01 — Source reference separation

`documentId` remains unchanged when physical SeaweedFS object key/version changes according to a new source revision.

### AC-02 — Streaming acquisition

A source larger than worker memory budget can be acquired and hashed without loading the whole object into RAM.

### AC-03 — Source integrity

Changed source bytes for the same accepted job are detected and fail with `SOURCE_CONTENT_MISMATCH`.

### AC-04 — Attempt-local workspace

A restarted/reclaimed worker cannot accidentally reuse stale local files from another attempt.

### AC-05 — Sharded artifact

A large canonical document produces multiple bounded JSONL parts rather than one object per fragment or one unbounded collection object.

### AC-06 — Manifest integrity

Recovery validates manifest schema, part existence, size and SHA-256 before reuse.

### AC-07 — Crash before manifest

Killing a worker after uploading parts but before publishing the manifest does not expose the incomplete set as a valid prepared checkpoint.

### AC-08 — Crash after manifest before DB checkpoint

A published but unreferenced artifact does not become authoritative until the fenced PostgreSQL checkpoint succeeds.

### AC-09 — Stale-worker fencing

A stale lease generation cannot replace `prepared_manifest_ref` after a new worker has reclaimed the job.

### AC-10 — Prepared replay

A new replica can recover a compatible prepared artifact and continue AstraVector ingestion without repeating OCR/parser stages.

### AC-11 — Corruption regeneration

Corrupt/missing prepared parts cause artifact invalidation and deterministic regeneration from intact source when allowed.

### AC-12 — Bounded-memory replay

Prepared fragments can be streamed to TZ-11 batching without loading the full collection into RAM.

### AC-13 — Independent retention

Deleting AstraVector vectors does not automatically delete source/prepared SeaweedFS objects.

### AC-14 — Orphan cleanup safety

GC dry-run demonstrates that active/referenced objects are never selected while aged unreferenced staging objects are discoverable.

### AC-15 — No credentials in contract

Persisted job/object references and logs contain no SeaweedFS passwords/tokens.

### AC-16 — Storage outage classification

Temporary SeaweedFS unavailability produces retryable dependency failure; permanent source absence after reconciliation is classified distinctly.

### AC-17 — Multi-replica publication

Concurrent worker/reclaim test proves that only one prepared manifest becomes the authoritative PostgreSQL checkpoint.

### AC-18 — Large document scalability

A performance fixture with many thousands of elements/fragments proves bounded object count, bounded part size and streaming processing.

### AC-19 — Hash separation

Tests prove prepared-artifact SHA-256 semantics are not accidentally substituted for AstraVector `batch_content_hash/final_content_hash` without the TZ-11 canonical contract.

### AC-20 — Derived binary bounds

OCR/page-rendering fixture proves derived binary persistence is bounded and does not persist every temporary image by default.

---

## 38. Required verification evidence

Implementation work SHALL provide at least:

- SeaweedFS integration test environment;
- source upload/read/hash fixture;
- large streaming download test;
- changed-source/hash mismatch test;
- prepared sharding test;
- manifest schema test;
- missing/corrupt part tests;
- crash-before-manifest test;
- crash-after-manifest-before-DB-checkpoint test;
- stale-worker fenced checkpoint test;
- cross-replica prepared replay test;
- object-storage outage/retry test;
- orphan GC dry-run test;
- retention separation test;
- bounded local-disk test;
- derived-image persistence limit test;
- metrics/log redaction assertions;
- proof that downstream AstraVector batching remains independent from artifact-part boundaries.

---

## 39. Architectural invariants established by TZ-03

1. SeaweedFS stores source binaries and durable prepared artifacts; PostgreSQL remains the coordinator authority.
2. Source bytes for an accepted document version are immutable processing input.
3. SHA-256 is the baseline cross-storage content-integrity fingerprint.
4. Local workspace is ephemeral and attempt-scoped.
5. Prepared artifacts are immutable, versioned and manifest-described.
6. `manifest.json` is published last and acts as the artifact-set publication marker.
7. A prepared artifact is authoritative for a job only after a fenced PostgreSQL checkpoint references it.
8. Prepared elements/fragments use bounded sharded JSONL parts rather than one-object-per-fragment or unbounded monoliths.
9. Recovery reads through the manifest and verifies part integrity.
10. Storage partitioning is independent from AstraVector gRPC batch partitioning.
11. Source/prepared/staging/vector lifecycles are separate.
12. SeaweedFS GC/reconciliation is asynchronous, age-gated and reference-aware.
13. AstraIndexator never relies on SeaweedFS object naming as business identity.
14. Credentials and privileged URLs never become durable job data.
15. Multi-replica correctness is enforced through PostgreSQL lease/fencing, not filesystem ownership.

---

## 40. Next dependency

With TZ-03 closed, the next processing-plane specification is:

```text
TZ-04 — File Validation & Acquisition
```

TZ-04 shall define:

```text
source metadata preflight
streaming size enforcement
SHA-256 acquisition contract
extension vs declared MIME vs detected MIME
magic-byte/content detection
format admission matrix
archive/container defenses
resource limits
corrupt/truncated file handling
safe temporary-file creation
parser routing decision
```

TZ-04 must use the storage/read/integrity contract from this specification rather than introducing a second source-acquisition model.
