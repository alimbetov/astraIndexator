# TZ-10 — Access Zones & TTL

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-10
- **Title:** Access Zones & TTL
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-02, TZ-09, TZ-11, TZ-12, TZ-13, TZ-16, TZ-17
- **Authoritative downstream contract:** AstraVector `llm2/main` public facade and Access Zone Registry
- **Consumer reference:** `agent-astradeployment-portable-local-1.0/docs/integration/ACCESS_ZONE_AND_TTL_SEMANTICS.md`

---

## 2. Purpose

This specification defines how AstraIndexator accepts, validates, normalizes and propagates access-zone and TTL intent while remaining contract-compatible with AstraVector.

AstraIndexator SHALL NOT invent downstream access-zone semantics and SHALL NOT duplicate AstraVector's authorization, zone-registry or TTL lifecycle implementation.

The responsibility boundary is:

```text
Spring Boot / platform
        |
        | access-zone assignment + optional TTL request
        v
AstraIndexator
        |
        | validate shape and platform catalog
        | normalize selectors
        | preserve original intent
        | require one effective ingestion zone
        v
AstraVector public ingestion facade
        |
        | resolve access-zone registry
        | apply effective TTL policy
        | own persistence/search exclusion/cleanup
        v
PostgreSQL + Qdrant
```

---

## 3. Source-of-truth rule

The authoritative runtime semantics are defined by AstraVector, specifically:

- `src/access_zone_registry/mod.rs`;
- `proto/astravector_embedding.proto`;
- AstraVector runtime configuration;
- AstraVector persistence/lifecycle implementation.

The deployment/integration repository is a consumer-facing reference and must remain aligned with those sources.

AstraIndexator SHALL NOT fork or independently evolve the code-to-TTL matrix.

The **AstraIndexator canonical knowledge-zone catalog** defined below is a platform ingestion classification/allowlist layered on top of valid AstraVector codes. It does not replace AstraVector Registry authority and does not redefine TTL math.

---

## 4. Access-zone representations

The platform supports two representations of one logical zone:

```text
accessZoneId   / access_zone_id   = UUID-backed internal identity
accessZoneCode / access_zone_code = immutable four-digit public code
```

### 4.1 Access-zone code format

Canonical format:

```text
0000 .. 9999
```

Validation:

```regex
^[0-9]{4}$
```

The code MUST be represented as a string, not an integer, because leading zeroes are significant.

Valid examples:

```text
0000
0001
0999
1000
1500
9500
9999
```

Invalid examples:

```text
1
150
10000
15A0
LEGAL
INTERNAL
```

### 4.2 UUID identity

`accessZoneId` is the canonical UUID-backed internal identity used by AstraVector storage/search internals.

For auto-created codes, current AstraVector derives a stable UUID v5 from the code. AstraIndexator SHALL NOT independently depend on or reproduce that algorithm as business logic; the registry remains authoritative.

---

## 5. Producer compatibility surface

A Spring Boot producer MAY expose the established compatibility shape:

```text
accessZoneId
accessZoneIds[]
accessZoneCode
accessZoneCodes[]
```

This shape exists to support both singular and plural application DTOs and to remain compatible with retrieval contracts.

Recommended producer preference:

```text
normal business traffic -> accessZoneCode / accessZoneCodes
internal integrations    -> accessZoneId / accessZoneIds allowed
```

The producer SHOULD prefer one representation in normal requests. Sending IDs and codes together is primarily a consistency-check scenario.

For the canonical AstraIndexator 1.0 knowledge catalog, the producer SHOULD assign one approved `accessZoneCode` at upload/job creation time. AstraIndexator preserves that code unchanged through the durable job and downstream ingestion.

---

## 6. Normalization rules

AstraIndexator SHALL normalize singular/plural fields without silently discarding values.

### 6.1 Same-representation normalization

```text
accessZoneId + accessZoneIds[]
        -> trim
        -> validate UUID
        -> distinct set

accessZoneCode + accessZoneCodes[]
        -> trim
        -> validate ^[0-9]{4}$
        -> distinct set
```

Ordering MAY be made deterministic for persistence/hashing, but ordering has no authorization meaning.

### 6.2 Code + ID consistency

If both code-based and ID-based selectors are provided, they SHALL NOT be treated as independent unions.

