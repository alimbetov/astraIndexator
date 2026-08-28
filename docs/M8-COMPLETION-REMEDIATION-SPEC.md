# M8 Completion Remediation — Technical Specification

## 1. Status

**Milestone:** M8 AstraVector Delivery & Reliability  
**Work package:** Completion Remediation  
**Status:** IMPLEMENTATION IN PROGRESS  
**AstraIndexator baseline:** `main@ec7ddc97a6446fe40d822f7e5a0e081fba3ceeb6`  
**Baseline CI:** AstraIndexator CI #328 — SUCCESS  
**Implementation branch:** `spec/m8-completion-remediation`  

This specification defines the remediation required before final real-AstraVector qualification and before M8 may be declared QUALIFIED. By explicit project decision, implementation is performed in this same reviewed branch; this document remains the normative acceptance contract for CR-01..CR-05.

---

## 2. Purpose

The current M8 implementation has strong durable-delivery primitives but still contains release-level gaps around logical Start idempotency, source identity, unified failure execution, ambiguous Finalize recovery and replay compatibility evidence.

Completion Remediation closes those gaps without expanding M8 into document lifecycle management and without weakening qualified M1–M7 invariants.

```text
producer
  -> accessZoneCode + ttl intent
  -> PostgreSQL durable job
  -> M3 acquisition
  -> M4/M5/M6 processing
  -> M7 immutable prepared artifact
  -> M8 lease-fenced AstraVector delivery
  -> authoritative AstraVector readiness
  -> local COMPLETED
```

---

## 3. Normative architecture decisions

### 3.1 AccessZone boundary

AstraIndexator producer/domain/inventory AccessZone identity is exclusively `access_zone_code`.

Forbidden producer/domain fields:

```text
access_zone_id
accessZoneId
accessZoneIds
requested_access_zone_id
requested_access_zone_code
```

`accessZoneCode` / `accessZoneCodes` may be accepted at the external normalization boundary only to produce exactly one effective `access_zone_code`.

`access_zone_code` MUST match `^[0-9]{4}$`; leading zeroes are significant. AstraVector owns code-to-UUID resolution.

The sole UUID exception in AstraIndexator is:

```text
delivery_checkpoint.resolved_access_zone_id
```

It is private downstream recovery/status evidence returned by finalized AstraVector. It MUST NOT become producer identity, inventory identity, authorization input or a replacement for `access_zone_code`.

### 3.2 TTL

`ttl_days = 0` means inherit AstraVector AccessZone/platform policy. `ttl_days > 0` means explicit finite relative TTL. Negative values are invalid. AstraIndexator MUST NOT derive TTL from AccessZoneCode.

### 3.3 Ownership and time

PostgreSQL remains authoritative for job ownership, retry scheduling and lease time. Every authoritative worker mutation MUST remain fenced by:

```text
job_id + worker_id + lease_generation + non-expired lease
```

A downstream mutating RPC MUST NOT start unless remaining PostgreSQL lease safely exceeds its RPC deadline plus safety margin.

### 3.4 Processing/replay boundary

M7 prepared artifacts remain the canonical expensive-processing restart boundary. M8 MUST NOT rerun parser/OCR/normalization/splitting when a compatible verified M7 artifact exists.

### 3.5 AstraVector boundary

Generated/pinned AstraVector protobuf + gRPC is the transport authority. AstraIndexator MUST NOT access AstraVector PostgreSQL/Qdrant directly and MUST NOT introduce a parallel handwritten wire protocol.

Session `COMPLETED` is not searchable proof. Local successful completion requires authoritative vector readiness evidence.

---

# 4. CR-01 — Stable logical Start idempotency

Start idempotency SHALL derive from immutable logical identity:

```text
document_id + document_version + verified source content hash
```

Canonical semantic form:

```text
astra-indexator:{documentId}:{documentVersion}:{contentHash}
```

`job_id`, `attempt_id`, `worker_id`, `lease_generation` and session ID MUST NOT participate. Retries, reclaim and M7 replay of the same document/version/content MUST reuse the same key. Conflicting content for the same logical version MUST fail closed or enter an explicitly qualified conflict/version path.

Required evidence proves deterministic equality across execution identity changes and inequality when document version/content identity changes.

**Acceptance:** no production Start request is keyed by local job execution identity.

---

# 5. CR-02 — Verified source identity before downstream mutation

A non-empty verified SHA-256 source content hash is mandatory before the first AstraVector mutating RPC.

The authoritative value SHALL originate from durable acquisition/job lineage and may be repeated by verified M7 evidence. M8 MUST NOT invent a hash, use an empty string or let payload evidence silently replace missing durable evidence.

Canonical representation is lowercase 64-character hexadecimal.

Missing/malformed/conflicting source hash is a local integrity/input failure before Start, not transient AstraVector unavailability.

Required evidence:

- missing hash blocks Start;
- malformed hash blocks Start;
- durable/M7 mismatch blocks delivery;
- valid replay preserves hash;
- no downstream mutation occurs on identity failure.

---

# 6. CR-03 — Durable runtime failure execution

One production executor SHALL own one claimed-job delivery turn and map every delivery exception to a defined disposition:

```text
SUCCESS
    -> COMPLETED only after authoritative SEARCHABLE evidence

TRANSIENT / DEPENDENCY_UNAVAILABLE
    -> RETRY_WAIT while budget remains
    -> DEAD_LETTER on exhaustion

PERMANENT_INPUT / PERMANENT_POLICY / RESOURCE_LIMIT / INTERNAL_BUG
    -> FAILED

DOWNSTREAM_AMBIGUOUS
    -> RECONCILE
    -> never blind retry

OWNERSHIP_LOST
    -> ABANDON
    -> no stale-worker authoritative mutation
```

