# M9 — Document Lifecycle, Reconciliation & Knowledge Inventory

## 1. Status

**IMPLEMENTATION SPECIFICATION — READY FOR DEVELOPMENT AFTER M8 MERGE**

M9 is implemented as one cohesive milestone. Internal implementation may be staged in commits, but acceptance is milestone-level: M9 is complete only when lifecycle, reindex, cancel, delete, reconciliation and Knowledge Inventory operate together with PostgreSQL durability and AstraVector evidence.

## 2. Purpose

M9 adds the long-lived business lifecycle of indexed documents on top of the M8 delivery path.

M8 proves that one immutable `documentId + documentVersion` can be delivered to AstraVector and become searchable. M9 must answer what happens afterwards:

- a newer version is indexed;
- indexing fails while an older version is still searchable;
- a producer requests cancellation;
- a document/version is deleted;
- a downstream mutation times out and its result is ambiguous;
- workers restart during lifecycle operations;
- operations are retried;
- operators need an authoritative inventory of knowledge state;
- AccessZone and TTL intent/evidence must remain visible without cross-schema shortcuts.

The core invariant is:

```text
job state != document lifecycle state != AstraVector vector state
```

These three state spaces must never be collapsed into one enum.

## 3. Sources of truth

Contract precedence:

```text
AstraIndexator TZ-10/TZ-11/TZ-12/TZ-13/TZ-14
+
actual alimbetov/llm2 protobuf/runtime contract
+
approved integration mapping in agent-astradeployment-portable-local-1.0
+
M8 implemented behavior and AccessZoneCode contract freeze
```

For downstream behavior:

```text
llm2 proto/runtime = wire/runtime authority
AstraIndexator = lifecycle/orchestration authority
```

AstraIndexator MUST NOT:

- read Qdrant directly;
- write Qdrant directly;
- read AstraVector-owned PostgreSQL tables as an integration shortcut;
- infer downstream mutation success only from a timeout/retry;
- derive `accessZoneId` from `accessZoneCode` locally;
- mutate the producer `accessZoneCode` for an existing document version.

Shared PostgreSQL is deployment topology, not a service-integration API.

## 4. Frozen AccessZone contract

M9 inherits `docs/ACCESS-ZONE-CODE-CONTRACT-FREEZE.md` unchanged.

```text
accessZoneCode  = String, ^[0-9]{4}$, lexical range "0000".."9999"
accessZoneCodes = List<String> with the same element contract
```

Leading zeroes are significant.

For one immutable document version:

```text
requestedAccessZoneCode is immutable producer intent
resolvedAccessZoneId is optional downstream evidence
```

They are separate values. A downstream UUID never replaces the producer code.

## 5. M9 scope

M9 includes all of the following:

1. document-version lifecycle state machine;
2. reindex/new-version semantics;
3. cancellation semantics;
4. delete semantics;
5. ambiguous downstream mutation reconciliation;
6. Knowledge Inventory projection;
7. searchable/vector synchronization visibility;
8. requested/resolved AccessZone visibility;
9. requested TTL and downstream authoritative expiry visibility when available;
10. crash/restart/retry qualification;
11. job/lifecycle consistency invariants;
12. audit events for every lifecycle transition.

M9 does NOT include:

- public/internal FastAPI endpoints — M10;
- Prometheus/tracing dashboards — M10;
- load/stress benchmarks — M11;
- deployment manifests — M12;
- direct activation through an undocumented/legacy AstraVector control plane.

## 6. Aggregate identity

The logical aggregate is a document with immutable numeric versions.

```text
DocumentIdentity {
  document_id: UUID
}

DocumentVersionIdentity {
  document_id: UUID
  document_version: int64 > 0
}
```

`document_version` is producer-visible and immutable after job creation.

A new source revision MUST create a new numeric version. M9 never mutates source identity of an existing `documentId + documentVersion`.

## 7. Separate state spaces

### 7.1 Job execution state

Existing M2 state remains operational:

```text
PENDING
PROCESSING
RETRY_WAIT
COMPLETED
FAILED
DEAD_LETTER
CANCELLED
```

This answers: **what happened to the execution job?**