They must resolve to the same effective zone set.

Conceptually:

```text
resolve(codes) -> UUID set A
resolve(ids)   -> UUID set B

A == B -> valid
A != B -> ACCESS_ZONE_ID_CODE_MISMATCH
```

The authoritative resolution belongs to AstraVector/access-zone registry. AstraIndexator MAY perform local pre-validation only when it has a trusted synchronized registry view; server validation remains authoritative.

---

## 7. Ingestion scope: exactly one effective access zone

This is a critical invariant.

One AstraVector indexed document version belongs to exactly one effective access zone.

Therefore the producer compatibility surface may contain singular/plural forms, but one AstraIndexator ingestion job MUST normalize to exactly one distinct effective zone.

Valid examples:

```json
{"accessZoneCode":"0600"}
```

```json
{"accessZoneCodes":["0600","0600"]}
```

The latter deduplicates to one effective code.

Invalid ingestion example:

```json
{"accessZoneCodes":["0300","0600"]}
```

because it represents two different effective zones.

AstraIndexator SHALL reject such a job before downstream ingestion with a permanent validation error such as:

```text
MULTIPLE_INGESTION_ACCESS_ZONES_NOT_ALLOWED
```

AstraIndexator SHALL NOT fan-out one document version into multiple zones as an implicit behavior.

---

## 8. Retrieval scope differs from ingestion scope

AstraVector retrieval supports:

```text
access_zone_id
access_zone_ids[]
access_zone_code
access_zone_codes[]
```

and may search multiple zones in one request, subject to configured limits.

Therefore:

```text
ingestion -> exactly one effective zone
retrieval -> one or more effective zones
```

These semantics MUST NOT be conflated.

The current deployment default allows up to 50 zones for retrieval. This is configuration, not a permanent protocol constant.

---

## 9. Access level is separate from access zone

AstraVector defines visibility levels:

```text
PUBLIC       = 1
INTERNAL     = 2
CONFIDENTIAL = 3
RESTRICTED   = 4
```

Access zone and access level are different dimensions.

```text
access zone  -> indexing/search scope
access level -> visibility threshold inside trusted authorization context
```

AstraIndexator SHALL NOT derive trusted access level from untrusted file content or arbitrary user input.

The canonical knowledge-zone catalog is also not a replacement for `AccessLevel`; e.g. `0800 SECURITY` is a retrieval/indexing zone classification, not an automatic `RESTRICTED` access-level assignment.

---

## 10. AstraVector access-zone code matrix

The following table mirrors the current AstraVector code-matrix behavior and is included for integration understanding and test compatibility.

It is NOT an independent AstraIndexator TTL policy table.

| accessZoneCode range | Current default TTL behavior |
|---|---:|
| `0000–0999` | `0` days: zone policy permits never-expire |
| `1000–1499` | 182 days (~6 months) |
| `1500–1999` | 365 days (~12 months) |
| `2000–2499` | 547 days (~18 months) |
| `2500–2999` | 730 days (~24 months) |
| `3000–3499` | 912 days (~30 months) |
| `3500–3999` | 1095 days (~36 months) |
| `4000–4499` | 1277 days (~42 months) |
| `4500–4999` | 1460 days (~48 months) |
| `5000–5499` | 1642 days (~54 months) |
| `5500–5999` | 1825 days (~60 months) |
| `6000–6499` | 2007 days (~66 months) |
| `6500–6999` | 2190 days (~72 months) |
| `7000–7499` | 2372 days (~78 months) |
| `7500–7999` | 2555 days (~84 months) |
| `8000–8499` | 2737 days (~90 months) |
| `8500–8999` | 2920 days (~96 months) |
| `9000–9499` | 3102 days (~102 months) |
| `9500–9999` | 3650 days (10 years) |

Current AstraVector algorithm is conceptually:

```text
if code <= 0999:
    default_ttl_days = 0
else if code >= 9500:
    default_ttl_days = 3650
else:
    block  = floor((code - 1000) / 500)
    months = (block + 1) * 6
    default_ttl_days = months * 365 / 12
```

AstraIndexator MUST NOT calculate an authoritative TTL from this formula in runtime business logic.

---

## 10A. Canonical knowledge Access Zone catalog `0000–0999`

