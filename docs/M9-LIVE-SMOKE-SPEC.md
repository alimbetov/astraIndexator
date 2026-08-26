# M9 — Real AstraVector Lifecycle Smoke Specification

## 1. Purpose

This document is the operator-run live gate for M9. Automated CI qualifies domain,
PostgreSQL, protobuf and reconciliation behavior. This smoke is executed against a real
AstraIndexator + AstraVector + Qdrant + SeaweedFS environment.

The smoke MUST use the normal production path. It must not repair state by directly
editing AstraVector-owned PostgreSQL tables or Qdrant.

## 2. Deployment topology

AstraIndexator and AstraVector use the same PostgreSQL instance/database in the approved
portable-local topology, with separate schema ownership.

```text
PostgreSQL instance/database
  ├─ astra_indexator.*   owned by AstraIndexator
  └─ AstraVector-owned schema/tables
```

Shared PostgreSQL is deployment topology, not the service integration API. All lifecycle
mutations and downstream evidence used by AstraIndexator must flow through the public
AstraVector protobuf facade.

Required runtime components:

- PostgreSQL with AstraVector + AstraIndexator migrations applied;
- SeaweedFS/object storage used by the normal acquisition path;
- AstraVector service generated from the pinned public protobuf contract;
- Qdrant configured for AstraVector;
- AstraIndexator worker using the M9 branch/build;
- model artifacts already available according to deployment policy.

## 3. Frozen fixture identity

Use a document with leading-zero AccessZoneCode and distinct public/storage names:

```text
documentId        = operator-generated UUID
accessZoneCode    = "0001"
sourceFileName    = "M9 Smoke — Публичный источник.pdf"
storageObjectId   = UUID
storageObjectName = <storageObjectId>.pdf
sourceUri         = SeaweedFS URI for storageObjectName
```

Assertions:

- `accessZoneCode` remains exactly `"0001"` through the whole path;
- `sourceFileName` remains the original user-visible filename;
- `storageObjectName` remains the UUID-based internal name;
- downstream `resolvedAccessZoneId`, when returned, is additional evidence and never
  overwrites the producer code.

## 4. Scenario A — first version

1. Upload/store fixture through the approved source path.
2. Create document version `1`.
3. Let the normal M2..M8 pipeline run.
4. Wait for AstraVector `searchable=true`.
5. Verify M9 local lifecycle is:

```text
v1 ACTIVE
is_current = true
```

6. Verify Knowledge Inventory contains:

```text
sourceFileName    = public filename
storageObjectId   = expected UUID
storageObjectName = expected UUID filename
accessZoneCode    = "0001"
searchable        = true
lifecycleState    = ACTIVE
isCurrent         = true
```

7. Retrieve a known phrase through AstraVector retrieval and verify the returned source
provenance refers to the public/original filename, not the UUID storage name.

## 5. Scenario B — reindex without outage

1. Create version `2` for the same `documentId` with changed content.
2. While v2 is BUILDING, verify:

```text
v1 ACTIVE / current / searchable
v2 BUILDING / not current
```

3. Verify retrieval still returns v1 content while v2 is not ready.
4. Let v2 reach `searchable=true`.
5. Verify the atomic switch:

```text
v1 SUPERSEDED / is_current=false
v2 ACTIVE      / is_current=true
```

6. Verify exactly one locally current ACTIVE version exists.
7. Verify retrieval returns the v2 content according to AstraVector activation/search
semantics.

## 6. Scenario C — candidate failure

1. Start version `3` using a deliberately invalid/unsupported test source that fails before
activation.
2. Verify:

```text
v2 ACTIVE / current / searchable
v3 FAILED / not current
```

3. Verify v2 retrieval remains available.

## 7. Scenario D — cancellation before downstream session

1. Create a new candidate version and request cancel before AstraVector Start.
2. Verify job and lifecycle reach CANCELLED.
3. Verify no delete is inferred from cancel.
4. Verify the current ACTIVE version is unchanged.

## 8. Scenario E — cancellation with active ingestion session

1. Start another candidate and wait until an ingestion session exists.
2. Request cancel.
3. Verify M8 Abort reconciliation is used.
4. Verify ABORTED/terminal result leads to local CANCELLED.
5. If Finalize wins the race, verify the system does not lie about cancellation: it moves to
DELETE_PENDING and reconciles the derived delete operation.

## 9. Scenario F — asynchronous delete

1. Delete a SUPERSEDED version first.
2. Verify local state becomes DELETE_PENDING before downstream mutation.
3. Verify public `DeleteDocumentVectorsFacade` is called with the exact document version.
4. While AstraVector reports DELETE_SCHEDULED/DELETING, local state remains DELETE_PENDING.
5. Only after public downstream status reports DELETED may local state become DELETED.
6. Re-run the same producer delete request and verify idempotent success.

Repeat for the current ACTIVE version. Do not expect automatic reactivation of an older
SUPERSEDED version; M9 deliberately has no hidden rollback activation.

## 10. Scenario G — crash/restart reconciliation

At minimum test these process-kill windows:

### G1 — after v2 searchable, before lifecycle activation commit

Restart AstraIndexator and verify the same v2 is activated; no v3 is created.

### G2 — after lifecycle activation commit, before job completion

Restart and verify the active switch is idempotent and the job can finish without a second
semantic version.

### G3 — after Abort accepted, before local CANCELLED commit

Restart and verify the same CANCEL operation is reconciled.

### G4 — after Delete accepted/scheduled, before local DELETED commit

Restart and verify the same durable DELETE operation/idempotency key is reused. The system
must observe status and must not create a logically new delete/version.

### G5 — worker dies while lifecycle_operation is RUNNING

After its PostgreSQL operation lease expires, another worker must reclaim the same operation
via `FOR UPDATE SKIP LOCKED` and continue it.

## 11. Database assertions

Operator SQL may be used to observe AstraIndexator-owned tables only:

```text
astra_indexator.indexation_job
astra_indexator.document_version_lifecycle
astra_indexator.lifecycle_operation
astra_indexator.delivery_checkpoint
astra_indexator.delivery_batch
astra_indexator.knowledge_inventory
astra_indexator.job_event
```

Do not use direct AstraVector-table inspection as correctness evidence for AstraIndexator.
AstraVector status must be confirmed through its public API.

## 12. Pass criteria

M9 live smoke passes when all of the following are demonstrated:

- first version becomes searchable and ACTIVE;
- reindex keeps previous ACTIVE version available until the candidate is ready;
- activation leaves exactly one current ACTIVE version;
- failed candidate never destroys the previous active version;
- cancel-before-Start requires no downstream mutation;
- active-session cancel is reconciled through Abort;
- delete is asynchronous and only reaches DELETED after downstream confirmation;
- repeated producer lifecycle requests are idempotent;
- crash windows resume the same semantic operation/version;
- `accessZoneCode="0001"` survives unchanged;
- public source filename survives independently from UUID storage identity;
- no cross-schema SQL shortcut is used.

Record service versions/commit SHAs, the fixture documentId, lifecycle operation IDs and the
final pass/fail observations in the smoke report.