### 7.2 Document lifecycle state

M9 introduces a semantic lifecycle projection:

```text
BUILDING
READY
ACTIVE
SUPERSEDED
CANCEL_PENDING
CANCELLED
DELETE_PENDING
DELETED
FAILED
```

Meaning:

- `BUILDING` — version is being prepared/delivered; not business-active yet;
- `READY` — downstream data is complete/searchable or ready under the selected activation policy, but lifecycle switch is not yet committed;
- `ACTIVE` — canonical current version for this document in the effective zone;
- `SUPERSEDED` — previously active version retained for history but no longer canonical current version;
- `CANCEL_PENDING` — cancellation requested while work may still exist downstream;
- `CANCELLED` — processing stopped and no further indexing work is owned locally;
- `DELETE_PENDING` — delete mutation is requested or its outcome is ambiguous;
- `DELETED` — downstream absence is confirmed according to the public contract;
- `FAILED` — this version failed without destroying a previously valid active version.

### 7.3 AstraVector state

AstraVector vector/session states remain raw downstream evidence, e.g.:

```text
ACTIVE
FINALIZING
COMPLETED
FAILED
ABORTED
EXPIRED
VECTORING
PUBLISHING
SYNCING
READY_TO_ACTIVATE
```

M9 stores raw downstream values; it does not redefine them.

## 8. Core lifecycle invariants

### 8.1 Previous searchable version survives reindex

When version `N` is active and version `N+1` is being built:

```text
vN     ACTIVE / searchable=true
vN+1   BUILDING / searchable=false
```

`vN` MUST remain available while `vN+1` is built.

Failure of `vN+1` results in:

```text
vN     ACTIVE
vN+1   FAILED
```

No outage of the previously active document is allowed.

### 8.2 Activation switch

A new version may become `ACTIVE` only after M8 readiness invariant is satisfied.

Default production requirement:

```text
GetDocumentVectorStatus.searchable == true
```

The local transaction switching lifecycle state must enforce at most one locally active version for the same effective lifecycle key.

### 8.3 No implicit version creation during recovery

Timeout or ambiguous downstream outcomes MUST NEVER create a replacement version.

Recovery always reconciles the same:

```text
documentId + documentVersion + ingestionSessionId / mutation identity
```

### 8.4 Immutable producer intent

For a persisted version these values are immutable:

```text
document_id
document_version
source_uri/source identity
source_content_hash when known
requested_access_zone_code/requested_access_zone_id
requested_ttl_days
```

## 9. Persistence model

M9 SHOULD evolve the existing `knowledge_inventory` instead of creating a competing inventory table.

### 9.1 `knowledge_inventory`

Required columns after M9:

```text
document_id UUID PK part
document_version BIGINT PK part
job_id UUID

lifecycle_state VARCHAR(32) NOT NULL
is_current BOOLEAN NOT NULL DEFAULT false

knowledge_type
source_file_name
source_content_hash
processing_fingerprint

requested_access_zone_code VARCHAR(4) NULL
requested_access_zone_id UUID NULL
resolved_access_zone_id UUID NULL
requested_ttl_days INTEGER NULL

ingestion_session_id UUID NULL

logical_fragment_count BIGINT NULL
logical_block_count BIGINT NULL

vector_state VARCHAR(64) NULL
searchable BOOLEAN NOT NULL DEFAULT false
ready_to_activate BOOLEAN NULL
expected_bindings BIGINT NULL
synced_bindings BIGINT NULL

raw_ttl_state VARCHAR(32) NULL
effective_expires_at TIMESTAMPTZ NULL

activated_at TIMESTAMPTZ NULL
superseded_at TIMESTAMPTZ NULL
cancel_requested_at TIMESTAMPTZ NULL
cancelled_at TIMESTAMPTZ NULL
delete_requested_at TIMESTAMPTZ NULL
deleted_at TIMESTAMPTZ NULL
failed_at TIMESTAMPTZ NULL

last_verified_at TIMESTAMPTZ NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Legacy `access_zone_code/access_zone_id/ttl_state` columns, if retained during migration, MUST be explicitly migrated/renamed or documented as compatibility aliases. M9 must not leave two writable sources of truth.

### 9.2 Constraints

Required DB constraints:

```text
document_version > 0
requested_access_zone_code IS NULL OR requested_access_zone_code ~ '^[0-9]{4}$'
requested_access_zone_id IS NOT NULL OR requested_access_zone_code IS NOT NULL
requested_ttl_days IS NULL OR requested_ttl_days >= 0
logical_fragment_count IS NULL OR logical_fragment_count >= 0
logical_block_count IS NULL OR logical_block_count >= 0
expected_bindings IS NULL OR expected_bindings >= 0
synced_bindings IS NULL OR synced_bindings >= 0
```

Lifecycle state allowed-value CHECK is mandatory.

### 9.3 Current-version uniqueness

Create a partial unique index representing one canonical current version per lifecycle partition.

Baseline key:

```text
(document_id)
WHERE is_current = true AND lifecycle_state = 'ACTIVE'
```

If the approved business model requires one current version per Access Zone, use:

```text
(requested_access_zone_code, document_id)
```

The implementation MUST choose this only after validating TZ-10/TZ-14 semantics. It may not silently change the aggregate key.

### 9.4 Lifecycle mutation record

Reuse `job_event` for job-scoped lifecycle audit and add a dedicated lifecycle operation table only if one mutation can outlive or exist independently of a single `indexation_job`.

Preferred M9 table:

```text
lifecycle_operation
```

Fields:

```text
id UUID PK
operation_type REINDEX|CANCEL|DELETE|RECONCILE
producer_request_id UUID UNIQUE
job_id UUID NULL
document_id UUID NOT NULL
document_version BIGINT NULL
requested_access_zone_code VARCHAR(4) NULL
requested_access_zone_id UUID NULL
status PENDING|PROCESSING|RETRY_WAIT|COMPLETED|FAILED|CANCELLED
attempt_count
next_retry_at
last_error_code
last_error_message
created_at
updated_at
completed_at
```

This is required for idempotent delete/cancel/reconciliation requests that cannot be represented safely as mutation of an already completed IndexationJob.

## 10. Application ports

M9 introduces explicit application ports; generated protobuf must remain below the anti-corruption boundary.

```python
class DocumentLifecycleRepository(Protocol):
    def get_version(...): ...
    def list_versions(...): ...
    def upsert_building(...): ...
    def activate_version(...): ...
    def mark_failed(...): ...
    def mark_cancel_pending(...): ...
    def mark_cancelled(...): ...
    def mark_delete_pending(...): ...
    def mark_deleted(...): ...
    def record_downstream_status(...): ...

class DownstreamLifecyclePort(Protocol):
    def get_vector_status(...): ...
    def get_ingestion_status(...): ...
    def abort_ingestion(...): ...
    def delete_document_version(...): ...
    def reconcile_delete(...): ...
```

`delete_document_version` and delete reconciliation MUST be mapped only to public/approved AstraVector wire operations verified against current `llm2` before implementation. If no adequate public RPC exists, M9 records an explicit upstream contract gap; it must not call legacy/internal RPCs by assumption.

## 11. Reindex semantics

Reindex means create/process a new immutable numeric version, not overwrite the active version.

Workflow:

```text
active vN
   ↓ producer requests vN+1
create IndexationJob(vN+1)
create/update KnowledgeInventory(vN+1=BUILDING)
   ↓
normal M2..M8 path
   ↓
searchable=true
   ↓
transactional lifecycle switch
   ├── vN   ACTIVE -> SUPERSEDED, is_current=false
   └── vN+1 READY  -> ACTIVE,     is_current=true
