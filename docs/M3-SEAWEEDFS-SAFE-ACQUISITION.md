# M3 — SeaweedFS & Safe Acquisition

Status: **CLOSED — M3 baseline + M3.1 hardening implemented and verified**.

Normative specifications: TZ-03, TZ-04 and `TZ-04A-safe-acquisition-hardening.md`.

## Scope

M3 implements the trusted boundary from a claimed M2 job to a validated attempt-local `AcquiredSource`:

```text
M2 LeaseToken
  -> credential-free seaweed:// source reference
  -> ObjectStorage port
  -> SeaweedFS Filer HTTP adapter
  -> HEAD/preflight
  -> workspace capacity guard
  -> bounded streaming download
  -> byte count + SHA-256
  -> signature/container/image/text validation
  -> atomic source.part -> source.validated promotion
  -> fenced PostgreSQL acquisition checkpoint
  -> processing_stage = ACQUIRED
```

## Implemented invariants

- SeaweedFS-specific calls stay behind `ObjectStorage`.
- durable source references contain no credentials.
- source bytes are streamed; arbitrary files are never read wholly into memory for acquisition/validation.
- source size is enforced from HEAD metadata and actual streamed bytes.
- SHA-256 is calculated over acquired bytes; durable mismatch fails closed.
- producer file name is metadata only.
- partial downloads are never parser input.
- successful materialization is atomically promoted to `source.validated`.
- workspace root and limits are typed/configurable and startup-validated.
- free-space/reserve and per-attempt workspace budgets are enforced.
- attempt workspace belongs to the processing attempt, not M3 alone.
- crash leftovers are reclaimed only by a PostgreSQL-aware scavenger.
- a live non-expired lease prevents scavenger deletion regardless of directory age.
- recursive nested ZIP/OOXML processing is disabled by default (`max_nested_container_depth = 0`).
- OOXML traversal, duplicates, encryption, entry count, uncompressed size and compression ratio are bounded.
- connect/read storage timeouts and total acquisition deadline are explicit.
- acquisition/storage/workspace instrumentation uses low-cardinality ports; exporter wiring remains M10.
- acquisition evidence is authoritative only after a live fenced PostgreSQL checkpoint.

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

PDF validation remains admission-level. Semantic PDF parsing, scanned/native classification and OCR routing remain M4/M5.

## Durable acquisition evidence

Alembic revision `0002_acquisition_evidence` persists:

```text
source_etag
source_version_id
source_detected_format
source_detected_content_type
source_validation_profile
source_acquired_at
```

Existing fields remain authoritative for:

```text
source_content_hash
source_size_bytes
```

`local_path` is never a durable cross-worker checkpoint.

## Standardized failure classes

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
CONTAINER_NESTING_LIMIT
UNSUPPORTED_ZIP_CONTAINER
MALFORMED_CONTAINER
WORKSPACE_CAPACITY_EXCEEDED
WORKSPACE_IO_ERROR
STORAGE_CONNECT_TIMEOUT
STORAGE_READ_TIMEOUT
ACQUISITION_DEADLINE_EXCEEDED
```

## Verification evidence

M3.1 CI verifies:

- typed configuration rejects invalid limits;
- disk/workspace capacity rejection;
- nested-container rejection by default;
- total acquisition deadline classification;
- PostgreSQL-aware scavenging keeps old-but-live workspaces;
- expired-lease workspaces are reclaimable;
- previous M1/M2/M3 PostgreSQL, hostile-file and fencing tests remain green.

A full deployed SeaweedFS topology smoke/E2E remains part of TZ-17/M11.

## Explicitly deferred

M3 does not implement parser semantics, OCR, prepared artifact publication, malware scanning or AstraVector delivery. Those remain later milestones.

## Closure

M3 is considered CLOSED. The next implementation milestone may proceed to **M4 — Canonical Parser**.