AstraIndexator 1.0 reserves ten **knowledge-zone families** inside the AstraVector-valid `0000–0999` range. Each family owns a block of 100 codes for future controlled subdivisions; v1 defines one canonical root code per family.

| Family range | Canonical code | `knowledgeType` | Intended knowledge scope |
|---|---:|---|---|
| `0000–0099` | `0000` | `GENERAL` | general/common platform knowledge |
| `0100–0199` | `0100` | `CORPORATE` | corporate policies, internal general documents |
| `0200–0299` | `0200` | `REGULATORY` | external/internal regulatory and normative materials |
| `0300–0399` | `0300` | `LEGAL` | contracts, legal opinions, legal clauses and templates |
| `0400–0499` | `0400` | `FINANCE` | financial procedures, reports and finance knowledge |
| `0500–0599` | `0500` | `HR` | HR policies, personnel procedures and HR knowledge |
| `0600–0699` | `0600` | `TECHNICAL` | technical documentation, software, infrastructure, API manuals |
| `0700–0799` | `0700` | `OPERATIONS` | business operations, runbooks, process instructions |
| `0800–0899` | `0800` | `SECURITY` | internal security standards, controls and security documentation |
| `0900–0999` | `0900` | `ARCHIVE` | long-lived archive/reference knowledge |

### 10A.1 Canonical upload rule

For normal AstraIndexator 1.0 ingestion, Spring Boot/platform selects one canonical zone during source upload/job creation:

```text
knowledgeType
    ↓ approved platform mapping
accessZoneCode = one of
0000 | 0100 | 0200 | 0300 | 0400 |
0500 | 0600 | 0700 | 0800 | 0900
    ↓
SeaweedFS source upload
    ↓
IndexationJob(accessZoneCode)
    ↓
AstraIndexator validation/preservation
    ↓
AstraVector registry resolution
```

Example:

```json
{
  "documentId": "20fd6906-cf10-4d2a-bdbf-31ae32316716",
  "documentVersion": 3,
  "knowledgeType": "TECHNICAL",
  "accessZoneCode": "0600",
  "ttlDays": 0
}
```

`knowledgeType` is optional descriptive/business metadata. `accessZoneCode` is the integration identifier and is authoritative for downstream zone selection.

### 10A.2 Allowlist semantics

Syntax validity and platform ingestion approval are separate checks:

```text
FORMAT VALID:
  any ^[0-9]{4}$ code

CANONICAL V1 KNOWLEDGE CATALOG:
  0000,0100,0200,0300,0400,
  0500,0600,0700,0800,0900
```

A code such as `0473` is syntactically valid for AstraVector, but a canonical-catalog producer profile SHOULD reject it with `ACCESS_ZONE_NOT_APPROVED_FOR_INGESTION` unless deployment configuration explicitly enables that registered subdivision.

This preserves future extensibility:

```text
06xx = TECHNICAL family
0600 = canonical TECHNICAL root
0601..0699 = reserved for future approved technical subdivisions
```

AstraIndexator MUST NOT infer the family by integer arithmetic as authorization/business policy. The approved catalog is explicit/versioned configuration.

### 10A.3 Registry provisioning requirement

A canonical catalog code is processable only if the corresponding zone is provisioned and ACTIVE in AstraVector Registry.

```text
catalog approved
+
registry ACTIVE
= eligible for ingestion
```

AstraIndexator does not create the zones and does not assume auto-create.

### 10A.4 TTL behavior for canonical catalog

All ten canonical root codes are inside `0000–0999`, therefore the current `llm2` registry default matrix yields:

```text
default_ttl_days = 0
allow_never_expire = true (for auto-created equivalent policy)
```

However request semantics remain:

```text
ttl_days = 0 -> inherit effective registry/platform policy
```

AstraIndexator MUST NOT rewrite `ttl_days=0` to an invented expiration date or unconditional forever flag. A positive explicit `ttlDays` MAY be propagated when allowed by the downstream registry/policy contract.

### 10A.5 Taxonomy vs access control

The catalog provides durable retrieval/indexing scopes useful for organizing knowledge. It MUST NOT be treated as the only authorization dimension or as a substitute for `AccessLevel`/upstream authorization.

---

## 11. Access-zone registry semantics

Current AstraVector registry resolves active zones by either code or UUID and exposes effective zone information including:

```text
access_zone_id
access_zone_code
default_ttl_days
allow_never_expire
status
```

Relevant zone states include:

```text
ACTIVE
DISABLED
DELETED
```

Only an ACTIVE zone is valid for normal indexing/search resolution.

AstraIndexator SHALL treat authoritative errors such as the following as permanent/precondition errors unless the platform explicitly changes registry state:

```text
ACCESS_ZONE_NOT_FOUND
ACCESS_ZONE_DISABLED
ACCESS_ZONE_DELETED
ACCESS_ZONE_NOT_ACTIVE
access_zone_code/access_zone_id mismatch
```

Blind retry MUST NOT be used for deterministic access-zone validation failures.

Registry unavailability is a transient infrastructure failure and MAY be retried according to TZ-02/TZ-13.

---

## 12. Auto-create behavior

Current AstraVector supports configurable access-zone auto-creation, but the deployment default is conservative and has auto-create-on-ingestion disabled.

AstraIndexator SHALL NOT assume that an unknown code will be auto-created.

AstraIndexator SHALL NOT create or invent access zones itself.

The platform/operator owns zone provisioning and activation policy.

---

## 13. TTL contracts in AstraVector

AstraVector currently exposes two distinct TTL-shaped ingestion contracts.

### 13.1 Single-call/full ingestion

The public facade contains:

```text
TtlPolicy.mode
TtlPolicy.ttl_seconds
TtlPolicy.expires_at
```

with modes:

```text
TTL_MODE_NONE
TTL_MODE_RELATIVE
TTL_MODE_ABSOLUTE
```

AstraIndexator 1.0 SHALL NOT rely on absolute-expiry semantics until the AstraVector contract explicitly stabilizes them end-to-end.

### 13.2 Session/chunked ingestion

The production-oriented large-document session path exposes:

```text
ttl_days
```

Semantics:

```text
ttl_days = 0 -> inherit effective zone/platform TTL policy

ttl_days > 0 -> request an explicit relative finite lifetime in days
```

`ttl_days = 0` MUST NOT be interpreted by AstraIndexator or Spring Boot as unconditional "never expire".

---

## 14. TTL ownership

AstraVector owns the effective lifecycle state for indexed/vector data, including:

- effective expiry;
- TTL bounds/policy enforcement;
- expired-document search exclusion;
- PostgreSQL lifecycle state;
- Qdrant projection removal;
- cleanup/retry/reconciliation;
- tombstone/metadata retention according to runtime policy.

AstraIndexator owns only the producer TTL intent and downstream delivery of that intent.

AstraIndexator MUST NOT delete Qdrant points itself when a document expires.

---

## 15. Canonical AstraIndexator lifecycle intent

AstraIndexator SHALL preserve the producer request without pretending it already knows the final effective AstraVector expiry.

Recommended internal representation:

```json
{
  "ttlIntent": {
    "mode": "INHERIT_ZONE_POLICY",
    "ttlDays": null,
    "ttlSeconds": null,
    "expiresAt": null
  }
}
```

or for the current large-document session path:

```json
{
  "ttlIntent": {
    "mode": "RELATIVE_DAYS",
    "ttlDays": 30
  }
}
```

AstraIndexator SHALL NOT persist a computed `expiresAt` as authoritative unless it was returned by AstraVector as effective runtime state or a future stabilized absolute-expiry contract explicitly makes the producer timestamp authoritative.

---

## 16. Recommended producer-facing TTL semantics

For AstraIndexator 1.0, producer-facing integration SHOULD favor semantics that can be represented without loss by the selected AstraVector ingestion path.

For session ingestion:

```text
absent/null TTL -> inherit zone/platform policy
explicit positive ttlDays -> relative finite lifetime
```

If the producer API supports seconds or absolute timestamps, the adapter SHALL reject or explicitly downgrade unsupported precision rather than silently changing semantics.

---

## 17. Persistence requirements in AstraIndexator

The durable job record SHALL retain enough information to reproduce the original intent and diagnose downstream resolution.

Recommended logical fields:

```text
requested_access_zone_id
requested_access_zone_ids
requested_access_zone_code
requested_access_zone_codes
normalized_access_zone_id
normalized_access_zone_code
knowledge_type                 optional descriptive classification
catalog_version                optional catalog/config revision

requested_ttl_mode
requested_ttl_days
requested_ttl_seconds
requested_expires_at

downstream_effective_expires_at   optional
```

