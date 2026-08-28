# M8 Completion Remediation — Technical Specification

## 1. Status

**Milestone:** M8 AstraVector Delivery & Reliability  
**Work package:** Completion Remediation  
**Status:** SPECIFICATION — IMPLEMENTATION NOT STARTED  
**AstraIndexator baseline:** `main@ec7ddc97a6446fe40d822f7e5a0e081fba3ceeb6`  
**Baseline CI:** AstraIndexator CI #328 — SUCCESS  

This specification defines the remediation required before final real-AstraVector qualification and before M8 may be declared QUALIFIED.

The implementation phase SHALL be performed separately after this specification is reviewed and merged. This specification does not authorize production-code changes in the specification PR.

---

## 2. Purpose

The current M8 implementation has strong durable-delivery primitives but still contains release-level gaps around logical Start idempotency, source identity, unified failure execution, ambiguous Finalize recovery and replay compatibility evidence.

The purpose of Completion Remediation is to close those gaps without expanding M8 into document lifecycle management and without weakening already-qualified M1–M7 invariants.

Completion Remediation SHALL preserve the architecture:

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

The following producer/domain fields are forbidden:

```text
access_zone_id
accessZoneId
accessZoneIds
requested_access_zone_id
requested_access_zone_code
```

`accessZoneCode` / `accessZoneCodes` may be accepted at the external normalization boundary only to produce exactly one effective `access_zone_code`.

`access_zone_code` MUST match `^[0-9]{4}$` and leading zeroes are significant.

AstraVector owns code-to-UUID resolution.

The sole UUID exception in AstraIndexator is:

```text
delivery_checkpoint.resolved_access_zone_id
```

It is private downstream recovery/status evidence returned by the finalized AstraVector contract. It MUST NOT become producer identity, inventory identity, authorization input or a replacement for `access_zone_code`.

### 3.2 TTL

`ttl_days = 0` means inherit AstraVector AccessZone/platform policy.  
`ttl_days > 0` means explicit finite relative TTL.  
Negative values are invalid.

AstraIndexator MUST NOT derive TTL from AccessZoneCode.

### 3.3 Ownership and time

PostgreSQL remains authoritative for job ownership, retry scheduling and lease time.

Every authoritative worker-side mutation MUST remain fenced by:

```text
job_id + worker_id + lease_generation + non-expired lease
```

A downstream mutating RPC MUST NOT start unless the remaining PostgreSQL lease safely exceeds its RPC deadline plus configured safety margin.

### 3.4 Processing/replay boundary

M7 prepared artifacts remain the canonical expensive-processing restart boundary. M8 MUST NOT rerun parser/OCR/normalization/splitting when a compatible verified M7 artifact is available.

### 3.5 AstraVector boundary

Generated/pinned AstraVector protobuf + gRPC is the transport authority. AstraIndexator MUST NOT access AstraVector PostgreSQL or Qdrant directly and MUST NOT introduce a parallel handwritten wire protocol.

Session `COMPLETED` is not equivalent to searchable knowledge. Local successful completion requires authoritative vector readiness evidence.

---

# 4. CR-01 — Stable logical Start idempotency

## 4.1 Current gap

The current coordinator derives Start idempotency from local job identity. A new local job representing the same logical document version may therefore produce a different downstream idempotency key.

Local execution identity MUST NOT define downstream logical-document identity.

## 4.2 Required decision

The Start idempotency identity SHALL be deterministically derived from immutable logical document identity:

```text
document_id + document_version + verified source content hash
```

Canonical semantic form:

```text
astra-indexator:{documentId}:{documentVersion}:{contentHash}
```

An implementation MAY hash this canonical form before transport, but equivalent inputs MUST always produce the same key and any change to one identity component MUST change the key.

`job_id`, `attempt_id`, `worker_id`, `lease_generation` and ingestion session ID MUST NOT participate in the logical Start idempotency identity.

## 4.3 Recovery semantics

Retries, worker reclaim and M7 replay of the same logical document/version/content MUST reuse the same Start identity.

A conflicting replay for the same document/version with different verified content MUST fail closed or enter an explicitly qualified version/conflict path; it MUST NOT masquerade as the same logical Start.

## 4.4 Required evidence

Tests SHALL prove:

- different job IDs with same document/version/hash produce identical Start key;
- different content hash changes the key;
- different document version changes the key;
- worker reclaim does not change the key;
- M7 replay preserves the key;
- the key is deterministic across process restart.

## 4.5 Acceptance criterion

No production Start request is keyed by local job execution identity.

---

# 5. CR-02 — Verified source identity before downstream mutation

## 5.1 Current gap

M8 can currently construct Start with an empty source content hash when neither delivery input nor job state supplies one.

This weakens source provenance, idempotency and replay integrity.

## 5.2 Required decision

A non-empty, syntactically valid, verified SHA-256 source content hash is mandatory before the first AstraVector mutating RPC.

The authoritative value SHALL originate from qualified acquisition/prepared-artifact evidence. M8 MUST NOT invent a hash, silently use an empty string, or compute identity from unverified mutable source metadata.

