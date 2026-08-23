# M3 — SeaweedFS & Safe Acquisition

Status: implementation milestone.

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

## Implemented invariants

- SeaweedFS-specific HTTP calls stay behind `ObjectStorage`.
- durable source references contain no credentials.
- producer file name is metadata only and is never used as a trusted local path.
- source bytes are streamed to an attempt-scoped workspace.
- `max_source_bytes` is enforced both from HEAD metadata and during the stream.
- SHA-256 is calculated from the acquired bytes.
- an existing durable hash mismatch fails as `SOURCE_CONTENT_MISMATCH`.
- partial files are never promoted to parser input.
- successful materialization is atomically renamed to `source.validated`.
- validation is bounded-memory; TXT/Markdown UTF-8 validation is incremental.
- detected bytes/structure outrank extension for routing.
- executable masquerading as a document is rejected.
- OOXML/DOCX inspection rejects traversal, duplicate/encrypted entries and resource-limit violations.
- PNG/JPEG/TIFF validation uses bounded header/decoder metadata with pixel/page limits.
- acquisition evidence can only be installed with a live `(job_id, worker_id, lease_generation, lease_until)` fence.

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

## Failure classes covered now

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

## Explicitly deferred

M3 does not implement parser semantics, OCR, prepared artifact publication, malware scanning or AstraVector delivery. Those remain M4+ concerns.

A full deployed SeaweedFS topology smoke/E2E remains part of the wider TZ-17/M11 verification layer; M3 CI proves the Filer adapter HTTP contract without requiring an external storage service.
