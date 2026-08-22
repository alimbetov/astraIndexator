# TZ-04 — File Validation & Acquisition

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-04
- **Title:** File Validation & Acquisition
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-02, TZ-03, TZ-05, TZ-06, TZ-09, TZ-13, TZ-14, TZ-16, TZ-17, TZ-18
- **Primary source storage:** SeaweedFS through the TZ-03 ObjectStorage abstraction

---

## 2. Purpose

This specification defines the trusted acquisition boundary between an accepted AstraIndexator job and format-specific parsing/OCR.

A producer-supplied file name, extension, declared MIME type, object metadata and object size are hints. They are not sufficient proof that the stored bytes are safe, complete, immutable or supported.

The canonical flow is:

```text
claimed IndexationJob
        ↓
source reference preflight
        ↓
SeaweedFS bounded streaming acquisition
        ↓
byte-count + SHA-256
        ↓
content-signature/type detection
        ↓
container/format safety validation
        ↓
policy admission
        ↓
AcquiredSource
        ↓
TZ-05 parser / TZ-06 OCR routing
```

The objective is to produce one validated, immutable, attempt-local source representation with enough evidence for deterministic routing, diagnostics and recovery.

---

## 3. Core invariants

The following are normative.

### FV-01 — Bytes are authoritative

Actual acquired bytes and validated content structure outrank file extension and declared MIME type.

### FV-02 — Validation precedes expensive parsing

Cheap bounded checks SHALL execute before expensive parser/OCR work whenever technically possible.

### FV-03 — Acquisition is bounded-memory

AstraIndexator SHALL NOT load arbitrary source files fully into heap merely to validate or hash them.

### FV-04 — Size is enforced twice

Object metadata size is preflight evidence only. The actual streamed byte count is authoritative for size-limit enforcement.

### FV-05 — Content hash is calculated from acquired bytes

SHA-256 of the actual source stream is the cross-storage integrity identity defined by TZ-03.

### FV-06 — Partial files are never parser input

Parser/OCR routing occurs only after acquisition has completed successfully and the local source has been atomically promoted to validated attempt-local state.

### FV-07 — Original file name is metadata only

It MUST NOT be used directly as a local filesystem path.

### FV-08 — Ambiguous type fails closed

When content cannot be confidently admitted to one supported format/profile, AstraIndexator SHALL reject it rather than route it by extension alone.

### FV-09 — Archive/container expansion is bounded

ZIP-based supported formats such as DOCX/XLSX/PPTX require container-safety validation. Generic archive ingestion remains outside the 1.0 baseline.

### FV-10 — Acquisition does not alter access scope or TTL

Access-zone and TTL intent remain document/job envelope data and are not inferred from file contents.

---

## 4. Responsibility boundary

### 4.1 TZ-04 owns

- source object preflight;
- bounded streaming download;
- actual byte-count enforcement;
- SHA-256 calculation;
- safe attempt-local materialization;
- declared MIME/extension/signature comparison;
- trusted format detection/admission;
- lightweight corruption/container checks;
- decompression-bomb/resource-limit defenses at admission boundary;
- acquisition diagnostics;
- production of `AcquiredSource`;
- parser/OCR route selection by canonical format.

### 4.2 TZ-03 owns

- source object reference and immutability semantics;
- SeaweedFS adapter;
- source/prepared retention;
- object-level recovery and prepared artifacts.

### 4.3 TZ-05 owns

- semantic/native document parsing;
- page/layout/text extraction;
- format-specific deep parser behavior.

### 4.4 TZ-06 owns

- OCR decision/execution after format acquisition;
- page/image OCR resource policy beyond generic admission checks.

### 4.5 TZ-16 owns

- malware scanning policy if required by deployment;
- trust/network/secret controls;
- sandbox/process isolation requirements.

TZ-04 SHALL expose hooks for malware/quarantine policy but SHALL NOT pretend MIME detection is malware detection.

---

## 5. Input contract

TZ-04 consumes a claimed job and TZ-03 source reference.

Conceptual input:

```json
{
  "jobId": "...",
  "processingAttemptId": "...",
  "documentId": "...",
  "documentVersion": 3,
  "source": {
    "storage": "SEAWEEDFS",
    "bucket": "documents",
    "objectKey": "original/.../v3/source.pdf",
    "versionId": null,
    "etag": null
  },
  "file": {
    "fileName": "contract.pdf",
    "declaredContentType": "application/pdf",
    "fileSize": 5230012
  }
}
```