Canonical representation SHALL be lowercase 64-character hexadecimal unless an earlier frozen source-hash contract explicitly defines another representation.

## 5.3 Failure semantics

Missing, malformed or lineage-conflicting source hash is a local integrity/input failure. It MUST fail before Start and MUST NOT be classified as transient AstraVector unavailability.

## 5.4 Required evidence

Tests SHALL prove:

- missing hash blocks Start;
- malformed hash blocks Start;
- hash mismatch between durable source/M7 evidence blocks delivery;
- valid M7 replay preserves the verified hash;
- no AstraVector mutating call occurs on source-identity failure.

## 5.5 Acceptance criterion

Every Start is backed by durable verified source SHA-256 evidence.

---

# 6. CR-03 — Durable runtime failure execution

## 6.1 Current gap

The codebase contains delivery, retry, reconciliation and fencing primitives, but `main` lacks one production execution layer that deterministically maps delivery outcomes/errors into durable job state transitions.

## 6.2 Required execution policy

A production executor SHALL own one claimed-job delivery attempt and apply exactly one of the following semantic dispositions:

```text
SUCCESS
    -> COMPLETED only after authoritative SEARCHABLE evidence

TRANSIENT / DEPENDENCY_UNAVAILABLE
    -> RETRY_WAIT with bounded backoff while attempt budget remains
    -> DEAD_LETTER when the qualified attempt budget is exhausted

PERMANENT_INPUT / PERMANENT_POLICY / RESOURCE_LIMIT
    -> FAILED

DOWNSTREAM_AMBIGUOUS
    -> RECONCILE
    -> never blind replay of an ambiguous mutation

OWNERSHIP_LOST
    -> ABANDON current execution
    -> no stale-worker authoritative mutation
```

Unknown/unclassified downstream failures MUST fail closed into an explicit operator-visible state/disposition; they MUST NOT default to blind retry.

## 6.3 Transaction/fencing requirements

Every durable transition performed by the executor MUST use the current lease token and PostgreSQL-time fencing.

If ownership is lost while handling an exception, the old worker MUST NOT persist retry/failure/completion decisions for the job.

Failure handling itself MUST be safe under process crash and duplicate invocation.

## 6.4 Backoff

Retry backoff SHALL be bounded and deterministic/configurable. Retry scheduling MUST use PostgreSQL time. Attempt exhaustion MUST have a durable terminal disposition and audit event.

## 6.5 Observability

Each disposition SHALL record enough correlation for operations:

```text
job_id
document_id
document_version
attempt_id
lease_generation
ingestion_session_id when known
error class/code
reconciliation reason when applicable
```

Credentials, tokens and sensitive SourceLink query material MUST NOT be logged.

## 6.6 Required evidence

The failure-injection matrix SHALL include at least:

- transient Start failure;
- transient Append failure;
- permanent input/policy failure;
- resource limit failure;
- attempt exhaustion;
- lease loss before failure persistence;
- crash after remote mutation but before local checkpoint;
- unknown downstream error;
- executor restart/reclaim;
- success path proving completion only after SEARCHABLE.

## 6.7 Acceptance criterion

No production delivery exception can escape without a defined durable or ownership-lost disposition.

---

# 7. CR-04 — Ambiguous Finalize recovery

## 7.1 Problem statement

Finalize is a mutating RPC. Timeout/transport loss after request transmission is ambiguous: AstraVector may have committed Finalize while AstraIndexator did not receive the response containing downstream `DocumentRef.access_zone_id`.

The finalized AstraVector vector-status wire currently requires that UUID. AstraIndexator producer identity remains code-only and MUST NOT solve this by reintroducing producer UUID.

## 7.2 Required recovery algorithm

After ambiguous Finalize, AstraIndexator SHALL first reconcile the existing ingestion session through public AstraVector APIs.

It MUST NOT create a new logical document version/session merely because the Finalize response was lost.

Conceptual flow:

```text
Finalize ambiguous
  -> persist/retain reconciliation evidence
  -> GetLogicalDocumentIngestionStatus(existing session)
     -> not terminal: remain bounded reconciliation/pending
     -> failed/conflict: classify explicitly
     -> completed:
          recover authoritative DocumentRef/resolved UUID if public contract permits
          -> GetDocumentVectorStatus
          -> SEARCHABLE => local completion
```

## 7.3 Contract-gap rule

If the finalized AstraVector public API can prove session completion but cannot recover the authoritative DocumentRef UUID needed for vector readiness, AstraIndexator SHALL enter an explicit durable operator-visible reconciliation failure/gap state.

It MUST NOT:

- infer a UUID from AccessZoneCode;
- query AstraVector PostgreSQL/Qdrant directly;
- mark the job COMPLETED without searchable proof;
- blindly re-Finalize or create a new logical version unless the downstream contract explicitly proves that operation safe.

`resolved_access_zone_id`, once obtained, SHALL be persisted only in the private delivery checkpoint and protected against conflicting replacement.

## 7.4 Required evidence

Tests SHALL cover:

