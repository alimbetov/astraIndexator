# TZ-17 — Testing & Verification

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-17
- **Title:** Testing & Verification
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01 through TZ-16, TZ-18
- **Primary goal:** convert architectural claims into executable evidence

---

## 2. Purpose

TZ-17 defines the verification strategy for AstraIndexator 1.0.

The project SHALL NOT treat unit-test coverage alone as proof that indexing works. Verification must demonstrate the complete behavioral contract:

```text
source accepted
  -> PostgreSQL durable job
  -> one of N workers claims it
  -> SeaweedFS source acquired safely
  -> parser reconstructs structure
  -> OCR enriches only where required
  -> normalization preserves meaning
  -> logical fragmentation is deterministic
  -> LogicalFragment[] maps deterministically to LogicalBlock[]
  -> prepared artifact is durable/replayable
  -> AstraVector ingestion is idempotent/reconcilable
  -> vector state becomes searchable
  -> retrieval returns expected evidence
  -> Knowledge Inventory reports the document correctly
  -> lifecycle/recovery/cleanup are observable
```

The specification also defines an **internal Smoke Test API** for controlled runtime verification. The Smoke API is not a second ingestion API and MUST drive the same production path used by normal jobs.

---

## 3. Verification philosophy

The evidence hierarchy is:

```text
static/schema checks
  < unit tests
  < component tests
  < integration tests
  < multi-replica/recovery tests
  < end-to-end indexing proof
  < retrieval-quality proof
  < production smoke proof
```

A higher layer does not replace lower layers, and lower layers do not substitute for end-to-end evidence.

Core rule:

> Every important architectural invariant from TZ-01..TZ-16 SHALL have at least one executable verification scenario or an explicitly documented reason why it cannot yet be automated.

---

## 4. Test suites

AstraIndexator SHALL maintain logically separate suites.

### 4.1 Unit

Fast deterministic tests without external infrastructure.

Examples:

```text
state transitions
processing-stage enum consistency
retry classification
lease/fencing predicates
access-zone selector normalization
canonical knowledge-zone catalog mapping
TTL intent mapping
file-type detection helpers
protected-span normalization
reading-order helpers
OCR decision rules
logical splitter boundaries
LogicalFragment -> LogicalBlock mapping
batch construction
idempotency-key construction
configuration schema validation
model-manifest validation
```

### 4.2 Component

One subsystem with controlled fakes/fixtures.

Examples:

```text
PDF parser against golden PDFs
DOCX parser against golden DOCX
OCR engine against fixed images
normalizer against multilingual fixtures
splitter against canonical ParsedDocument fixtures
SeaweedFS adapter against compatible object-storage fixture
AstraVector adapter against generated/fake gRPC server
```

### 4.3 Integration

Real PostgreSQL plus real or protocol-compatible dependencies where practical.

Required baseline:

```text
PostgreSQL
SeaweedFS-compatible storage
AstraIndexator worker
```

AstraVector integration tests SHOULD use the real AstraVector service for contract/recovery suites whenever the test environment can provide it.

### 4.4 Multi-replica

At least 3 AstraIndexator workers sharing one PostgreSQL queue.

This suite proves claim/lease/fencing/race behavior rather than merely calling coordinator methods sequentially.

### 4.5 E2E

Full processing path through AstraVector and retrieval proof.

### 4.6 Performance/resource

Large documents, bounded memory, concurrency/backpressure and model/runtime limits.

### 4.7 RAG quality

Golden corpus retrieval evidence across supported languages and structures.

### 4.8 Runtime smoke

Controlled internal API-driven probes in a deployed environment.

---

## 5. CI verification levels

Recommended gates:

### PR gate

```text
format/lint/type-check
unit
component lightweight
schema/contract checks
determinism fixtures
```

### Merge/main gate

```text
PR gate
PostgreSQL integration
SeaweedFS integration
parser/OCR golden corpus
AstraVector contract tests when available
```

### Release candidate gate

```text
multi-replica race suite
crash/recovery suite
real AstraVector E2E
retrieval-quality suite
model-supply/offline suite
resource/performance suite
```

### Deployment smoke gate

```text
non-mutating dependency probe
controlled E2E smoke fixture
retrieve proof
cleanup proof
```

---

# Part I — Internal Smoke Test API

## 6. Why a Smoke API is required

Health/readiness endpoints answer:

```text
is this process alive?
can it currently serve/claim work?
```