Unknown/unclassified failures fail closed and MUST NOT default to retry.

Retry scheduling uses PostgreSQL time and bounded deterministic/configurable backoff. Durable transitions use current lease fencing. If ownership is lost during failure persistence, the stale worker returns ABANDON without a second authoritative mutation.

Required failure-injection evidence includes transient Start/Append, permanent failure, attempt exhaustion, lease loss, remote mutation/local checkpoint crash, unknown error, reclaim/restart and searchable success.

---

# 7. CR-04 — Ambiguous Finalize recovery

Finalize timeout/transport loss after request transmission is ambiguous. AstraVector may have committed while AstraIndexator did not receive `DocumentRef.access_zone_id`.

AstraIndexator SHALL reconcile the existing ingestion session through public AstraVector APIs first and MUST NOT create a new logical document version/session merely because the response was lost.

```text
Finalize ambiguous
  -> retain reconciliation evidence
  -> GetLogicalDocumentIngestionStatus(existing session)
     -> non-terminal: reconciliation pending
     -> terminal failure/conflict: explicit classification
     -> completed:
          recover authoritative DocumentRef/resolved UUID if public contract permits
          -> GetDocumentVectorStatus
          -> SEARCHABLE => local completion
```

If the finalized public API proves session completion but cannot recover the authoritative UUID required for readiness, AstraIndexator SHALL return/persist explicit operator-visible reconciliation gap evidence. It MUST NOT infer UUID from code, read AstraVector private storage, mark COMPLETED without searchable proof, blindly create a duplicate version or blindly replay an ambiguous mutation.

`resolved_access_zone_id`, once obtained, is private checkpoint evidence and conflicting replacement is an integrity error.

Real AstraVector M8.F qualification SHALL include ambiguous Finalize.

---

# 8. CR-05 — Immutable delivery compatibility fingerprint

M7 replay is safe only when its prepared artifact remains compatible with current M8 mapping/wire/hash/validation semantics.

A deterministic versioned delivery compatibility fingerprint SHALL bind at least:

```text
M7 prepared compatibility SHA-256
logical-block mapping contract revision
AstraVector pinned wire revision
canonical hash contract revision
structural validation revision
fingerprint format revision
```

It MUST exclude worker ID, attempt ID, hostname, timestamp and other ephemeral deployment values.

Before downstream mutation, current fingerprint SHALL be compared to any durable checkpoint value. Unknown/malformed/incompatible evidence fails closed before Start.

Required tests prove deterministic generation, same-contract replay acceptance, changed wire/mapping/prepared evidence incompatibility, and execution-identity independence.

---

# 9. Documentation reconciliation

Implementation SHALL:

1. update `README.md` to Roadmap 2.0/current M8 phase;
2. remove/supersede producer UUID semantics in Roadmap 2.0;
3. explicitly mark producer UUID portions of `M8.0-CONTRACT-FREEZE-GAP-AUDIT.md` historical/superseded;
4. keep `ACCESS-ZONE-CODE-CONTRACT-FREEZE.md` as active AccessZone authority unless explicitly replaced;
5. make M8.F qualification code-only and include producer UUID rejection rather than AccessZone-by-UUID producer success.

---

# 10. Implementation state in this branch

```text
CR-01 stable logical Start identity             IMPLEMENTED / qualification running
CR-02 mandatory verified source SHA-256         IMPLEMENTED / qualification running
CR-03 durable runtime failure executor          IMPLEMENTED / qualification running
CR-04 ambiguous Finalize convergence            EXISTING RECONCILIATION + explicit gap; final evidence open
CR-05 immutable delivery compatibility          IMPLEMENTED / qualification running
Docs reconciliation                             IN PROGRESS
M8.F real AstraVector qualification              NOT PART OF merge claim yet
```

This table is implementation tracking only; no item becomes QUALIFIED until required tests/CI/merge evidence exists.

---

# 11. Qualification gates

Completion Remediation implementation is not QUALIFIED until:

```text
Ruff lint                         PASS
Ruff format                       PASS
M8 scoped mypy                    PASS
full pytest                       PASS
PostgreSQL integration            PASS
failure-injection matrix          PASS
migration verification            PASS
package build                     PASS
reviewed PR                       MERGED
post-merge main CI                PASS
```

M8 itself remains NOT QUALIFIED until real AstraVector M8.F evidence passes.

---

# 12. Out of scope

Completion Remediation SHALL NOT implement M9 business/document lifecycle, product delete/reindex semantics beyond one-delivery reconciliation, authorization redesign, AstraVector internal storage changes, direct Qdrant/PostgreSQL access, AstraIndexator embeddings/tokenizer-aware searchable chunking, M12 deployment work, or broad M10 observability/API work beyond minimum CR-03 correlation.

---

# 13. Merge readiness

This branch is merge-ready only when CR-01..CR-05 have executable evidence, code-only AccessZone remains unambiguous, no implementation depends on AstraVector private persistence, scope does not leak into M9/M10/M12, and all branch quality/test/migration gates are green.

Merge plus post-merge `main` CI are required before Completion Remediation is called merged/qualified. Final M8 qualification additionally remains blocked on M8.F real AstraVector evidence.
