# M7 — Prepared Artifacts & Replay

## Status

**IMPLEMENTED on `feat/m7-prepared-artifacts-replay`; CI evidence required before merge.**

M7 establishes the durable recovery boundary between expensive document preparation (M4/M5/M6) and downstream delivery (M8).

## Frozen invariants

1. A prepared artifact is immutable and content-addressed.
2. Identity binds `documentId + documentVersion + sourceSha256`.
3. Compatibility binds parser, normalization, splitter and OCR model revisions.
4. Artifact parts are canonical UTF-8 JSONL with SHA-256, byte count and record count.
5. Parts are published before `manifest.json`.
6. `manifest.json` is the object-store commit marker. No manifest means no replayable artifact.
7. Every object publication and final manifest publication is fenced by the current M2 `LeaseToken` supplied by the worker transaction boundary.
8. PostgreSQL `prepared_artifact_checkpoint` is the authoritative replay pointer. Object-store listing is never authority.
9. Checkpoint installation locks the job row and re-validates worker, generation, PROCESSING state and PostgreSQL-time lease expiry.
10. A stale worker may leave unreachable immutable parts after a crash/lease loss; it cannot install an authoritative checkpoint.
11. Existing immutable objects are never overwritten. Identical bytes make publication idempotent; different bytes are a hard conflict.
12. Replay verifies every part before exposing records.
13. Any compatibility fingerprint change means `REPROCESS`, not best-effort replay.
14. M7 canonical JSON is an artifact-storage format only. It MUST NOT be reused to invent M8 `batch_content_hash` / `final_content_hash` serialization.

## Layout

```text
prepared/v1/
  {documentId}/
    {documentVersion}/
      {sourceSha256}/
        {artifactId}/
          parts/
            elements-00000.jsonl
            elements-00001.jsonl
            fragments-00000.jsonl
          manifest.json              # published last
```

`artifactId` is SHA-256 over canonical identity, compatibility fingerprint and ordered part hashes. Different prepared outputs therefore have different artifact roots.

## Compatibility contract

The v1 fingerprint includes:

```text
schemaVersion
parserName
parserVersion
parserProfile
normalizerVersion
splitterProfile
splitterVersion
ocrModelId
ocrArtifactRevision
ocrManifestSha256
```

OCR fields may be null only when OCR did not participate in the prepared pipeline contract. An OCR model revision change rejects replay even if source bytes are unchanged.

## Publication protocol

```text
build deterministic JSONL parts
→ calculate part SHA-256/count/bytes
→ calculate compatibilitySha256
→ calculate artifactId
→ assert current M2 lease
→ put immutable part 0
→ ... repeat fence + put for every part
→ construct manifest
→ assert current M2 lease
→ put immutable manifest.json LAST
→ begin PostgreSQL transaction
→ lock indexation_job
→ revalidate M2 fencing predicate using PostgreSQL now()
→ upsert prepared_artifact_checkpoint
→ append PREPARED_ARTIFACT_COMMITTED job_event
→ commit
```

A crash before `manifest.json` leaves only unreachable parts. A crash after manifest but before PostgreSQL commit leaves a non-authoritative object-store root. Retry can deterministically republish identical bytes and install the checkpoint. M9 may garbage-collect unreachable roots only with an age safety window.

## Replay protocol

```text
read prepared_artifact_checkpoint
→ load committed manifest
→ compare expected compatibilitySha256
→ mismatch: REPROCESS
→ match: fetch every part
→ verify byteCount
→ verify SHA-256
→ parse JSONL
→ verify recordCount
→ verify manifest totals
→ expose PreparedArtifact
```

Integrity failure is artifact corruption, not a parser/OCR retry condition, and must be surfaced distinctly.

## SeaweedFS semantics

`SeaweedPreparedArtifactStore` uses a dedicated bucket/prefix and immutable content-addressed keys. SeaweedFS Filer does not provide a portable atomic create-if-absent primitive used by this implementation; correctness therefore does not depend on overwrite races. Existing keys are read back and must be byte-identical. Since `artifactId` includes ordered part hashes, different content maps to a different root.

## PostgreSQL schema

Migration `0003_prepared_artifact_checkpoint` adds one authoritative checkpoint per job with:

```text
job_id PK/FK
artifact_id
manifest_uri
source_sha256
compatibility_sha256
element_count
fragment_count
lease_generation
created_at
updated_at
```

The checkpoint deliberately stores the immutable pointer/fingerprint rather than duplicating artifact bodies in PostgreSQL.

## Evidence matrix

Automated tests prove:

- deterministic partition names and bounded records per part;
- manifest is written last;
- lease is checked before every external mutation;
- stale lease cannot publish the commit marker;
- identical retry is idempotent;
- exact compatibility permits replay;
- pipeline revision mismatch forces reprocessing;
- corrupted part is rejected before replay;
- PostgreSQL migration creates the checkpoint;
- valid current lease installs the authoritative pointer;
- expired lease cannot install the pointer.

## M8 hand-off

M8 consumes only a verified replay or a freshly prepared in-memory equivalent. M8 remains the sole owner of AstraVector protobuf serialization and downstream content hashes. This boundary prevents M7 storage serialization from becoming an accidental wire contract.