Producer fields are not trusted validation results.

---

## 6. Output contract — AcquiredSource

TZ-04 SHALL produce a canonical acquisition result conceptually equivalent to:

```json
{
  "sourceRef": {"storage":"SEAWEEDFS","bucket":"documents","objectKey":"..."},
  "localPath": "/work/astra-indexator/<job>/<attempt>/source.validated",
  "originalFileName": "contract.pdf",
  "declaredContentType": "application/pdf",
  "declaredExtension": "pdf",
  "detectedFormat": "PDF",
  "detectedContentType": "application/pdf",
  "sizeBytes": 5230012,
  "sha256": "...",
  "etag": "...",
  "versionId": null,
  "validationProfile": "default-v1",
  "warnings": [],
  "acquiredAt": "..."
}
```

`localPath` is process-local and MUST NOT be persisted as a durable cross-worker checkpoint.

Durable evidence MAY persist detected format, actual size, SHA-256 and validation diagnostics in PostgreSQL attempt/job fields according to TZ-02.

---

## 7. Validation pipeline

Canonical sequence:

```text
1. validate source reference shape
2. HEAD/stat object
3. enforce metadata/preflight limits
4. create attempt-scoped temporary target
5. open bounded source stream
6. stream bytes to temporary file
7. count bytes + calculate SHA-256
8. enforce actual-stream maximum continuously
9. fsync/close according to runtime policy
10. validate final byte count
11. inspect bounded prefix/signatures
12. detect canonical format/MIME
13. compare extension + declared MIME + detected format
14. perform lightweight format/container safety checks
15. apply supported-format policy
16. optional security scanning hook
17. atomically promote temp file to source.validated
18. emit AcquiredSource
```

The implementation MAY combine compatible steps in one pass but MUST preserve these semantics.

---

## 8. Preflight / HEAD semantics

Before downloading, AstraIndexator SHOULD obtain object metadata through TZ-03 `head(ref)`.

Useful fields:

```text
exists
size
etag
versionId
lastModified
storage content-type when present
```

Preflight failures:

- missing object after bounded retry -> `SOURCE_NOT_FOUND`;
- metadata size above configured hard limit -> `SOURCE_TOO_LARGE` without downloading;
- version/etag conflict when the durable intent requires an exact value -> integrity failure;
- storage unavailable -> transient dependency failure.

HEAD metadata MUST NOT eliminate stream-time enforcement because metadata may be absent, stale, inaccurate or changed by a broken integration.

---

## 9. Streaming acquisition

Source acquisition SHALL use streaming I/O.

The worker writes to an internally generated temporary path, for example:

```text
/work/astra-indexator/<jobId>/<processingAttemptId>/incoming/source.part
```

While streaming, the worker SHALL:

- increment actual byte count;
- update SHA-256 digest;
- enforce maximum source bytes;
- enforce I/O deadline/idle timeout policy;
- fail on read/write error;
- never expose the partial target to parser/OCR code.

On success, the temporary file is closed and promoted inside the same workspace to:

```text
source.validated
```

On failure, partial data is deleted best-effort and the attempt records the failure class.

---

## 10. Size policy

Limits are deployment/configuration values, not permanent protocol constants.

The validation profile SHOULD define at least:

```text
max_source_bytes
max_container_entries
max_total_uncompressed_bytes
max_single_entry_uncompressed_bytes
max_compression_ratio
max_nested_container_depth
max_text_probe_bytes
max_signature_probe_bytes
```

Rules:

1. `max_source_bytes` is checked against HEAD size when available;
2. the same limit is enforced while reading actual bytes;
3. `Content-Length`/metadata cannot authorize an overrun;
4. zero-byte input is rejected unless an explicitly supported format/profile permits it;
5. limits MUST be observable and included in diagnostics without exposing secrets.

A retry with identical input and unchanged limits MUST NOT repeatedly process a deterministic `RESOURCE_LIMIT` failure.

---

## 11. Hash and source immutability validation

SHA-256 SHALL be calculated over exactly the acquired source bytes.

After download:

```text
actual_sha256 = SHA256(streamed bytes)
```