For ingestion, normalization must produce exactly one effective selector pair before downstream delivery.

---

## 18. Propagation through canonical document processing

Access-zone and TTL intent belongs to the document-version/job envelope, not to text semantics.

Parser, OCR, normalizer and logical splitter MUST NOT infer or modify access-zone assignment or TTL policy.

Canonical relationship:

```text
IndexationJob
  |- DocumentIdentity
  |- AccessScope
  |- TtlIntent
  `- ParsedDocument / LogicalBlock tree
```

---

## 19. Downstream mapping baseline

AstraIndexator SHALL integrate with the public AstraVector ingestion facade rather than creating a duplicate custom REST wire protocol.

For session ingestion, `StartLogicalDocumentIngestionRequest` includes the relevant fields:

```text
access_zone_id
access_zone_code
document_id
document_version
...
ttl_days
```

The downstream request SHALL contain one zone by ID and/or code. If both are supplied, they must identify the same zone.

Large-document flow:

```text
StartLogicalDocumentIngestion
 -> AppendLogicalDocumentBlocks x N
 -> FinalizeLogicalDocumentIngestion
 -> GetLogicalDocumentIngestionStatus
 -> GetDocumentVectorStatus
```

---

## 20. Error classification

Baseline permanent validation/precondition errors:

```text
ACCESS_ZONE_REQUIRED
ACCESS_ZONE_CODE_INVALID
ACCESS_ZONE_ID_INVALID
ACCESS_ZONE_ID_CODE_MISMATCH
ACCESS_ZONE_NOT_APPROVED_FOR_INGESTION
MULTIPLE_INGESTION_ACCESS_ZONES_NOT_ALLOWED
ACCESS_ZONE_NOT_FOUND
ACCESS_ZONE_DISABLED
ACCESS_ZONE_DELETED
ACCESS_ZONE_NOT_ACTIVE
TTL_INVALID
TTL_MODE_UNSUPPORTED
TTL_OUT_OF_RANGE
```

Transient failures include registry/database/network unavailability and are handled through TZ-02/TZ-13 retry policy.

---

## 21. Trust-boundary rules

1. Access-zone assignment is supplied by the trusted platform/business boundary, not inferred from document contents.
2. AstraIndexator MUST NOT broaden an access scope.
3. Missing zone SHALL fail closed when downstream ingestion requires a zone.
4. Code/ID mismatch SHALL fail closed.
5. Unknown/disabled/deleted zones SHALL not be silently substituted.
6. Canonical catalog membership SHALL be validated for producer profiles that opt into the v1 catalog.
7. `callerAccessLevel` remains separate from `accessZoneCode`/`knowledgeType`.
8. Secrets MUST NOT be stored in metadata, source links or access-zone values.

---

## 22. Compatibility rules

AstraIndexator contract evolution SHALL preserve the following stable concepts:

```text
4-digit access-zone code
UUID-backed access-zone identity
one effective zone per indexed document version
multi-zone retrieval as a distinct read concern
ttl_days=0 means inherit policy, not forever
AstraVector registry owns effective TTL policy
```

The ten canonical v1 knowledge-zone root codes are a versioned platform catalog and may be extended only through explicit catalog/config evolution, not ad-hoc client invention.

---

## 23. Required verification evidence

Implementation SHALL include automated tests for at least:

1. `0000`, `0001`, `0999`, `1000`, `1499`, `1500`, `1999`, `2500`, `9500`, `9999` code-format compatibility;
2. rejection of `1`, `150`, `10000`, `15A0`, `LEGAL`;
3. leading-zero preservation;
4. singular/plural deduplication;
5. one-zone ingestion acceptance;
6. multiple-distinct-zone ingestion rejection;
7. code/UUID consistency success;
8. code/UUID mismatch rejection;
9. unknown/disabled/deleted zone handling against AstraVector contract test double or integration environment;
10. `ttl_days=0` mapping to inherit policy;
11. positive `ttl_days` propagation;
12. rejection of unsupported absolute TTL behavior;
13. retry classification for registry unavailable vs invalid zone;
14. downstream response/effective-expiry capture where available;
15. exact canonical root-code allowlist: `0000,0100,0200,0300,0400,0500,0600,0700,0800,0900`;
16. `knowledgeType -> canonical accessZoneCode` mapping for all ten catalog entries;
17. syntactically valid but unapproved code (e.g. `0473`) rejected by canonical-catalog profile;
18. configured approved subdivision (e.g. future `0601`) accepted only when explicit catalog config and ACTIVE registry state both exist;
19. `0000–0999` leading-zero round-trip through producer DTO, PostgreSQL and AstraVector adapter;
20. canonical catalog with `ttl_days=0` preserves inherit semantics rather than computing local expiry.

---

## 24. Acceptance criteria

### AC-01 — Numeric code contract
Only four-ASCII-digit access-zone codes are accepted.

### AC-02 — Leading zero preservation
`0001` remains `0001` end-to-end and is never normalized to integer `1`.

### AC-03 — One-zone ingestion
Every processable indexing job resolves to exactly one effective access zone before downstream ingestion.

### AC-04 — No implicit fan-out
AstraIndexator does not silently duplicate one document version into multiple zones.

### AC-05 — Code/ID consistency
When both selectors are present they must identify the same effective zone.

### AC-06 — Fail closed
Missing, invalid, unknown, disabled, deleted or mismatched zone information cannot broaden access or proceed silently.

### AC-07 — Registry authority
AstraIndexator does not independently create zones or become authoritative for downstream zone policy.

### AC-08 — Matrix compatibility
The documented range matrix matches the current AstraVector implementation and is used for verification/reference only, not duplicated runtime TTL policy logic.

### AC-09 — TTL inherit semantics
For session ingestion, `ttl_days=0` means inherit effective zone/platform policy and is never documented as unconditional never-expire.

### AC-10 — Positive TTL propagation
A positive explicit session `ttl_days` value is forwarded without unit conversion.

### AC-11 — No silent precision loss
Unsupported `ttl_seconds`/`expires_at` semantics are rejected or handled only by an explicitly approved TZ-11 mapping.

### AC-12 — Effective lifecycle authority
AstraVector remains authoritative for expiry, search exclusion, Qdrant cleanup and reconciliation.

### AC-13 — Retrieval distinction
The design explicitly preserves multi-zone retrieval while keeping ingestion single-zone.

### AC-14 — Security independence
Access-zone assignment is independent of parser/OCR/text content and cannot be inferred from document semantics.

### AC-15 — Canonical v1 catalog
Normal catalog-mode ingestion accepts the ten canonical root codes and preserves them unchanged from upload/job creation through AstraVector delivery.

### AC-16 — Catalog/registry dual validation
Catalog approval does not bypass AstraVector Registry ACTIVE-state validation; registry validity does not automatically make an arbitrary code part of the canonical producer catalog.

### AC-17 — Catalog extensibility
Reserved family subdivisions are enabled only through explicit versioned catalog configuration and are never inferred by integer arithmetic.

---

## 25. Decisions frozen by TZ-10

The following decisions are frozen for downstream design unless AstraVector contract changes:

1. Access-zone code is a string of exactly four ASCII digits `0000..9999`.
2. Access-zone UUID and code represent the same zone identity through AstraVector registry.
3. One indexed document version belongs to one effective access zone.
4. Plural access-zone selectors are primarily a retrieval/application compatibility concern; they do not authorize multi-zone ingestion in one job.
5. AstraIndexator does not perform implicit zone fan-out.
6. The code-to-default-TTL matrix is documented for compatibility but owned by AstraVector registry/configuration.
7. `ttl_days=0` on session ingestion means inherit policy, not unconditional never-expire.
8. AstraVector owns effective expiry and vector lifecycle.
9. AstraIndexator integrates through the public AstraVector facade and generated protobuf contract.
10. Exact absolute-expiry behavior remains unsupported by AstraIndexator until the downstream contract guarantees it end-to-end.
11. AstraIndexator 1.0 canonical knowledge root codes are `0000,0100,0200,0300,0400,0500,0600,0700,0800,0900`.
12. Their families reserve `00xx` through `09xx` respectively for future explicit subdivisions.
13. The canonical knowledge catalog is a platform ingestion allowlist/taxonomy layered over, not replacing, AstraVector Registry authority.
14. Producer assigns the canonical access zone at upload/job creation time; parser/OCR/splitter do not infer it.
