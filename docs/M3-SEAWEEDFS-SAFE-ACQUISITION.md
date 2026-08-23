# M3 — SeaweedFS & Safe Acquisition

Status: **implemented baseline; M3.1 hardening required before M4**.

Normative specifications: TZ-03, TZ-04 and `TZ-04A-safe-acquisition-hardening.md`.

## Scope

M3 implements the TZ-03/TZ-04 boundary from a claimed M2 job to a validated attempt-local `AcquiredSource`.

```text
M2 LeaseToken
  -> credential-free seaweed:// source reference
  -> ObjectStorage port
  -> SeaweedFS Filer HTTP adapter
  -> HEAD/preflight
  -> bounded streaming download
  -> byte count + SHA-256
  -> signature/container/image/text validation
  -> atomic source.part -> source.validated promotion
  -> fenced PostgreSQL acquisition checkpoint
  -> processing_stage = ACQUIRED
```

## Implemented baseline

- SeaweedFS-specific HTTP calls stay behind `ObjectStorage`.
- durable source references contain no credentials.
- producer file name is metadata only and is never used as a trusted local path.
- source bytes are streamed to an attempt-scoped workspace.
- `max_source_bytes` is enforced both from HEAD metadata and during the stream.
- SHA-256 is calculated from acquired bytes.
- durable hash mismatch fails as `SOURCE_CONTENT_MISMATCH`.
- partial files are never promoted to parser input.
- successful materialization is atomically renamed to `source.validated`.
- validation is bounded-memory; TXT/Markdown UTF-8 validation is incremental.
- detected bytes/structure outrank extension for routing.
- executable masquerading as a document is rejected.
- OOXML/DOCX inspection rejects traversal, duplicate/encrypted entries and current resource-limit violations.
- PNG/JPEG/TIFF validation uses bounded header/decoder metadata with pixel/page limits.
- acquisition evidence can only be installed with a live `(job_id, worker_id, lease_generation, lease_until)` fence.

## M3.1 hardening gate

The post-implementation review identified production requirements that MUST be completed before M4:

```text
1. explicit configurable workspace root/capacity policy
2. preflight free-space/reserve/attempt-budget enforcement
3. attempt-owned normal cleanup
4. PostgreSQL-aware orphan workspace scavenger
5. explicit max_nested_container_depth policy
6. recursive nested-container processing disabled by default
7. typed acquisition/storage/workspace configuration
8. connect/read/total acquisition timeouts
9. low-cardinality acquisition/storage/workspace instrumentation hooks
10. regression tests for all above behavior
```

These requirements are normative in TZ-04A.

## Workspace ownership

The local workspace belongs to the **processing attempt**, not to M3 alone. `source.validated` remains available to M4/M5 until the attempt terminates or loses authoritative ownership.

Normal cleanup occurs at orchestration boundary. Crash leftovers are reclaimed only by a state-aware scavenger that verifies PostgreSQL attempt/job/lease state. Age-only deletion is prohibited.

Production MUST use an explicit workspace mount/configuration; an implicit `/tmp` capacity plan is not accepted.

## Container hardening

The existing DOCX validator already bounds entry count, advertised uncompressed size, single-entry size and compression ratio and rejects unsafe paths/encryption/duplicates. M3.1 additionally makes nesting policy explicit.

AstraIndexator 1.0 does not recursively expand arbitrary embedded ZIP/OOXML containers during M3 admission. Future nested processing must be depth- and cumulative-budget-bounded.

## Observability boundary

M3.1 introduces instrumentation interfaces/hooks and records acquisition/storage/workspace measurements. Prometheus/OTel HTTP/exporter wiring remains owned by TZ-14/M10.

Metric labels must remain low-cardinality; document/job/file/object identifiers belong in logs/traces, not metric labels.

## Tier-1 formats

```text
PDF
JPEG
PNG
TIFF
DOCX
TXT
MARKDOWN
```

PDF validation in M3 is intentionally admission-level, not semantic parsing. Deep PDF structure, native/scanned classification and reading order belong to M4/M5.

## Durable acquisition evidence

Alembic revision `0002_acquisition_evidence` adds:

```text
source_etag
source_version_id
source_detected_format
source_detected_content_type
source_validation_profile
source_acquired_at
```

Existing M1 fields remain authoritative for:

```text
source_content_hash
source_size_bytes
```

`local_path` is never persisted as a durable cross-worker checkpoint.

## Failure classes implemented in baseline

```text
SOURCE_NOT_FOUND
SOURCE_TOO_LARGE
EMPTY_SOURCE
SOURCE_SIZE_MISMATCH
SOURCE_CONTENT_MISMATCH
UNSUPPORTED_EXECUTABLE
MALFORMED_PDF
MALFORMED_IMAGE
IMAGE_RESOURCE_LIMIT
UNSUPPORTED_FORMAT
TEXT_ENCODING_UNSUPPORTED
CONTAINER_PATH_TRAVERSAL
CONTAINER_DUPLICATE_ENTRY
CONTAINER_ENCRYPTED
CONTAINER_RESOURCE_LIMIT
UNSUPPORTED_ZIP_CONTAINER
MALFORMED_CONTAINER
```

M3.1 additionally standardizes:

```text
WORKSPACE_CAPACITY_EXCEEDED
WORKSPACE_IO_ERROR
CONTAINER_NESTING_LIMIT
STORAGE_CONNECT_TIMEOUT
STORAGE_READ_TIMEOUT
ACQUISITION_DEADLINE_EXCEEDED
```

## Explicitly deferred

M3/M3.1 does not implement parser semantics, OCR, prepared artifact publication, malware scanning or AstraVector delivery. Those remain M4+ concerns.

A full deployed SeaweedFS topology smoke/E2E remains part of TZ-17/M11; M3 CI may continue proving the Filer adapter HTTP contract without requiring an external storage cluster.

## Closure criterion

M3 is not considered fully CLOSED until M3.1 tests are green for workspace capacity, state-aware crash cleanup, nested-container policy, timeout classification, instrumentation contract and existing PostgreSQL fencing/hostile-file behavior. Only then should M4 Canonical Parser begin.