```

Switch transaction requirements:

- lock all relevant inventory versions for `document_id`;
- verify candidate version still has downstream `searchable=true` evidence;
- verify no cancellation/delete request won the race;
- enforce unique current-version index;
- emit lifecycle event(s);
- commit atomically.

If switch transaction fails, downstream vector data may exist but local current version remains old. Recovery retries only the local switch after re-verifying downstream state.

## 12. Cancellation semantics

Cancellation is not deletion.

### 12.1 Before downstream Start

```text
cancel_requested=true
→ stop processing
→ job CANCELLED
→ inventory CANCELLED
```

No AstraVector mutation is required.

### 12.2 Active M8 session

Use the M8.2.7 idempotent/reconcilable Abort path.

```text
CANCEL_PENDING
→ AbortLogicalDocumentIngestion
→ reconcile status
→ ABORTED / existing terminal state
→ CANCELLED
```

If finalization wins and the session becomes `COMPLETED`, cancellation must not pretend that no downstream version exists. M9 must continue with vector-status reconciliation and then apply the approved lifecycle/delete policy.

### 12.3 Already ACTIVE version

A cancellation request against an already active version MUST NOT imply delete. Return/record an explicit semantic conflict or map the request to a separate delete operation only if the producer contract explicitly requests deletion.

## 13. Delete semantics

Delete is asynchronous and reconciliation-driven.

Required lifecycle:

```text
ACTIVE/SUPERSEDED/FAILED
        ↓ delete request
DELETE_PENDING
        ↓ downstream delete mutation
        ├── confirmed success → DELETED
        └── timeout/ambiguous → RECONCILE SAME mutation/version
```

Rules:

1. never mark `DELETED` solely because a mutating RPC timed out;
2. never create a new document version to compensate for delete ambiguity;
3. repeated same producer delete request is idempotent;
4. delete of an already confirmed deleted version is idempotent success;
5. delete reconciliation must use public AstraVector evidence;
6. physical prepared-artifact cleanup is not part of the transactional delete correctness path;
7. object cleanup/GC must use age/safety windows and may be deferred to later operational work.

If deleting current ACTIVE version while an older SUPERSEDED version exists, M9 does NOT automatically reactivate the older version unless TZ-14 explicitly requires rollback activation. Default behavior is no hidden rollback.

## 14. Ambiguous mutation reconciliation

The M8 rule generalizes to all M9 downstream mutations:

```text
mutating RPC timeout = UNKNOWN OUTCOME
```

Never:

```text
timeout -> blindly issue logically new mutation
```

Always:

```text
timeout
  ↓
read authoritative status/evidence
  ↓
classify
  ↓
retry SAME mutation only when safe
```

Required generic classifications:

```text
CONFIRMED_SUCCEEDED
CONFIRMED_NOT_APPLIED
STILL_IN_PROGRESS
CONFIRMED_FAILED
UNKNOWN_RETRY_LATER
INTEGRITY_CONFLICT
```

Every reconciliation attempt is durable and restartable.

## 15. Knowledge Inventory projection

Knowledge Inventory is AstraIndexator's operational projection, not a replacement for AstraVector.

It answers:

- what document versions are known locally;
- which one is current;
- which version is searchable according to AstraVector evidence;
- requested `accessZoneCode` / optional requested UUID;
- downstream resolved UUID if exposed;
- requested TTL intent;
- authoritative expiry if exposed;
- logical fragment/block counts;
- vector synchronization counts;
- last downstream verification time;
- lifecycle failure/cancel/delete timestamps.

Inventory update rules:

- updated transactionally with local lifecycle transitions;
- downstream evidence is copied only from public AstraVector responses;
- `searchable` must never be inferred from local job completion;
- `effective_expires_at` must remain NULL if AstraVector does not expose authoritative expiry;
- raw downstream states should be stored for forward compatibility;
- unknown future downstream state must not crash projection code.

## 16. TTL semantics

M9 preserves M8 intent:

```text
requested_ttl_days = 0 / NULL according to boundary normalization
→ inherit AstraVector policy
```

M9 MUST NOT calculate authoritative expiry from wall-clock plus requested TTL unless the downstream contract explicitly defines and exposes that calculation as authoritative.

Inventory fields distinguish:

```text
requested_ttl_days     # producer intent
raw_ttl_state          # downstream evidence if available
effective_expires_at   # downstream-authoritative only
```

## 17. Failure semantics

Failure of a candidate new version MUST NOT damage the previously active version.

Example:

```text
v10 ACTIVE/searchable
v11 BUILDING
    ↓ OCR/AstraVector failure
