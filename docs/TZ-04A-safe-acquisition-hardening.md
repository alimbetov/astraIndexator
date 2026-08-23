# TZ-04A — Safe Acquisition Hardening

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-04A
- **Title:** Safe Acquisition Hardening
- **Status:** Normative hardening baseline
- **Parent:** `TZ-04-file-validation-acquisition.md`
- **Related:** TZ-02, TZ-03, TZ-04, TZ-13, TZ-14, TZ-15, TZ-17, TZ-18
- **Implementation milestone:** M3.1

This document closes production-hardening gaps discovered during the post-implementation review of M3. Where this document is more specific than TZ-03/TZ-04 for local acquisition workspace, nested containers, acquisition timeouts, cleanup or instrumentation, **TZ-04A takes precedence** for AstraIndexator 1.0.

---

## 2. Scope

M3.1 hardens the already implemented acquisition chain:

```text
M2 LeaseToken
  -> ObjectStorage / SeaweedFS
  -> bounded streaming acquisition
  -> SHA-256 + byte count
  -> format/container validation
  -> attempt-local source.validated
  -> fenced PostgreSQL acquisition checkpoint
```

The hardening adds:

1. explicit workspace capacity policy;
2. crash-safe orphan workspace scavenging;
3. explicit nested-container depth policy;
4. acquisition/storage/workspace observability hooks;
5. typed resource-limit and timeout configuration.

It does **not** move parser, OCR, prepared artifacts or AstraVector delivery into M3.

---

## 3. Hardening invariants

### FH-01 — Workspace root is explicit

AstraIndexator MUST receive an explicit `workspace_root` from validated runtime configuration. Production deployment MUST NOT depend on an implicit system temporary directory.

Recommended production path:

```text
/work/astra-indexator
```

The path is deployment configuration, not a protocol constant.

### FH-02 — Workspace is ephemeral, bounded and non-authoritative

Attempt-local files are execution material only. PostgreSQL and published SeaweedFS artifacts remain durable authorities.

A worker MUST NOT assume that a local workspace survives pod/node/process restart.

### FH-03 — Capacity is checked before acquisition

Before writing source bytes, the worker MUST verify workspace capacity against the active policy. A job MUST fail/retry safely before expensive work when configured reserve cannot be maintained.

### FH-04 — Source-size limit is not a disk-capacity guarantee

`max_source_bytes` limits the source stream only. Parser/OCR expansion may exceed source size. Workspace policy therefore remains independent from source admission limits.

### FH-05 — Cleanup is attempt-owned

M3 MUST NOT delete `source.validated` immediately after acquisition because M4/M5 consume it. The orchestration layer owns cleanup of the whole attempt workspace after terminal attempt completion/abort.

### FH-06 — Crash cleanup requires durable-state reconciliation

Age alone MUST NOT authorize deletion of an attempt workspace. A scavenger MUST reconcile the directory with PostgreSQL attempt/job/lease state before deletion.

### FH-07 — Recursive container expansion is disabled by default

AstraIndexator 1.0 does not recursively extract arbitrary nested ZIP/OOXML containers during M3 admission. Nested-container processing requires an explicit bounded parser contract.

### FH-08 — Timeouts are explicit and bounded

Storage connect/read timeouts and total acquisition deadline MUST be configured and validated. A stalled stream MUST not retain a worker indefinitely.

### FH-09 — Instrumentation is low-cardinality

Acquisition instrumentation MUST NOT use `documentId`, `jobId`, file name, object key or producer request ID as metric labels.

### FH-10 — Configuration is typed and startup-validated

Invalid limits/timeouts/workspace settings MUST fail application startup rather than silently fall back to unsafe values.

---

## 4. Workspace policy

Conceptual configuration:

```text
workspace.root
workspace.min_free_bytes
workspace.reserve_bytes
workspace.max_attempt_bytes
workspace.orphan_grace_period
workspace.scavenge_interval
```

Semantics:

- `root` — attempt workspace root;
- `min_free_bytes` — minimum filesystem free space required before starting acquisition;
- `reserve_bytes` — capacity that AstraIndexator must leave unused for node/runtime safety;
- `max_attempt_bytes` — hard local-byte budget for one processing attempt across acquisition/parser/OCR intermediates;
- `orphan_grace_period` — minimum age before a directory may be considered by scavenger;
- `scavenge_interval` — bounded periodic cleanup cadence.

Before acquisition:

```text
usable = filesystem_free_bytes(workspace.root) - workspace.reserve_bytes

if filesystem_free_bytes < workspace.min_free_bytes
   OR usable < minimum_required_for_attempt
then
   WORKSPACE_CAPACITY_EXCEEDED
```