If a durable expected content hash exists:

```text
expected != actual
→ SOURCE_CONTENT_MISMATCH
→ permanent integrity failure
```

If no prior SHA-256 exists, the first successfully validated acquisition MAY establish the source content hash in the durable processing record through a fenced PostgreSQL update.

The hash MUST remain stable across retries of the same immutable document version.

ETag is retained as storage evidence but MUST NOT be substituted for SHA-256 unless a storage-specific contract explicitly proves equivalent semantics.

---

## 12. File-name and extension handling

Original file name is provenance/display metadata.

Rules:

- strip path components for display normalization;
- reject or sanitize control characters according to policy;
- never concatenate original name into a trusted filesystem path;
- extension is normalized case-insensitively for comparison;
- double extensions do not establish type;
- names such as `invoice.pdf.exe` are not routed as PDF merely because `.pdf` appears in the name;
- absence of extension does not reject a file whose bytes identify a supported format.

Examples:

```text
report.PDF       + PDF bytes -> PDF
report.txt       + PDF bytes -> mismatch policy, detected PDF authoritative
report.pdf       + executable bytes -> reject
no-extension     + PNG bytes -> PNG if admitted
```

---

## 13. Declared MIME versus detected MIME

Three signals remain distinct:

```text
file extension
declaredContentType / storage content-type
detected content format/MIME
```

Detected bytes/structure are authoritative for parser routing.

Mismatch SHALL produce an explicit diagnostic, for example:

```text
FILE_TYPE_MISMATCH
```

Mismatch policy is profile-driven:

- safe, unambiguous supported detected format MAY be admitted with warning;
- dangerous/unsupported/ambiguous detected format MUST be rejected;
- an executable masquerading as a document MUST be rejected;
- `application/octet-stream` MAY be accepted when trusted detection identifies a supported format.

AstraIndexator MUST NOT silently rewrite provenance as if the producer had declared the detected type correctly.

---

## 14. Canonical format taxonomy

TZ-04 SHALL route to canonical format identifiers rather than arbitrary MIME strings.

Baseline Tier 1:

```text
PDF
JPEG
PNG
TIFF
DOCX
TXT
MARKDOWN
```

Planned Tier 2:

```text
XLSX
PPTX
CSV
HTML
```

Extended/legacy formats require explicit profile support as defined by TZ-01.

`UNKNOWN` and `UNSUPPORTED` are validation outcomes, not parser plugins.

---

## 15. Detection strategy

Format detection SHOULD combine:

1. bounded magic/signature inspection;
2. container signature inspection;
3. minimal structural validation;
4. declared MIME/extension only as secondary evidence.

Examples:

```text
PDF   -> %PDF- signature plus structural parser preflight
JPEG  -> JPEG SOI/signature validation
PNG   -> PNG signature
TIFF  -> TIFF byte-order/magic validation
DOCX  -> ZIP container + required OOXML package structure/content types
TXT   -> text-decoding/binary heuristic after stronger binary formats excluded
MD    -> text plus producer/profile/extension hints; Markdown has no unique magic signature
```

Signature detection alone is insufficient for ZIP-based Office formats because DOCX/XLSX/PPTX share ZIP container magic.

---

## 16. ZIP/container safety

AstraIndexator 1.0 does not accept arbitrary ZIP/RAR/7Z archives as documents by default.

However OOXML formats are ZIP containers and therefore require bounded inspection.

Before deep extraction, the validator SHALL enforce configurable protections against:

- excessive entry count;
- excessive total advertised/actual uncompressed bytes;
- excessive single-entry size;
- pathological compression ratio;
- nested archive/container recursion;
- path traversal entries (`../`, absolute paths, drive prefixes);
- duplicate/conflicting entry names when unsafe for the selected parser;
- encrypted entries when unsupported;
- malformed central directory/container metadata.

Validation MUST NOT trust only advertised uncompressed sizes. Deep parser/extractor code SHALL continue enforcing resource limits while consuming entries.

This is decompression-bomb defense, not a complete malware scanner.

---

## 17. Path traversal and extraction rules

Any format-specific extraction to local workspace SHALL use safe generated destinations.

Archive entry names MUST NOT be written directly to arbitrary filesystem paths.

Required rule:

```text
normalize(candidatePath)
MUST remain under attempt workspace root
```