They do **not** prove:

```text
can a real document travel through the entire system?
can it become searchable?
can retrieval find the expected knowledge?
can cleanup complete?
```

Therefore AstraIndexator SHOULD expose a small internal smoke-test control surface.

It MUST NOT be used as Kubernetes liveness/readiness because a full smoke run is expensive and mutating.

---

## 7. Smoke API boundary

Baseline endpoints:

```http
POST /internal/v1/smoke-tests
GET  /internal/v1/smoke-tests/{smokeTestId}
POST /internal/v1/smoke-tests/{smokeTestId}/cleanup
```

Optional list endpoint:

```http
GET /internal/v1/smoke-tests?limit=...
```

Rules:

- endpoints are internal-only according to TZ-16;
- mutating smoke endpoints MUST NOT be exposed through public ingress;
- mutation SHOULD be disabled unless `smokeEnabled=true` (or equivalent approved deployment setting);
- `RECOVERY_E2E` MUST remain disabled in ordinary production deployments;
- callers cannot supply arbitrary source URLs, arbitrary document text, arbitrary access zones or arbitrary model names.

---

## 8. Smoke profiles

Server-side allowlisted profiles:

```text
DEPENDENCIES
PIPELINE
E2E_RETRIEVAL
OCR_E2E
RECOVERY_E2E        // non-production/verification environment only
```

### 8.1 DEPENDENCIES

Non-mutating or minimally mutating checks:

```text
PostgreSQL connectivity
SeaweedFS connectivity
local workspace writability
required model bundle readiness
AstraVector runtime/facade health
configuration validity
```

This profile does not prove indexing.

### 8.2 PIPELINE

Processes a deterministic fixture through the normal job path until AstraVector searchability proof, without requiring a retrieval assertion if retrieval facade is unavailable.

### 8.3 E2E_RETRIEVAL

Full preferred smoke:

```text
fixture
 -> SeaweedFS
 -> normal PENDING job
 -> worker claim
 -> full pipeline
 -> LogicalBlock[] delivery
 -> AstraVector searchable=true
 -> RetrieveContext
 -> expected marker/citation found
 -> PASS
 -> cleanup
```

### 8.4 OCR_E2E

Uses an approved scanned/mixed multilingual fixture requiring OCR and proves the OCR model path in addition to normal E2E behavior.

### 8.5 RECOVERY_E2E

Fault-injection profile for dedicated test environments. It SHALL NOT be enabled by default in ordinary production runtime.

---

## 9. Smoke request contract

Conceptual request:

```json
{
  "profile": "E2E_RETRIEVAL",
  "fixtureId": "smoke-multilingual-v1",
  "cleanup": true,
  "correlationId": "optional-caller-correlation"
}
```

Rules:

- `fixtureId` must refer to a server-approved immutable fixture manifest;
- caller cannot supply arbitrary storage location;
- caller cannot supply arbitrary `accessZoneCode`;
- smoke access zone is deployment configuration and MUST be an explicitly approved/ACTIVE zone;
- a dedicated subdivision from the `0000–0999` catalog space MAY be provisioned for smoke, but it MUST be explicit configuration rather than an inferred code;
- model/profile selection is server-side configuration;
- smoke IDs/document IDs are generated in an isolated reserved namespace;
- cleanup defaults SHOULD be environment-configurable and normally enabled.

---

## 10. Smoke start response

Conceptual asynchronous response:

```json
{
  "smokeTestId": "...",
  "profile": "E2E_RETRIEVAL",
  "state": "ACCEPTED",
  "jobId": "...",
  "documentId": "20fd6906-cf10-4d2a-bdbf-31ae32316716",
  "documentVersion": 1,
  "statusUrl": "/internal/v1/smoke-tests/..."
}
```

`documentVersion` is the same positive numeric canonical type used by TZ-01/TZ-02/TZ-09/TZ-11/TZ-12. Smoke MUST NOT introduce a string version DTO.

The HTTP call SHALL NOT remain open for the full OCR/vector/retrieval duration.

The actual work is represented durably and follows the normal asynchronous job path.

---

## 11. Smoke state machine

```text
ACCEPTED
 -> PREPARING_FIXTURE
 -> JOB_CREATED
 -> PROCESSING
 -> VECTORING
 -> SEARCHABILITY_CHECK
 -> RETRIEVAL_CHECK
 -> PASSED
 -> CLEANUP_PENDING
 -> CLEANED
```