`minimum_required_for_attempt` MAY initially use a conservative profile-derived estimate. M4/M5 MAY refine reservation accounting for parser/OCR expansion.

The implementation MUST prevent path escape from `workspace.root`.

---

## 5. Attempt workspace lifecycle

Canonical layout remains:

```text
<workspace.root>/<jobId>/<processingAttemptId>/
  incoming/source.part
  source.validated
  parse/
  ocr/
  output/
```

Lifecycle:

```text
claim attempt
 -> create attempt workspace
 -> acquire/validate source
 -> parser/OCR/downstream processing
 -> terminal attempt outcome or ownership loss
 -> orchestration cleanup
```

Normal cleanup SHOULD execute in orchestration `finally`/equivalent logic and MUST be idempotent.

M3 acquisition failure MUST remove incomplete `source.part` best-effort. Failure to delete a local partial file MUST NOT convert a deterministic acquisition error into a successful acquisition.

---

## 6. Crash-safe workspace scavenger

A separate scavenger/reconciliation function SHALL handle directories left by:

```text
SIGKILL
OOMKilled
container restart
node failure
process crash
lost worker
```

A directory is eligible for deletion only when all required conditions hold:

```text
age >= orphan_grace_period
AND durable attempt/job identity can be resolved or safely classified orphan
AND no live PostgreSQL lease authorizes the attempt
AND the attempt is terminal, superseded, expired, or otherwise non-live under TZ-02/TZ-13
```

A live lease is a hard deletion veto.

The scavenger MUST be safe under multiple replicas. Duplicate deletion attempts are acceptable; deleting a live workspace is not.

The scavenger SHOULD emit audit/log/metric evidence for:

```text
workspace deleted
workspace retained because live
workspace unknown/unresolvable
cleanup failure
bytes reclaimed
```

A simple age-only command such as `find ... -mtime ... -delete` is explicitly non-compliant.

---

## 7. Nested-container policy

TZ-04 already requires bounded container inspection. M3.1 makes the depth rule explicit.

Configuration MUST include:

```text
max_nested_container_depth
```

Baseline 1.0 policy:

```text
root supported OOXML package = depth 0
recursive nested-container extraction = disabled
```

For M3 admission, embedded ZIP/OOXML/OLE-like payloads MUST NOT trigger recursive extraction. They may be retained as bounded package entries/provenance for a later parser contract.

If a future parser enables nested-container inspection, every recursive step MUST independently enforce:

```text
max_nested_container_depth
max_container_entries
max_total_uncompressed_bytes
max_single_entry_uncompressed_bytes
max_compression_ratio
workspace.max_attempt_bytes
```

The limits are cumulative where required to prevent a hierarchy of individually legal containers from exceeding the attempt budget.

Violation outcome:

```text
CONTAINER_NESTING_LIMIT
or
CONTAINER_RESOURCE_LIMIT
```

---

## 8. Acquisition timeout policy

Typed configuration MUST define at least:

```text
storage.connect_timeout
storage.read_timeout
acquisition.total_deadline
```

Semantics:

- connect timeout protects dependency establishment;
- read timeout protects stalled storage reads;
- total deadline bounds the entire acquisition operation independently of individual successful reads.

Timeout errors MUST be distinguishable from deterministic file-validation failures.

Recommended classification:

```text
STORAGE_CONNECT_TIMEOUT      -> transient dependency failure
STORAGE_READ_TIMEOUT         -> transient dependency failure
ACQUISITION_DEADLINE_EXCEEDED -> retryable according to TZ-13 policy
```

Retry MUST remain bounded and lease-aware. A worker that loses its lease MUST stop authoritative processing even if the storage operation later returns.

---

## 9. Typed acquisition configuration

The following implementation defaults are not protocol constants and MUST be externally configurable with validated bounds:

```text
max_source_bytes
max_container_entries
max_total_uncompressed_bytes
max_single_entry_uncompressed_bytes
max_compression_ratio
max_nested_container_depth
max_image_width
max_image_height
max_image_pixels
max_tiff_pages
max_text_probe_bytes
max_signature_probe_bytes
validation_profile
storage.connect_timeout
storage.read_timeout
acquisition.total_deadline
workspace.*
```

Configuration ownership follows TZ-15.

Requirements:

1. negative/zero values are rejected where nonsensical;
2. contradictory limits fail startup;
3. secrets are not part of these diagnostic settings;
4. active non-secret limits/profile SHOULD be visible in startup diagnostics;
5. configuration changes that alter deterministic admission behavior SHOULD change `validation_profile` or an equivalent processing fingerprint component.

