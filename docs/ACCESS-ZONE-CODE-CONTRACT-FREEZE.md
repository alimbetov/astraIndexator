# AstraIndexator AccessZoneCode Contract Freeze

## Status

**Status:** ACTIVE / AUTHORITATIVE FOR ASTRAINDEXATOR PRODUCER, DOMAIN AND INVENTORY ACCESSZONE IDENTITY  
**Current M8 remediation:** `docs/M8-COMPLETION-REMEDIATION-SPEC.md`

This document supersedes historical AstraIndexator requirements that allowed producer/domain AccessZone UUID selectors.

## Frozen invariant

AstraIndexator accepts, persists and propagates AccessZone producer intent exclusively as `access_zone_code`.

```text
Spring Boot / trusted producer
        |
        | accessZoneCode / accessZoneCodes
        v
boundary normalization
        |
        | exactly one effective four-digit code
        v
IndexationJob.access_zone_code
        |
        +--> M3/M4/M5/M6 context
        +--> M7 prepared-artifact lineage
        v
M8 StartIngestion
        |
        | access_zone_code=<original code>
        | access_zone_id=None
        v
AstraVector AccessZone registry
        |
        | code -> internal UUID
        v
private delivery_checkpoint.resolved_access_zone_id
        |
        v
GetDocumentVectorStatus / recovery only
```

## Producer boundary

Allowed input representations:

```text
accessZoneCode
accessZoneCodes
```

They must normalize to exactly one effective value.

Forbidden producer representations:

```text
accessZoneId
accessZoneIds
access_zone_id
requested_access_zone_id
```

Forbidden UUID input must be rejected rather than ignored.

## Code lexical contract

`accessZoneCode` is exactly four ASCII digits:

```text
^[0-9]{4}$
```

It is a string, never an integer. Leading zeroes are significant and must survive every boundary byte-for-byte.

Examples:

```text
0000
0001
0010
0100
0600
0999
9999
```

The current implementation retains `0000` as a valid legacy/canonical GENERAL code. Any future decision to reserve or forbid `0000` requires an explicit migration and reviewed contract change; it must not be changed implicitly inside M8 remediation.

## Domain/persistence rules

`IndexationJob` contains exactly one producer-owned AccessZone identity field:

```text
access_zone_code
```

The following fields are not part of the current domain model:

```text
access_zone_id
requested_access_zone_id
requested_access_zone_code
```

`KnowledgeInventory` uses `access_zone_code`, not AstraVector UUID.

M7 prepared-artifact checkpoint/replay preserves `access_zone_code` and must not request the producer to resubmit it after crash/restart.

## Downstream UUID exception

AstraVector internally resolves AccessZoneCode to its own UUID. Because the finalized public vector-status contract currently identifies `DocumentRef` using UUID, AstraIndexator may persist:

```text
delivery_checkpoint.resolved_access_zone_id
```

This value is private downstream technical evidence only. It may be used for public AstraVector status/reconciliation operations that require it.

It must not be used as:

- producer input;
- domain AccessZone identity;
- KnowledgeInventory identity;
- authorization/routing input from the producer;
- substitute for `access_zone_code`;
- a reason to read AstraVector private PostgreSQL/Qdrant state.

Conflicting replacement of a persisted resolved UUID is an integrity failure.

## TTL independence

TTL remains independent producer intent:

```text
ttl_days = 0  -> inherit AstraVector AccessZone/platform policy
ttl_days > 0  -> explicit finite relative TTL
```

AstraIndexator must not derive TTL from AccessZoneCode.

## M8 completion relationship

M8 Completion Remediation must preserve this contract while adding stable logical Start idempotency, verified source SHA-256, durable runtime failure execution, ambiguous Finalize handling and immutable delivery compatibility evidence.

In particular, ambiguous Finalize must never be solved by reintroducing producer UUID. If the finalized AstraVector public API cannot recover a required resolved UUID, AstraIndexator fails closed into explicit reconciliation rather than inferring identity or accessing private storage.

## Qualification expectations

Executable evidence must prove:

- leading-zero code preservation;
- singular/plural normalization to exactly one code;
- producer UUID rejection;
- persistence/replay code lineage;
- Start sends `access_zone_code` and no producer UUID;
- returned/resolved UUID remains confined to private downstream checkpoint/recovery paths;
- `ttl_days=0` inheritance is preserved.

Real AstraVector M8.F qualification is code-based. Producer “AccessZone by UUID success” is obsolete for AstraIndexator.