Failure states:

```text
FAILED
TIMED_OUT
CLEANUP_FAILED
CANCELLED
```

A response SHOULD expose the deepest proven stage rather than only pass/fail.

---

## 12. Smoke status response

Conceptual response:

```json
{
  "smokeTestId": "...",
  "profile": "E2E_RETRIEVAL",
  "state": "PASSED",
  "startedAt": "...",
  "finishedAt": "...",
  "job": {
    "jobId": "...",
    "status": "COMPLETED",
    "processingStage": "FINALIZING_VECTOR_STATE"
  },
  "source": {
    "fixtureId": "smoke-multilingual-v1",
    "contentHash": "sha256:..."
  },
  "processing": {
    "parser": "...",
    "ocrModelId": "...",
    "ocrArtifactRevision": "...",
    "normalizerProfile": "...",
    "splitterProfile": "..."
  },
  "astravector": {
    "state": "ACTIVE",
    "searchable": true,
    "expectedBindings": 12,
    "syncedBindings": 12
  },
  "retrieval": {
    "executed": true,
    "expectedMarkerFound": true,
    "citationDocumentMatched": true
  },
  "cleanup": {
    "requested": true,
    "state": "PENDING"
  },
  "errors": []
}
```

No raw secret or unrestricted source text is returned.

---

## 13. Smoke fixture contract

Smoke fixtures SHALL be immutable, small and semantically distinctive.

Each fixture manifest SHOULD define:

```text
fixtureId
fixtureVersion
fileName
format
sha256
requiresOcr
languages
expectedStructuralAssertions
retrievalQueries[]
expectedMarkers[]
expectedCitationProperties
```

Example conceptual marker:

```text
ASTRA-SMOKE-KK-RU-EN-2026
```

Fixtures SHOULD include at least:

```text
smoke-native-pdf-v1
smoke-multilingual-v1
smoke-ocr-ru-kk-en-v1
smoke-docx-structure-v1
```

---

## 14. Smoke isolation

Smoke jobs MUST NOT contaminate business knowledge.

Required controls:

- dedicated configured smoke access zone;
- reserved document ID prefix/namespace;
- synthetic source fixtures only;
- explicit metadata marker `testData=true` or equivalent where supported;
- deterministic cleanup workflow;
- Knowledge Inventory identifies smoke/test records;
- smoke retrieval assertions use only the configured smoke zone.

The actual access-zone code is deployment configuration and SHALL NOT be hard-coded in TZ-17.

---

## 15. Smoke cleanup

Cleanup SHALL use normal lifecycle contracts:

```text
DeleteDocumentVectorsFacade
 -> reconcile DELETE_SCHEDULED / DELETING / DELETED
```

SeaweedFS fixture/source cleanup follows TZ-03 retention policy.

Smoke must not directly delete Qdrant points or AstraVector database rows.

A failed cleanup is visible as `CLEANUP_FAILED` and produces an operator-visible reconciliation item.

---

## 16. Smoke pass criteria

For `E2E_RETRIEVAL`, PASS requires all of:

```text
source fixture integrity verified
job created through normal persistence path
one worker completed processing
prepared artifact valid when applicable
AstraVector document state reconciled
searchable=true
retrieval query returns expected synthetic marker
citation points to smoke document/version
no unexpected duplicate document version created
```

Cleanup may be tracked separately so that a successful functional proof is not hidden by a later cleanup retry, but cleanup failure remains operationally visible.

---

# Part II — Contract and deterministic verification

## 17. DTO/schema contract tests

Contract tests SHALL cover:

```text
Spring Boot job DTO
PostgreSQL persistence mapping
canonical document schema
prepared manifest/JSONL schema
LogicalFragment -> LogicalBlock mapping
AstraVector generated protobuf compatibility
Knowledge Inventory DTO
Smoke API DTO
```

Unknown/additive fields SHOULD be tested according to the versioning rules in each relevant TZ.

---

## 18. Golden deterministic fixtures

Repeated processing under identical pinned configuration SHALL reproduce stable deterministic evidence where specified:

```text
source SHA-256
detected format
canonical reading order
element IDs
normalized text
fragment boundaries
fragment IDs
LogicalBlock IDs/order/parents
batch boundaries
idempotency keys
processing fingerprint
```

Where exact equality is intentionally not promised, the specification/test SHALL state the permitted variance.