Symlinks, hard links, device files or other special-entry semantics SHALL be rejected unless an explicit parser sandbox contract safely supports them.

OOXML parsers SHOULD prefer stream/package APIs that do not require blind archive extraction.

---

## 18. Text-file validation

TXT and Markdown lack strong binary signatures.

Validation SHOULD include:

- BOM detection where present;
- configurable allowed encodings;
- UTF-8 preference;
- strict/diagnostic decoding rather than silent replacement of arbitrary bytes;
- NUL/binary-content heuristic;
- maximum line/record safeguards where relevant;
- preservation of original encoding in provenance when known.

If encoding cannot be determined safely under the active profile:

```text
TEXT_ENCODING_UNSUPPORTED
```

shall be returned rather than silently corrupting content.

---

## 19. PDF admission preflight

TZ-04 performs only lightweight PDF validation sufficient to decide whether TZ-05/TZ-06 may attempt processing.

Checks SHOULD distinguish:

```text
not a PDF
malformed/corrupt PDF
password/encryption protected PDF
PDF accepted for deeper parsing
```

Encrypted/password-protected documents SHALL be rejected in baseline 1.0 unless a separate secure password/key contract is introduced.

TZ-04 SHALL NOT attempt to infer that a PDF is scanned versus native-text; that decision belongs to parser/OCR processing.

---

## 20. Image admission preflight

For JPEG/PNG/TIFF, the validator SHOULD obtain bounded header metadata before OCR, including when available:

```text
width
height
page/frame count
bit depth/color characteristics
```

It SHALL enforce configured limits such as:

```text
max_image_width
max_image_height
max_image_pixels
max_tiff_pages
max_total_image_pixels
```

This prevents small compressed inputs from triggering unbounded decoded-memory/OCR workloads.

Actual decoder/OCR stages SHALL continue to enforce their own limits.

---

## 21. Malware/security scanning hook

File-type validation is not antivirus scanning.

A deployment MAY require a security scanning step:

```text
VALIDATED_TYPE
  -> SECURITY_SCAN
  -> CLEAN / REJECTED / SCAN_UNAVAILABLE
```

If enabled, policy SHALL define fail-open versus fail-closed behavior. Production handling of untrusted external uploads SHOULD default to fail-closed when the scanner is mandatory.

Scanner implementation, signatures and quarantine lifecycle belong to TZ-16/TZ-18.

AstraIndexator SHALL NOT execute macros, scripts, embedded executables or active document content as part of validation.

---

## 22. Parser routing contract

Only a successfully produced `AcquiredSource` may be routed.

Conceptual registry:

```text
PDF      -> PdfHandler
JPEG     -> ImageHandler/OCR path
PNG      -> ImageHandler/OCR path
TIFF     -> ImageHandler/OCR path
DOCX     -> DocxHandler
TXT      -> TextHandler
MARKDOWN -> MarkdownHandler
```

Routing is based on `detectedFormat`, not solely on file extension.

Unsupported detected formats fail before parser invocation.

---

## 23. Idempotency and retry

Acquisition may repeat because AstraIndexator uses at-least-once processing.

Repeated acquisition of the same immutable source SHOULD produce:

```text
same sizeBytes
same SHA-256
same detectedFormat
same admission result
```

under the same validation profile/version.

A new processing attempt creates a new local workspace and does not trust a previous attempt's partial file.

Transient SeaweedFS/network failures are retried according to TZ-13.

Deterministic unsupported/corrupt/resource-limit failures are not blindly retried with unchanged input/configuration.

---

## 24. Lease/fencing behavior

Long downloads SHALL respect TZ-02 lease ownership.

A worker MAY perform local acquisition while it owns the job, but before persisting authoritative acquisition evidence or proceeding to downstream mutation it SHALL still own the current lease generation.

If ownership is lost:

```text
stop authoritative processing
close/cancel stream when practical
cleanup local workspace
DO NOT update job checkpoint as current owner
```

A stale worker's local validated file has no authority for another worker.

---

## 25. Failure taxonomy

Canonical TZ-04 error codes SHOULD include:

```text
SOURCE_NOT_FOUND
SOURCE_STORAGE_UNAVAILABLE
SOURCE_READ_TIMEOUT
SOURCE_READ_FAILED
SOURCE_TOO_LARGE
SOURCE_EMPTY
SOURCE_SIZE_MISMATCH
SOURCE_CONTENT_MISMATCH
SOURCE_VERSION_MISMATCH
FILE_TYPE_UNKNOWN
FILE_TYPE_UNSUPPORTED
FILE_TYPE_MISMATCH
FILE_CORRUPT
FILE_ENCRYPTED_UNSUPPORTED
TEXT_ENCODING_UNSUPPORTED
CONTAINER_MALFORMED
CONTAINER_TOO_MANY_ENTRIES
CONTAINER_ENTRY_TOO_LARGE
CONTAINER_EXPANSION_TOO_LARGE
CONTAINER_COMPRESSION_RATIO_EXCEEDED
CONTAINER_PATH_TRAVERSAL
CONTAINER_ENCRYPTED_UNSUPPORTED
IMAGE_DIMENSIONS_EXCEEDED
IMAGE_PIXEL_LIMIT_EXCEEDED
LOCAL_DISK_INSUFFICIENT
LOCAL_WRITE_FAILED
SECURITY_SCAN_REJECTED
SECURITY_SCAN_UNAVAILABLE
OWNERSHIP_LOST
```

Classification into transient/permanent/resource/policy categories follows TZ-13.

---

## 26. Local disk capacity

Before downloading or expanding expensive formats, the worker SHOULD verify sufficient workspace capacity against configured safety margins.

Disk checks are advisory plus enforced write limits; free-space checks alone are not guarantees because replicas/processes may consume disk concurrently.

AstraIndexator SHOULD support:

```text
workspace_max_bytes_per_attempt
workspace_min_free_bytes
workspace_min_free_percent
```

A disk-full condition MUST be surfaced as a resource/infrastructure failure, not as `FILE_CORRUPT`.

---

## 27. Observability

TZ-04 SHALL expose structured evidence sufficient to answer:

```text
which source was acquired?
how many bytes were read?
how long did acquisition take?
what SHA-256 was established?
what type was declared?
what type was detected?
was there a mismatch?
which validation profile/version was used?
which limit rejected the file?
was container/image preflight applied?
```

Metrics SHOULD include:

```text
acquisition_started_total
acquisition_completed_total
acquisition_failed_total{reason}
acquisition_bytes_total
acquisition_duration_seconds
file_type_detected_total{format}
file_type_mismatch_total
validation_rejected_total{reason}
container_rejected_total{reason}
workspace_write_failed_total
```

Logs MUST NOT dump arbitrary document contents, archive entries containing sensitive text, access credentials or signed storage URLs.

---

## 28. Security rules

1. Source object reference is trusted only after normal contract validation.
2. Arbitrary external HTTP(S) URL acquisition is not baseline behavior.
3. Original file names never control local paths.
4. Archive paths never escape the workspace.
5. Active document content is never executed.
6. Parser invocation occurs only for admitted canonical formats.
7. Validation limits are server-side configuration and cannot be increased by untrusted document metadata.
8. Access-zone assignment is not derived from file contents.
9. Storage credentials remain runtime secrets.
10. Temporary files SHOULD use restrictive permissions and be deleted best-effort after processing.

---

## 29. Configuration baseline

Exact values are deployment-specific, but configuration SHALL be explicit and startup-validated.

Conceptual configuration:

```yaml
acquisition:
  validation-profile: default-v1
  max-source-bytes: ...
  signature-probe-bytes: ...
  read-timeout: ...
  workspace:
    root: /work/astra-indexator
    max-bytes-per-attempt: ...
    min-free-bytes: ...
  container:
    max-entries: ...
    max-total-uncompressed-bytes: ...
    max-single-entry-bytes: ...
    max-compression-ratio: ...
    max-nesting-depth: 0
  image:
    max-width: ...
    max-height: ...
    max-pixels: ...
    max-tiff-pages: ...
```

AstraIndexator SHALL fail startup or readiness when required safety limits are absent/invalid rather than silently operating unbounded.

---

## 30. Recovery semantics

Crash during acquisition:

```text
partial local source
→ attempt lost
→ lease expires/reclaim
→ new worker creates new workspace
→ reacquires immutable source
```

Crash after acquisition but before durable later checkpoint:

- reacquisition is acceptable and deterministic;
- local source is not assumed recoverable across pods;
- if a compatible TZ-03 prepared artifact already exists, TZ-13 may skip source reacquisition when downstream recovery does not require the source.

If a later stage discovers that source bytes differ from the durable hash, processing fails closed.

---

## 31. Interaction with prepared artifacts

TZ-04 validates SOURCE bytes. It does not validate TZ-03 prepared artifact JSONL schemas/hashes; prepared-artifact recovery has its own manifest contract.

The source SHA-256 established here becomes part of:

```text
processingFingerprint
prepared manifest source identity
recovery compatibility checks
AstraVector source/content provenance where mapped
```

Thus source validation is the root integrity evidence for deterministic reprocessing.

---

## 32. Test and verification requirements

TZ-17 SHALL include executable evidence for at least:

1. valid PDF with correct extension/MIME;
2. valid PDF with misleading extension;
3. executable/binary masquerading as PDF;
4. missing object;
5. HEAD size above limit;
6. stream exceeds declared/metadata size and hard limit;
7. source truncated during read;
8. same source retry reproduces SHA-256;
9. changed source under same job produces `SOURCE_CONTENT_MISMATCH`;
10. DOCX ZIP bomb simulation rejected before deep parsing;
11. path traversal ZIP entry rejected;
12. excessive container entry count rejected;
13. encrypted OOXML/container rejected when unsupported;
14. PNG/JPEG image pixel bomb rejected by header limits;
15. multi-page TIFF page/pixel limit enforcement;
16. invalid UTF-8/text encoding policy;
17. `application/octet-stream` + valid supported bytes admitted according to profile;
18. unknown format rejected;
19. local disk exhaustion classified correctly;
20. crash mid-download leaves no parser-visible valid source;
21. stale worker cannot persist acquisition checkpoint after lease loss;
22. no document bytes/secrets leaked to normal logs.

---

## 33. Acceptance criteria

TZ-04 is satisfied when:

- **AC-01:** acquisition is bounded-memory and streaming;
- **AC-02:** metadata and actual-stream size limits are both enforced;
- **AC-03:** SHA-256 is calculated from actual bytes;
- **AC-04:** immutable-source mismatch fails closed;
- **AC-05:** parser routing uses detected canonical format;
- **AC-06:** extension/MIME mismatches are explicit and policy-controlled;
- **AC-07:** unsupported/ambiguous content never reaches a parser by extension fallback;
- **AC-08:** partial downloads never become parser input;
- **AC-09:** local paths are internally generated and traversal-safe;
- **AC-10:** OOXML/container admission has decompression/resource defenses;
- **AC-11:** generic archive ingestion remains disabled by default;
- **AC-12:** image header limits prevent obvious decoded-memory bombs;
- **AC-13:** encrypted/password-protected unsupported files fail explicitly;
- **AC-14:** retry/recovery uses new attempt-local workspace;
- **AC-15:** deterministic failures are classified separately from transient storage failures;
- **AC-16:** stale workers cannot commit acquisition evidence after fencing loss;
- **AC-17:** validation configuration is bounded and startup-validated;
- **AC-18:** observability exposes type/size/hash/limit outcomes without document-content leakage;
- **AC-19:** source SHA-256 propagates into prepared/recovery identity;
- **AC-20:** TZ-17 contains adversarial and crash/retry evidence for this boundary.

---

## 34. Implementation decomposition

Recommended modules/interfaces:

```text
SourceAcquisitionService
SourcePreflightValidator
StreamingSourceDownloader
SourceIntegrityHasher
FileTypeDetector
FileAdmissionPolicy
ContainerSafetyValidator
ImageHeaderValidator
TextEncodingDetector
WorkspaceManager
AcquiredSource
ValidationResult / ValidationFailure
```

Format-specific deep parsing remains behind TZ-05 handlers.

This decomposition allows tests to inject hostile streams/containers without requiring a live SeaweedFS instance for every validation case.

---

## 35. Final invariant

The parser boundary is not:

```text
fileName -> parser
```

It is:

```text
immutable source reference
  -> bounded streaming acquisition
  -> actual size + SHA-256
  -> trusted format/container validation
  -> safe attempt-local source
  -> AcquiredSource
  -> format-specific parser/OCR
```

No producer-controlled extension, MIME string, file name or storage metadata may bypass this boundary.