- normal Finalize response with resolved UUID;
- Finalize ACK loss but session still processing;
- ACK loss and session completed with recoverable identity;
- ACK loss and completed session without recoverable identity;
- restart during reconciliation;
- conflicting recovered UUID;
- no duplicate logical version/session caused by ambiguity.

Real AstraVector qualification SHALL include an ambiguous Finalize scenario.

## 7.5 Acceptance criterion

Every ambiguous Finalize converges either to authoritative searchable completion or to an explicit durable operator-visible unresolved state; never to silent duplication or false completion.

---

# 8. CR-05 — Immutable delivery compatibility fingerprint

## 8.1 Purpose

M7 replay is safe only if the prepared artifact remains compatible with the M8 mapper/wire semantics used after restart or deployment.

## 8.2 Required fingerprint

A durable immutable compatibility fingerprint SHALL bind, at minimum:

```text
prepared artifact schema/revision
logical-block mapping contract revision
AstraVector protobuf/wire revision
canonical hash algorithm/revision
relevant structural-validation revision
```

The exact serialization SHALL be deterministic and versioned.

The fingerprint MUST NOT contain deployment-specific ephemeral values such as worker ID, job attempt, hostname or timestamp.

## 8.3 Replay rule

Before downstream mutation from a persisted M7 artifact, M8 SHALL verify compatibility.

Unknown/incompatible fingerprint MUST fail closed before Start. Compatibility MUST NOT be silently assumed after a deployment changes mapper/wire/hash semantics.

## 8.4 Required evidence

Tests SHALL prove:

- deterministic fingerprint generation;
- restart with same contract accepts replay;
- changed wire revision rejects incompatible replay;
- changed hash/mapping revision rejects incompatible replay;
- worker/attempt changes do not affect fingerprint.

## 8.5 Acceptance criterion

No persisted M7 artifact is replayed into M8 under unknown delivery semantics.

---

# 9. Documentation reconciliation requirements

The implementation phase SHALL reconcile historical documentation with the code-only AccessZone contract.

At minimum:

1. `README.md` SHALL point to Roadmap 2.0 and reflect the actual M1–M8 phase.
2. `IMPLEMENTATION-ROADMAP-2.0.md` SHALL remove producer/requested AccessZone UUID semantics and update M8 status from executable evidence.
3. `M8.0-CONTRACT-FREEZE-GAP-AUDIT.md` SHALL be explicitly marked historically superseded where it permits producer AccessZone UUIDs.
4. `ACCESS-ZONE-CODE-CONTRACT-FREEZE.md` SHALL remain the active AccessZone producer/domain authority unless replaced by an explicitly newer reviewed contract.
5. M8.F real-service qualification SHALL test code-only producer behavior; “AccessZone by UUID” is obsolete for AstraIndexator producer qualification.

Historical documents MAY retain old text for audit history only when clearly labelled superseded and when the current authority is unambiguous.

---

# 10. Required implementation sequencing

Implementation SHALL be performed in a separate implementation branch after this specification is approved.

Recommended order:

```text
CR-01 stable logical Start identity
  -> CR-02 mandatory verified source hash
  -> CR-05 compatibility fingerprint
  -> CR-03 runtime failure executor
  -> CR-04 ambiguous Finalize closure
  -> documentation/traceability reconciliation
  -> real AstraVector M8.F qualification
  -> M8.G final qualification
```

CR-03 and CR-04 may share durable reconciliation primitives, but the implementation MUST avoid creating a second delivery engine parallel to `AstraVectorDeliveryCoordinator`.

---

# 11. Qualification gates

Completion Remediation implementation is not QUALIFIED until all applicable gates pass:

```text
Ruff lint                         PASS
Ruff format                       PASS
M8 scoped mypy                    PASS
full pytest                       PASS
PostgreSQL integration            PASS
failure-injection matrix          PASS
migration verification if needed PASS
package build                     PASS
reviewed PR                       MERGED
post-merge main CI                PASS
```

In addition, M8 itself remains NOT QUALIFIED until real AstraVector M8.F evidence passes.

---

# 12. Out of scope

Completion Remediation SHALL NOT implement:

- M9 business/document lifecycle;
- delete/reindex product semantics beyond what is required to reconcile one M8 delivery;
- authorization policy redesign;
- AstraVector internal storage changes;
- direct Qdrant/PostgreSQL access in AstraVector;
- tokenizer-aware searchable chunking in AstraIndexator;
- embeddings in AstraIndexator;
- production deployment/Kubernetes work owned by M12;
- broad observability/platform API work owned by M10 except minimum failure correlation required by CR-03.

---

# 13. Definition of specification approval

This specification is APPROVED when:

- it is reviewed against current `main` implementation;
- AccessZone code-only semantics are unambiguous;
- CR-01..CR-05 have testable acceptance criteria;
- no requirement depends on AstraVector private persistence;
- implementation scope does not leak into M9/M10/M12;
- specification PR is merged with green CI.

Only after that gate may the implementation branch be opened and production code changed for Completion Remediation.