---

## 19. P0 cross-language hashing proof

TZ-11 identifies a P0 gap for session hashes.

Before production strict session ingestion is considered verified, the project SHALL have golden vectors shared with AstraVector for:

```text
batch_content_hash
final_content_hash
```

The fixture SHALL define exact canonical bytes and expected SHA-256 digest.

Rust and Python tests MUST produce identical digests.

No independently guessed JSON serialization is accepted as proof.

---

## 20. Document-version contract proof

AstraIndexator 1.0 does not support an opaque canonical `documentVersion`. Tests SHALL prove:

```text
producer documentVersion is positive numeric
PostgreSQL persistence preserves it exactly
canonical DTOs preserve it exactly
session Start rejects values outside current uint32 wire range
uint64 status/delete identity is mapped without truncation
externalRevision remains metadata only
retry/restart does not renumber documentVersion
```

No opaque-string-to-numeric mapping table is required for the 1.0 baseline.

---

# Part III — Coordinator and recovery verification

## 21. Multi-replica race tests

At least three workers SHALL concurrently contend for jobs.

Required cases:

1. one PENDING job is claimed by exactly one lease generation;
2. many jobs are distributed without blocking convoy;
3. `FOR UPDATE SKIP LOCKED` behavior is demonstrated under contention;
4. expired lease is reclaimed;
5. active lease is not stolen;
6. stale worker cannot finalize after generation increment;
7. concurrent retry-ready jobs preserve priority/order policy;
8. cancellation race does not produce an invalid final state.

Assertions SHALL inspect durable PostgreSQL state, not only in-memory mocks.

---

## 22. Golden recovery scenarios

The following scenarios from TZ-13 are mandatory executable evidence:

```text
FR-01 worker crash during OCR
FR-02 crash after prepared artifact publication
FR-03 StartLogicalDocumentIngestion ACK lost
FR-04 AppendLogicalDocumentBlocks ACK lost
FR-05 Finalize ACK lost
FR-06 stale worker resumes after reclaim
FR-07 PostgreSQL outage / ownership uncertainty
FR-08 SeaweedFS outage
FR-09 AstraVector unavailable
FR-10 Qdrant projection loss handled by AstraVector, not Indexator
FR-11 cancellation during active session
FR-12 failed new version keeps prior searchable version available
FR-13 retry budget exhausted -> DEAD_LETTER
```

Each scenario SHALL assert both final state and absence of duplicate/unsafe side effects.

---

## 23. Lost-ACK fault injection

A test proxy/fake transport MAY deliberately drop responses after AstraVector accepted a mutation.

The test SHALL prove:

```text
Start timeout -> same idempotency key
Append timeout -> same session + batch index + hash
Finalize timeout -> status reconciliation before recreation
```

---

# Part IV — Storage/acquisition hostile-input verification

## 24. TZ-03/TZ-04 evidence

Mandatory examples:

```text
source mutation under same job -> SOURCE_CONTENT_MISMATCH
partial download never parser-visible
large source remains bounded-memory
prepared manifest published last
missing/corrupt part invalidates prepared artifact
stale worker cannot checkpoint orphan artifact
orphan GC respects references/age gate
fake PDF rejected
OOXML ZIP bomb rejected
path traversal entry rejected
excessive entry count rejected
encrypted unsupported container rejected
image pixel bomb rejected
TIFF page/pixel bounds enforced
unknown format rejected
invalid text encoding handled deterministically
```

---

# Part V — Parser/OCR/normalization/splitter quality

## 25. Parser corpus

Golden parser corpus SHALL cover:

```text
native PDF
scanned PDF
mixed PDF
multi-column PDF
RU/KK bilingual columns
DOCX headings/lists/tables/images
TXT
Markdown
image inputs
```

Assertions focus on structure and reading order, not only extracted character count.

---

## 26. OCR corpus

Baseline language evidence:

```text
ru
kk
en
ru+kk
ru+en
kk+en
ru+kk+en
```

Kazakh-specific letters SHALL appear in fixtures:

```text
ә ғ қ ң ө ұ ү һ і
Ә Ғ Қ Ң Ө Ұ Ү Һ І
```

Required OCR behaviors:

```text
native page not redundantly OCRed
scanned page OCRed
mixed page uses region-aware OCR
native/OCR duplicate suppressed
repeated logo does not flood index
confidence recorded
bad/missing model fails according to capability policy
CPU profile works
GPU profile works when enabled
runtime model download does not occur
```