v11 FAILED
v10 remains ACTIVE/searchable
```

Terminal local failure requires durable `last_error_code/message` and lifecycle event.

Retryable infrastructure failures remain operational job/lifecycle-operation retries, not semantic document failure until retry policy is exhausted.

## 18. Concurrency and fencing

All worker-owned lifecycle mutations initiated from processing jobs inherit M2 `LeaseToken` fencing.

Required protections:

- stale worker cannot activate a version;
- stale worker cannot mark current version superseded;
- stale worker cannot finalize cancellation after losing lease;
- lifecycle-operation workers use their own durable ownership/fencing if operations outlive IndexationJob lease;
- current-version switch uses row locks + unique DB constraint;
- two concurrent reindex completions cannot both become current;
- PostgreSQL time remains authoritative for retry scheduling/leases.

## 19. Audit events

At minimum emit durable events:

```text
LIFECYCLE_BUILDING_CREATED
LIFECYCLE_READY
LIFECYCLE_ACTIVATED
LIFECYCLE_SUPERSEDED
LIFECYCLE_FAILED
CANCEL_REQUESTED
CANCEL_RECONCILED
CANCELLED
DELETE_REQUESTED
DELETE_RECONCILIATION_REQUIRED
DELETE_CONFIRMED
INVENTORY_DOWNSTREAM_VERIFIED
TTL_EVIDENCE_UPDATED
```

Events must include enough identity for forensic reconstruction without storing secrets.

## 20. Migration plan

M9 Alembic migration must:

1. evolve `knowledge_inventory` to canonical M9 columns;
2. migrate existing M8-compatible rows safely;
3. add lifecycle CHECK constraints;
4. add requested AccessZone/TTL fields without losing leading zeroes;
5. add current-version partial unique index;
6. add lifecycle-operation table and indexes if adopted;
7. preserve downgrade safety where practical;
8. pass clean-install and upgrade-from-current-head tests.

No runtime `create_all()` is acceptable as production migration behavior.

## 21. Required automated qualification

### 21.1 Lifecycle unit tests

- initial version transitions BUILDING -> READY -> ACTIVE;
- old ACTIVE becomes SUPERSEDED only when candidate is ready;
- failed candidate leaves old ACTIVE unchanged;
- two candidates cannot both become current;
- illegal transitions fail fast;
- unknown downstream states are preserved defensively.

### 21.2 PostgreSQL integration

- migration from current M8 schema succeeds;
- clean database installs through Alembic head;
- partial unique current-version constraint works;
- row-lock activation race produces one winner;
- stale lease cannot activate/supersede;
- `"0001"` survives all lifecycle round trips;
- requested AccessZone code is never replaced by resolved UUID;
- requested TTL remains durable.

### 21.3 Reindex E2E with fake/contract port

```text
v1 ACTIVE searchable
→ create v2
→ M8 delivery v2 searchable
→ atomic switch
→ v1 SUPERSEDED
→ v2 ACTIVE
```

Also prove failed v2 leaves v1 ACTIVE.

### 21.4 Cancel qualification

- cancel before Start;
- cancel during active session;
- timeout Abort -> status reconciliation;
- finalize-wins race;
- restart during CANCEL_PENDING;
- already ACTIVE version is not silently deleted.

### 21.5 Delete qualification

- direct confirmed delete;
- timeout then confirmed success;
- timeout then not-applied -> safe same mutation retry;
- restart during DELETE_PENDING;
- duplicate producer delete request idempotency;
- delete of already deleted version is idempotent;
- no false DELETED on ambiguous outcome.

### 21.6 Knowledge Inventory qualification

- projection reflects BUILDING/ACTIVE/SUPERSEDED/FAILED/CANCELLED/DELETED;
- vector state/searchable counts come from downstream evidence;
- `searchable=false` after mere local completion is enforced;
- TTL expiry remains NULL when downstream does not expose it;
- leading-zero accessZoneCode remains exact;
- resolved UUID is optional separate evidence.

### 21.7 Crash/restart matrix

Simulate restart after each durable boundary:

```text
before lifecycle row creation
after BUILDING row commit
after M8 searchable evidence
after READY before activation switch
after old version superseded attempt rollback
after CANCEL_PENDING before Abort
after remote Abort before local commit
after DELETE_PENDING before RPC
after remote delete before local commit
during reconciliation
```

Recovery must continue the same semantic operation, not create replacement versions.

## 22. Live AstraVector qualification

Live smoke is operator-run when AstraVector/Qdrant/model credentials are available.

Minimum scenario:

```text
index v1 accessZoneCode="0001"
→ searchable=true
→ inventory ACTIVE