---

## 10. Observability contract

M3.1 introduces instrumentation ports/hooks. The concrete Prometheus/OTel exposure remains TZ-14/M10.

Minimum metrics semantics:

```text
astra_indexator_acquisition_total{result,format}
astra_indexator_acquisition_failures_total{error_code}
astra_indexator_acquisition_duration_seconds{format}
astra_indexator_acquisition_bytes_total
astra_indexator_storage_requests_total{operation,result}
astra_indexator_storage_request_duration_seconds{operation}
astra_indexator_workspace_free_bytes
astra_indexator_workspace_cleanup_total{result}
astra_indexator_workspace_reclaimed_bytes_total
```

Allowed labels MUST remain bounded enums/categories.

Forbidden metric labels include:

```text
documentId
jobId
processingAttemptId
producerRequestId
fileName
objectKey
source URI
```

Those identifiers belong in structured logs/traces/audit records.

The acquisition service SHOULD depend on a narrow instrumentation interface with a no-op implementation available for unit tests and minimal deployments.

---

## 11. Failure taxonomy additions

M3.1 adds/clarifies:

```text
WORKSPACE_CAPACITY_EXCEEDED
WORKSPACE_IO_ERROR
CONTAINER_NESTING_LIMIT
STORAGE_CONNECT_TIMEOUT
STORAGE_READ_TIMEOUT
ACQUISITION_DEADLINE_EXCEEDED
```

Classification MUST remain compatible with TZ-13 retry/recovery policy.

`WORKSPACE_CAPACITY_EXCEEDED` is normally operational/retryable after capacity recovery, not evidence that the document bytes are invalid.

`CONTAINER_NESTING_LIMIT` is deterministic for the same source and unchanged policy and therefore MUST NOT be retried indefinitely.

---

## 12. Kubernetes / deployment requirements

TZ-18 deployment SHALL mount an explicit bounded workspace for worker roles that acquire/parse/OCR documents.

Baseline shape:

```yaml
volumeMounts:
  - name: work
    mountPath: /work/astra-indexator
volumes:
  - name: work
    emptyDir:
      sizeLimit: <deployment-value>
```

The exact volume implementation is deployment-specific; `emptyDir` is the baseline for ephemeral attempt data, not a requirement to persist local workspace.

Container filesystem `/tmp` MUST NOT be the implicit production capacity plan.

Pod ephemeral-storage requests/limits SHOULD be aligned with workspace policy so Kubernetes eviction behavior is predictable.

---

## 13. Required verification / acceptance criteria

M3.1 is complete only when automated tests prove at least:

### Workspace

- acquisition succeeds with adequate free capacity;
- acquisition is rejected before download when configured capacity guard fails;
- one attempt cannot exceed `max_attempt_bytes` through managed workspace writes;
- generated paths cannot escape workspace root.

### Cleanup

- terminal orphan workspace older than grace period is removed;
- expired/superseded attempt workspace is removable;
- live leased attempt workspace is never removed;
- cleanup is idempotent;
- concurrent scavengers do not corrupt live work.

### Containers

- existing OOXML traversal/entry/ratio/size tests remain green;
- recursive nested-container processing is disabled by default;
- configured nesting limit produces deterministic rejection;
- cumulative container/resource budgets cannot be bypassed by nesting.

### Timeouts

- connect timeout is classified correctly;
- stalled read is classified correctly;
- total acquisition deadline is enforced;
- timeout/retry cannot bypass M2 lease fencing.

### Observability

- successful acquisition records duration/bytes/result;
- validation failure records bounded `error_code` label;
- workspace cleanup records reclaimed bytes/result;
- high-cardinality identifiers are absent from metric labels.

### Configuration

- valid configuration starts;
- invalid/contradictory limits fail fast;
- canonical defaults remain explicit and testable;
- configuration override does not silently weaken hard safety floors if such floors are configured.

Integration tests SHALL continue to use real PostgreSQL/Testcontainers for lease/state-sensitive behavior. Full deployed SeaweedFS E2E remains part of TZ-17/M11, while adapter contract tests remain acceptable for M3 CI.

---

## 14. Definition of Done

M3/M3.1 is considered CLOSED when:

```text
streaming acquisition PASS
SHA-256/integrity PASS
format admission PASS
hostile-file baseline PASS
workspace capacity PASS
crash scavenger PASS
nested-container policy PASS
timeout classification PASS
instrumentation contract PASS
PostgreSQL fencing PASS
CI PASS
```

Only after this gate should implementation proceed to M4 Canonical Parser, because M4/M5 materially increase local-disk and decompression/decoding pressure.