---

## 27. Normalization corpus

Assertions include preservation of:

```text
Kazakh letters
ё
UUID
URL/email
IPv4/IPv6
package/class/method names
semantic versions
access-zone codes with leading zeroes
amounts/percentages/dates
clause/article numbering
code indentation
list/table structure
```

Conservative dehyphenation and page-furniture suppression SHALL be tested with positive and negative examples.

---

## 28. Logical splitter corpus

Verification SHALL cover:

```text
headings and hierarchy
long paragraphs
lists
tables
mixed-language technical prose
unknown language
forced size boundaries
semantic section boundaries
stable fragment IDs
numeric documentVersion
LogicalFragment -> LogicalBlock mapping
```

AstraIndexator SHALL NOT reproduce AstraVector tokenizer-size chunking in these tests.

---

# Part VI — AstraVector and lifecycle proof

## 29. Single-call integration

For a small document, verify:

```text
LogicalBlock mapping
one effective access zone
TTL intent mapping
IndexLogicalDocument
status reconciliation
searchable=true
retrieve proof
```

---

## 30. Session ingestion

For a large deterministic fixture, verify:

```text
Start
Append 0..N deterministic batches
Finalize
session status
vector status
searchable=true
```

Assertions:

```text
bounded memory
stable batch boundaries
same retry identity
no duplicate block ingestion
correct total block count
```

---

## 31. Access-zone verification

Tests SHALL prove both the real AstraVector code domain and the AstraIndexator canonical v1 catalog.

Wire/registry compatibility:

```text
0000..9999 format validation
leading zero preservation
ID/code same-zone consistency
ID/code mismatch failure
multiple distinct ingestion zones rejected
unknown/disabled zone failure when configured by AstraVector
retrieval may use multiple zones separately from ingestion
```

Canonical v1 root catalog:

```text
0000 GENERAL
0100 CORPORATE
0200 REGULATORY
0300 LEGAL
0400 FINANCE
0500 HR
0600 TECHNICAL
0700 OPERATIONS
0800 SECURITY
0900 ARCHIVE
```

Required assertions:

```text
all ten canonical roots round-trip byte-for-byte as 4-character strings
knowledgeType -> accessZoneCode mapping is deterministic
producer assigns exactly one canonical zone at job creation
syntactically valid unapproved code (e.g. 0473) is rejected in catalog mode
configured subdivision is accepted only when both catalog-approved and AstraVector Registry ACTIVE
catalog mapping does not derive AccessLevel
catalog mapping does not compute TTL locally
```

The test suite MAY contain the current code→TTL compatibility matrix from TZ-10 as a downstream contract fixture, but AstraIndexator runtime logic MUST NOT implement it as policy.

---

## 32. TTL/lifecycle verification

Tests distinguish:

```text
ingestion session expires_at
document effective expiry
```

`ttl_days=0` SHALL verify inheritance behavior rather than asserting unconditional never-expire.

For every canonical root in `0000–0999`, tests SHALL prove that AstraIndexator forwards `ttl_days=0` unchanged when inheritance is requested and does not synthesize an expiration timestamp.

Exact remaining-lifetime tests require AstraVector to expose authoritative effective expiry according to TZ-14. Until then, Knowledge Inventory SHALL assert:

```text
INHERITED_UNRESOLVED / UNKNOWN
```

rather than a guessed date.

---

## 33. Version lifecycle

Required cases:

```text
v1 searchable
v2 indexing -> v1 remains available
v2 failure -> v1 remains available
v2 success -> expected lifecycle behavior
reindex auditable
async delete progresses through scheduled/deleting/deleted
same-version duplicate is idempotent or explicitly rejected according to contract
numeric documentVersion remains stable through restart/recovery
```

---

# Part VII — Knowledge Inventory and observability proof

## 34. Inventory correctness

For a successfully indexed document, verify that Inventory exposes:

```text
documentId/documentVersion
externalRevision when present
source hash/format
job status
processing fingerprint
accessZoneId/accessZoneCode
knowledgeType/catalog version when applicable
page/element/fragment/block counts when available
AstraVector state
searchable
sync evidence
lastVerifiedAt/freshness
TTL state
```

The read model SHALL never claim fresher downstream state than its `lastVerifiedAt` evidence.

---

## 35. Inventory inconsistency tests

Detect at least:

```text
local COMPLETED + downstream searchable=false
local PROCESSING + downstream ACTIVE
stale Inventory record
AstraVector unavailable during refresh
unknown effective TTL
expired/deleted downstream document
catalog knowledgeType/accessZoneCode mismatch in local data
```

---

## 36. Telemetry tests

Verify:

- correlation IDs propagate;
- job/attempt/session identifiers are available in structured logs/traces;
- canonical processing-stage values match TZ-02;
- Prometheus labels are bounded-cardinality;
- raw document/OCR/embedding text is absent from ordinary telemetry;
- signed URLs/credentials are absent;
- error classifications/stages are observable;
- smoke runs are separately identifiable from business jobs.

---

# Part VIII — Model supply/configuration proof

## 37. Model supply

Required TZ-15 scenarios:

```text
valid manifest/checksum -> ready
missing required file -> not ready
checksum mismatch -> not ready
wrong artifactRevision -> not ready
Nexus unavailable + verified local model -> runtime remains ready
Nexus unavailable + required model absent during preload -> preload fails
runtime download attempt -> test failure
silent model/device fallback -> test failure
CPU bundle smoke
GPU bundle smoke when profile enabled
rollback to prior immutable revision
```

---

## 38. Configuration verification

Test:

```text
precedence order
required settings
unknown/invalid enum values
lease/heartbeat invariants
workspace/resource limits
AstraVector client thresholds
OCR concurrency bounds
secret redaction
effectiveConfigSha256 stability
catalog version/allowlist loading
```

A worker SHALL NOT become ready with missing mandatory safety bounds.

---

# Part IX — Internal trust-boundary proof

## 39. TZ-16 tests

The internal-service model still requires evidence for its intentional boundaries:

```text
arbitrary HTTP source URL rejected
unapproved storage adapter rejected
source filename cannot escape workspace
Nexus secret absent from worker when preload separation enabled
worker has no direct Qdrant dependency
worker has no direct AstraVector DB dependency
Knowledge Inventory/admin endpoints bind/expose according to internal deployment config
smoke mutation endpoints are not publicly exposed
RECOVERY_E2E is disabled in ordinary production
logs/config dump redact secrets
access zone cannot be inferred/broadened from document content
```

No OAuth/JWT/RBAC tests are required for AstraIndexator 1.0 because those mechanisms are explicitly out of scope in TZ-16.

---

# Part X — Performance and resource verification

## 40. Performance objectives

TZ-17 SHALL measure, not guess:

```text
queue wait
acquisition throughput
parse duration
OCR duration/pages per second
normalization duration
split duration
prepared artifact write duration
AstraVector ingestion duration
searchability latency
end-to-end latency
```

Results SHALL be segmented by representative fixture classes rather than one synthetic average.

---

## 41. Bounded-resource proof

Large-document tests SHALL demonstrate configured upper bounds for:

```text
process memory
workspace disk
active jobs
OCR pages in flight
AstraVector RPCs in flight
prepared-part size
session batch size
```

The expected failure mode under overload is backpressure/queueing or explicit resource rejection, not uncontrolled OOM/crash loops.

---

## 42. Load/concurrency profiles

At minimum test:

```text
many small documents
few large documents
mixed small/large workload
OCR-heavy workload
AstraVector slowdown
SeaweedFS slowdown
PostgreSQL contention
```

Scale targets are deployment inputs from TZ-18; TZ-17 defines how they are proved.

---

# Part XI — RAG quality verification

## 43. RAG golden corpus

The corpus SHALL include representative:

```text
Russian prose
Kazakh prose
English technical text
mixed RU/KK/EN
legal clauses and numbers
tables
lists
multi-column/bilingual layout
OCR scans
technical identifiers/code
```

Each case defines one or more questions plus expected document/section evidence.

Where Access Zone is part of expected retrieval scope, corpus fixtures SHOULD include cross-zone negative assertions to prove that a query over one zone does not accidentally retrieve another catalog zone.

---

## 44. Retrieval metrics

Useful baseline metrics:

```text
target-document hit rate
Recall@K
MRR
nDCG where graded relevance exists
citation correctness
exact identifier retrieval
no-answer correctness where supported
duplicate-context rate
cross-zone leakage rate (must be zero for isolated fixture scopes)
```

A profile change to parser/OCR/normalizer/splitter SHALL be evaluated against the previous approved processing fingerprint when it can materially affect retrieval quality.