index v2 same document
→ while building, v1 remains retrievable
→ v2 searchable=true
→ lifecycle switch
→ inventory v1 SUPERSEDED, v2 ACTIVE

cancel a building v3
→ reconciled CANCELLED

delete selected non-current version
→ DELETE_PENDING
→ downstream confirmation
→ DELETED
```

Retrieval verification must prove no availability gap during reindex.

## 23. Implementation package layout

Recommended structure:

```text
src/astra_indexator/domain/
  document_lifecycle.py

src/astra_indexator/application/
  lifecycle_service.py
  reindex_service.py
  cancellation_service.py
  deletion_service.py
  lifecycle_reconciliation.py
  knowledge_inventory_service.py

src/astra_indexator/persistence/
  lifecycle_repository.py
  inventory_repository.py

src/astra_indexator/astravector/
  lifecycle_contracts.py       # only if not already represented in M8 contracts
  lifecycle_mapper.py          # generated protobuf mapping only
```

Avoid a monolithic `m9_service.py`.

## 24. Implementation order inside the single M9 milestone

Although M9 is accepted as one milestone, implementation should proceed dependency-first:

```text
A. lifecycle domain + DB migration
B. Knowledge Inventory repository/projection
C. activation/current-version transaction
D. reindex orchestration
E. cancel orchestration using M8 Abort
F. verified AstraVector delete contract + delete adapter
G. delete reconciliation
H. generic lifecycle reconciliation worker
I. crash/race/PostgreSQL qualification
J. live smoke documentation
```

No sub-step is considered a separately accepted milestone.

## 25. Definition of Done

M9 is **QUALIFIED** only when all are true:

1. one canonical current version is enforced transactionally;
2. previous active version remains searchable during reindex;
3. new version becomes current only after downstream readiness invariant;
4. failed candidate never destroys previous active availability;
5. cancel is distinct from delete;
6. active-session cancel uses M8 Abort reconciliation;
7. delete is durable, idempotent and ambiguity-safe;
8. no downstream timeout is treated as implicit success;
9. Knowledge Inventory is populated and restart-safe;
10. requested AccessZone code survives byte-for-byte, including leading zeroes;
11. resolved AccessZone UUID remains separate optional downstream evidence;
12. TTL intent/evidence are distinct and no expiry is invented locally;
13. no direct Qdrant or AstraVector DB integration exists;
14. stale workers cannot perform lifecycle transitions;
15. clean-install and upgrade migrations pass PostgreSQL tests;
16. race/restart/reconciliation test matrix passes;
17. package/lint/format/mypy/pytest/PostgreSQL CI is green;
18. live AstraVector smoke procedure exists and can validate reindex availability, cancel and delete against the deployed stack.

## 26. Explicit pre-implementation verification gates

Before writing downstream delete code, verify in current `alimbetov/llm2`:

- exact public RPC/message for delete of a document version;
- idempotency key/request identity semantics;
- how delete status can be queried after ambiguous timeout;
- whether delete addresses `accessZoneId`, `accessZoneCode`, or DocumentRef;
- whether current/activation state is exposed or mutated through the public ingestion facade;
- whether authoritative TTL expiry is exposed anywhere in the approved public contract.

Any mismatch must update this specification before code. No guessed RPC names are allowed.

## 27. Change-control rule

M9 implementation may refine internal class names, but may not silently change these frozen contracts:

```text
accessZoneCode format and producer ownership
numeric immutable documentVersion
previous-version availability during reindex
searchable=true as default completion proof
mutating timeout = ambiguous outcome
PostgreSQL durable recovery authority
no cross-schema/direct-Qdrant integration shortcut
```

If an upstream AstraVector limitation blocks one requirement, record an explicit contract gap and reconcile through approved public evidence rather than weakening lifecycle correctness.
