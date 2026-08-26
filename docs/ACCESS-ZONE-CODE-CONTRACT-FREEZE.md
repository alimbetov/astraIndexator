# AccessZoneCode Contract Freeze

Status: **FROZEN for AstraIndexator 1.0 / M8 delivery**

This document is the short canonical reminder for all future AstraIndexator work involving Access Zones. Detailed semantics remain in `TZ-10-access-zones-ttl.md`; AstraVector runtime authority remains `alimbetov/llm2` (`src/access_zone_registry/mod.rs` and `proto/astravector_embedding.proto`).

## 1. Canonical type and range

`accessZoneCode` is a public immutable **four-character string**:

```text
0000 .. 9999
```

Validation:

```regex
^[0-9]{4}$
```

Leading zeroes are significant. Never convert `accessZoneCode` to an integer in DTO, persistence, hashing, logging, replay, or wire mapping.

Examples:

```text
"0000" valid
"0001" valid
"0600" valid
"9999" valid
"1"    invalid
600    invalid wire/application type
```

## 2. Producer compatibility fields

The producer-facing compatibility surface is:

```text
accessZoneId
accessZoneIds[]
accessZoneCode
accessZoneCodes[]
```

For ingestion, singular/plural values normalize to **exactly one distinct effective zone**. Repeating the same code is allowed and deduplicates; multiple different codes for one ingestion job are rejected.

For retrieval, multiple IDs/codes are allowed subject to AstraVector limits.

## 3. Primary ingestion rule

Normal document upload/indexation SHOULD preserve `accessZoneCode` from producer input all the way to AstraVector:

```text
Spring Boot / producer
  accessZoneCode="0600"
        ↓
AstraIndexator boundary normalization
        ↓
IndexationJob.requested_access_zone_code="0600"
        ↓
PreparedArtifactCheckpoint.requested_access_zone_code="0600"
        ↓ restart/replay uses persisted value
AstraVectorDeliveryCoordinator
        ↓
StartLogicalDocumentIngestionRequest.access_zone_code="0600"
        ↓
AstraVector Access Zone Registry
```

The code must be passed **unchanged**. No integer conversion, padding repair, family inference, UUID derivation, or code replacement is allowed in AstraIndexator.

## 4. UUID is a separate representation, not a replacement

`accessZoneId` and `accessZoneCode` are two representations supported by AstraVector. AstraIndexator does **not** require a code to be converted/resolved to UUID before ingestion.

Rules:

- code-only ingestion is valid;
- id-only ingestion is valid when supplied by a trusted producer;
- when both are supplied they are a consistency assertion and AstraVector Registry is authoritative;
- AstraIndexator must not derive UUID from code;
- AstraIndexator must not overwrite persisted `requested_access_zone_code` with a UUID or with a code returned from another source.

AstraVector may return its internal `access_zone_id` in `DocumentRef`. AstraIndexator may persist that separately as downstream evidence (`DeliveryCheckpoint.resolved_access_zone_id`) for APIs that currently require `DocumentRef`. That field never replaces producer `requested_access_zone_code`.

## 5. Shared PostgreSQL does not change service ownership

AstraIndexator and AstraVector may use the same PostgreSQL database/instance, but separate schema ownership remains authoritative:

```text
astra_indexator.*  owned by AstraIndexator
astravector.*      owned by AstraVector
```

AstraIndexator must not resolve `accessZoneCode` by directly reading `astravector.access_zones`. The service contract is AstraVector gRPC/public facade. Shared PostgreSQL is deployment topology, not an integration API.

## 6. Current wire recovery caveat

`StartLogicalDocumentIngestion` accepts `access_zone_code` directly. A successful `FinalizeLogicalDocumentIngestion` returns `DocumentRef`, including AstraVector internal `access_zone_id`.

If Finalize has an ambiguous transport result and later `GetLogicalDocumentIngestionStatus` reports `COMPLETED`, the current status response does not expose `DocumentRef`, while `GetDocumentVectorStatus` currently requires an internal `access_zone_id` in its `DocumentRef`.

This is a **wire recovery identity gap**, not evidence that `accessZoneCode` requires UUID resolution. Code-based ingestion remains valid and the original code must remain durable. Any future fix should improve AstraVector public recovery/status contract rather than introducing cross-schema SQL lookup or local code-to-UUID derivation.

## 7. Required regression tests

CI must keep tests proving:

1. `"0001"` remains `"0001"` through normalization and PostgreSQL persistence;
2. plural `accessZoneCodes=["0600","0600"]` normalizes to `"0600"`;
3. distinct ingestion codes are rejected;
4. mismatched legacy/requested code fields are rejected instead of silently preferring one;
5. restart/replay preserves `requested_access_zone_code`;
6. code-only M8 coordinator sends `StartIngestionCommand.access_zone_code` unchanged and `access_zone_id=None`;
7. downstream `resolved_access_zone_id` never mutates/replaces `requested_access_zone_code`.

## 8. Review rule

Any change touching Access Zone DTOs, job creation, persistence, replay, coordinator, protobuf mapping, or Spring integration must preserve this contract. If a future AstraVector proto changes it, update this file, TZ-10, contract tests, and integration documentation in the same change.