---

## 45. No-regression rule

A new processing profile SHOULD NOT be promoted merely because text appears cleaner.

Promotion requires evidence that critical multilingual/legal/technical retrieval does not regress beyond approved thresholds.

---

# Part XII — Evidence artifacts

## 46. Verification report

Release verification SHOULD produce a machine-readable and human-readable report containing:

```text
application version/commit
canonical schema version
effectiveConfigSha256
catalog version
parser profile/version
OCR model IDs/revisions
normalizer profile/version
splitter profile/version
AstraVector contract/model/tokenizer versions
suite results
failed scenarios
performance summary
RAG metrics
smoke result IDs
```

Evidence MUST NOT contain secrets or unrestricted document content.

---

## 47. Test fixture versioning

Fixtures are immutable/versioned artifacts.

Changing expected evidence without changing fixture/test-contract revision is not allowed merely to make a failing test green.

Golden corpus changes require review because they redefine system behavior/quality proof.

---

## 48. Acceptance criteria

TZ-17 is satisfied when:

- **AC-01:** all TZ-01..TZ-16 critical invariants map to executable scenarios;
- **AC-02:** PostgreSQL coordinator behavior is proven with real concurrent replicas;
- **AC-03:** stale-worker fencing is proven under race/reclaim;
- **AC-04:** crash recovery resumes from durable safe checkpoints;
- **AC-05:** lost-ACK Start/Append/Finalize behavior is proven without duplicate side effects;
- **AC-06:** hostile-file/resource-boundary tests cover TZ-04;
- **AC-07:** parser golden corpus proves structural/reading-order behavior;
- **AC-08:** OCR corpus proves ru/kk/en and mixed/native/scanned handling;
- **AC-09:** normalization preserves multilingual/legal/technical protected forms;
- **AC-10:** logical fragmentation determinism is proven;
- **AC-11:** real AstraVector integration reaches `searchable=true`;
- **AC-12:** retrieval E2E proves expected synthetic/business-test evidence and citations;
- **AC-13:** access-zone ingestion semantics and the ten canonical `0000–0999` root catalog entries are contract-tested;
- **AC-14:** TTL/session-expiry distinction is proven;
- **AC-15:** Knowledge Inventory accuracy/freshness is tested;
- **AC-16:** model supply works offline with checksum/revision enforcement;
- **AC-17:** no silent runtime model/device fallback exists;
- **AC-18:** secrets/raw document text are absent from ordinary telemetry;
- **AC-19:** bounded-resource behavior is measured under large/mixed workloads;
- **AC-20:** RAG quality regression suite exists for ru/kk/en and structured/OCR documents;
- **AC-21:** internal Smoke Test API exists or an equivalent internal control surface provides the same proof;
- **AC-22:** E2E smoke runs through the normal durable production job path rather than invoking parser/indexer internals directly;
- **AC-23:** smoke fixture/source/access zone are allowlisted/configured, not arbitrary caller-controlled values;
- **AC-24:** E2E smoke proves `searchable=true` and retrieval marker/citation when retrieval is available;
- **AC-25:** smoke cleanup uses normal AstraVector lifecycle APIs and is observable;
- **AC-26:** P0 session hashing parity is closed with shared Rust/Python golden vectors before strict session production readiness;
- **AC-27:** positive numeric document-version semantics and current wire-width guards are proven; opaque source revisions remain metadata only;
- **AC-28:** release verification produces durable evidence tied to code/config/model/catalog versions;
- **AC-29:** canonical indexing processing stages are consistent with TZ-02/TZ-12;
- **AC-30:** catalog approval and AstraVector Registry ACTIVE validation are both enforced without local TTL-policy duplication.

---

## 49. Final verification invariant

AstraIndexator 1.0 is not considered verified because:

```text
"the worker started"
```

or:

```text
"unit tests pass"
```

The minimum production proof is:

```text
known immutable fixture
  -> normal durable job with numeric documentVersion + approved accessZoneCode
  -> multi-stage AstraIndexator processing
  -> LogicalBlock[] AstraVector ingestion
  -> searchable=true
  -> RetrieveContext returns expected evidence/citation in the expected zone
  -> Inventory reflects reality
  -> normal lifecycle cleanup succeeds or is reconcilably pending
```

and the failure/recovery suites must prove that the same architecture remains safe when workers, storage, PostgreSQL or downstream acknowledgements fail.
