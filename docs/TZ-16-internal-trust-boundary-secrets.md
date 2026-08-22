# TZ-16 — Internal Trust Boundary & Secrets

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-16
- **Title:** Internal Trust Boundary & Secrets
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01, TZ-02, TZ-03, TZ-04, TZ-10, TZ-11, TZ-14, TZ-15, TZ-17, TZ-18
- **Deployment assumption:** AstraIndexator is an internal service inside a trusted platform network and is not exposed directly to untrusted public clients.

---

## 2. Purpose

TZ-16 defines the minimum trust-boundary and secret-handling requirements for AstraIndexator 1.0.

This is intentionally **not** a full application-security architecture. AstraIndexator is an internal processing service and SHALL NOT implement end-user authentication/authorization mechanisms such as OAuth2/OIDC/JWT/RBAC unless the deployment model changes and a separate architecture decision explicitly introduces them.

The objective is to prevent accidental privilege broadening, secret leakage, unsafe source acquisition and hidden cross-service coupling while preserving a simple internal-service architecture.

---

## 3. Explicit non-goals

The following are out of scope for AstraIndexator 1.0:

```text
OAuth2
OIDC
JWT validation
browser sessions
public API authentication
end-user RBAC
fine-grained application roles
public CORS policy
interactive login
Keycloak integration
browser-facing CSRF controls
```

These controls belong at upstream platform/gateway boundaries if ever required.

---

## 4. Core trust model

AstraIndexator assumes the following components are internal trusted platform services:

```text
Spring Boot producer/service
PostgreSQL coordinator database
SeaweedFS source/artifact storage
AstraVector
Nexus model registry/preload path
internal operator/monitoring plane
```

"Trusted internal" does not mean "no controls". It means:

- caller identity is established outside AstraIndexator when needed;
- network reachability is bounded by infrastructure;
- service credentials are provisioned securely;
- AstraIndexator does not duplicate upstream authentication stacks.

---

## 5. Trust boundaries

Canonical boundaries:

```text
Spring Boot
  -> PostgreSQL job rows / source references

AstraIndexator worker
  -> PostgreSQL
  -> SeaweedFS
  -> AstraVector

Model preload / init container
  -> Nexus

Operator/support tooling
  -> AstraIndexator internal Knowledge Inventory/admin read APIs
```

AstraIndexator MUST NOT assume arbitrary external Internet resources are trusted inputs.

---

## 6. No public ingestion endpoint requirement

The baseline architecture uses PostgreSQL as the durable work queue and SeaweedFS as source storage.

Therefore AstraIndexator does not require a public document-upload REST API for normal indexing.

If an internal maintenance/admin HTTP endpoint exists, it SHALL be bound/exposed only according to TZ-18 internal-network policy and MUST NOT become an alternative unaudited ingestion path that bypasses TZ-01/TZ-02 contracts.

---

## 7. Source acquisition restrictions

AstraIndexator SHALL acquire source documents only through approved storage adapters, initially SeaweedFS.

Baseline rule:

```text
allowed: approved internal SeaweedFS object reference
not baseline: arbitrary http:// or https:// URL supplied by a job
```

This prevents an internal job field from turning AstraIndexator into a generic network fetcher/SSRF proxy.

If future requirements introduce remote URL acquisition, that capability requires a separate allowlist/egress/redirect/DNS-resolution security contract.

---

## 8. File and path safety

TZ-04 remains normative for hostile/corrupt files.

Security-sensitive invariants include:

- original file name is metadata only;
- producer file name never controls trusted local paths;
- archive/container path traversal is rejected;
- executable/active document content is not executed;
- decompression/resource bombs are bounded;
- partial downloads never become parser input;
- local workspaces are attempt-scoped and use generated paths.

TZ-16 does not duplicate these algorithms.

---

## 9. Access-zone handling

AstraIndexator is not the authorization engine for indexed knowledge.

TZ-10 is normative:

- access-zone assignment comes from the trusted platform/business boundary;
- AstraIndexator validates and propagates the requested/effective selector;
- AstraIndexator MUST NOT broaden access scope;
- AstraIndexator MUST NOT infer access-zone assignment from document text;
- code/ID mismatch fails closed;
- unknown/disabled/deleted zones are not silently substituted;
- AstraVector Access Zone Registry remains authoritative.

One indexing job normalizes to one effective ingestion zone.

---

## 10. Secrets model

Secrets SHALL remain separate from ordinary application configuration, model manifests, prepared artifacts and source metadata.

Typical secrets include:

```text
PostgreSQL password / client credential
SeaweedFS credential
Nexus credential
AstraVector mTLS/private key if deployed
internal admin API credential if infrastructure requires one
```

Secrets MUST NOT be committed to Git.

Secrets MUST NOT be embedded in:

```text
application YAML committed to repository
models.yaml
model_manifest.json
prepared manifest.json
LogicalBlock metadata
source links
Knowledge Inventory records
structured logs
metrics labels
trace attributes
error messages returned to ordinary callers
```

---

## 11. Secret delivery

Deployment-specific delivery belongs to TZ-18, but AstraIndexator SHALL consume secrets from external runtime secret sources rather than source-controlled files.

Acceptable examples include:

- Kubernetes Secret mounted as file/environment;
- external secret operator;
- VM/service credential file with restricted permissions;
- platform secret manager.

The exact secret manager is not mandated by AstraIndexator 1.0.

---

## 12. Least privilege

Service credentials SHOULD be scoped to the minimal capabilities required.

Recommended separation:

```text
runtime worker
  -> PostgreSQL job/coordinator schema permissions
  -> SeaweedFS required source/prepared operations
  -> AstraVector client permissions
  -> NO Nexus credential where models are preloaded

model preload/init component
  -> Nexus read credential
  -> target model volume write
  -> NO document-processing DB privileges
```

This separation is preferred over giving every worker a broad shared credential set.

---

## 13. Nexus credential boundary

TZ-15 is normative.

Preferred flow:

```text
init/preload component
  -> authenticated Nexus read
  -> checksum/manifest verification
  -> local immutable model directory
  -> runtime mounts model directory read-only
```

Runtime document-processing workers SHOULD NOT hold Nexus credentials when preloading is used.

A Nexus outage does not affect already-ready workers whose required model revision is verified locally.

---

## 14. AstraVector boundary

AstraIndexator SHALL interact with AstraVector only through supported service contracts, primarily `AstraVectorIngestionFacade` and supported status/admin APIs defined by TZ-11/TZ-14.

AstraIndexator MUST NOT:

- connect directly to AstraVector PostgreSQL for lifecycle/search state;
- modify Qdrant directly;
- bypass AstraVector Access Zone Registry;
- manufacture vector/search readiness state locally.

This rule is both an architecture and trust-boundary requirement.

---

## 15. Database boundary

AstraIndexator PostgreSQL credentials SHOULD be restricted to its own required schemas/tables/functions.

The service SHOULD NOT require database superuser or cluster-administrator privileges.

Schema migration credentials MAY be separated from runtime worker credentials when operationally practical.

Runtime worker permission should be sufficient for:

- claim/lease/fencing operations;
- job/attempt/checkpoint updates;
- Knowledge Inventory projection updates;
- read-only status queries needed by the service.

---

## 16. Object-storage boundary

SeaweedFS credentials SHOULD provide only required object operations for configured source/prepared namespaces.

AstraIndexator SHOULD NOT require arbitrary access to unrelated object namespaces.

Source/prepared object references appearing in logs or Inventory MUST NOT expose embedded credentials or long-lived privileged signed URLs.

---

## 17. Knowledge Inventory / admin endpoints

TZ-14 defines internal operator read APIs such as:

```text
GET /internal/v1/knowledge
GET /internal/v1/knowledge/{documentId}
GET /internal/v1/jobs/{jobId}
```

Baseline rules:

1. these endpoints are internal-only;
2. they MUST NOT be exposed directly to the public Internet;
3. infrastructure/network controls are sufficient for MVP when operating in the trusted internal service network;
4. application-level OAuth/JWT/RBAC is not required by AstraIndexator 1.0;
5. returned data MUST exclude raw document text, secrets and privileged signed URLs;
6. any future public/operator portal exposure requires a gateway/authentication decision outside this baseline.

---

## 18. Network exposure

AstraIndexator SHOULD only require network paths to:

```text
PostgreSQL
SeaweedFS
AstraVector
observability backends where configured
Nexus only for preload/init components
```

General outbound Internet access is not required for production processing.

NetworkPolicy/firewall/service-mesh restrictions are infrastructure recommendations handled in TZ-18, not application-level requirements for MVP.

---

## 19. TLS and mTLS

AstraIndexator 1.0 does not mandate application-managed mTLS when the deployment environment already provides trusted internal transport/network isolation.

However transports SHOULD support TLS/mTLS configuration where required by platform policy.

This is an infrastructure capability, not a reason to add user authentication logic inside AstraIndexator.

No component may silently disable required certificate verification when TLS is configured.

---

## 20. Logging and telemetry

TZ-14 is normative.

Normal telemetry MUST NOT contain:

- database passwords;
- SeaweedFS/Nexus credentials;
- authorization headers;
- private keys/certificates;
- signed URLs with credentials;
- raw source document text;
- OCR/embedding text dumps;
- full sensitive metadata payloads.

Allowed operational identifiers include bounded non-secret fields such as:

```text
jobId
documentId
documentVersion
processingAttemptId
accessZoneCode
workerId
processingStage
modelId
artifactRevision
content hash when policy permits
```

High-cardinality fields belong in logs/read models, not Prometheus labels.

---

## 21. Error handling

Dependency errors SHALL avoid returning secret-bearing raw exception messages.

For example, database/Nexus/SeaweedFS errors should be normalized into stable operational codes while detailed low-level stack traces remain controlled internal diagnostics.

Credentials contained in library-generated URLs/connection strings MUST be redacted before logging.

---

## 22. Temporary files

Local attempt workspaces SHOULD:

- use generated directories;
- have restrictive filesystem permissions appropriate to the container/host;
- be deleted best-effort after completion/failure;
- never be shared as cross-pod durable state;
- avoid predictable external-user-controlled paths.

Sensitive local files are not persisted to logs or ordinary diagnostics.

---

## 23. Model artifacts are executable-adjacent trusted inputs

Although model files are not application secrets, they influence code execution/inference behavior and SHALL therefore be integrity-verified according to TZ-15.

Runtime workers SHALL only load approved manifest/checksum-verified model revisions.

A model checksum mismatch is a readiness/integrity failure, not a warning to ignore.

---

## 24. Dependency/library vulnerabilities

Routine dependency scanning and container/image vulnerability management are recommended operational practices, but AstraIndexator 1.0 does not define a dedicated application vulnerability-scanner subsystem.

CI/deployment verification belongs to TZ-17/TZ-18.

---

## 25. Security configuration failure behavior

Configuration failures involving required credentials or trust boundaries SHALL fail explicitly.

Examples:

```text
missing PostgreSQL credential -> not ready
invalid SeaweedFS credential -> not ready / dependency failure
required AstraVector TLS verification disabled -> startup validation failure
missing required local model bundle -> not ready
arbitrary external source URL in baseline -> reject
```

The service SHALL NOT silently fall back to insecure anonymous/public behavior.

---

## 26. Minimal audit events

Security-relevant but operationally useful audit events SHOULD include:

```text
SOURCE_REFERENCE_REJECTED
ACCESS_ZONE_VALIDATION_FAILED
SECRET_CONFIGURATION_INVALID
MODEL_INTEGRITY_FAILED
INTERNAL_ADMIN_STATUS_REFRESH
MANUAL_RECOVERY_ACTION
```

Audit events SHALL contain identifiers/reason codes, not secrets or raw document contents.

---

## 27. Verification requirements

TZ-17 SHALL include at least the following evidence:

1. no OAuth/JWT dependency is required for normal worker startup;
2. arbitrary external URL source is rejected in baseline mode;
3. producer file name cannot escape local workspace;
4. ZIP traversal/decompression defenses remain effective through TZ-04 tests;
5. access-zone mismatch fails closed;
6. worker does not broaden access zone;
7. runtime secrets are absent from Git/manifests/logs;
8. Nexus credential is not required by runtime worker in preload profile;
9. runtime worker can operate while Nexus is unavailable when models are already verified locally;
10. runtime worker has no direct Qdrant integration path;
11. runtime worker does not read AstraVector PostgreSQL directly;
12. Knowledge Inventory does not expose raw document text or secrets;
13. TLS certificate verification cannot be silently disabled when configured as required;
14. malformed credential-bearing dependency exceptions are redacted;
15. missing required credential produces readiness/startup failure rather than anonymous fallback.

---

## 28. Acceptance criteria

TZ-16 is satisfied when:

- **AC-01:** AstraIndexator is explicitly classified as an internal trusted service;
- **AC-02:** end-user OAuth/OIDC/JWT/RBAC are documented as out of scope;
- **AC-03:** normal indexing does not require a public upload API;
- **AC-04:** source acquisition is restricted to approved storage adapters in baseline mode;
- **AC-05:** arbitrary external URL fetching is not available by default;
- **AC-06:** service secrets are externalized and excluded from Git/manifests/telemetry;
- **AC-07:** runtime and preload credentials can be separated;
- **AC-08:** AstraIndexator does not access Qdrant directly;
- **AC-09:** AstraIndexator does not read AstraVector PostgreSQL directly for lifecycle/search state;
- **AC-10:** access-zone scope is propagated without broadening or inference from content;
- **AC-11:** Knowledge Inventory/admin endpoints are internal-only in baseline deployment;
- **AC-12:** logs/errors redact credentials and raw document content;
- **AC-13:** model integrity verification remains mandatory;
- **AC-14:** required credential/trust failures fail explicitly rather than falling back insecurely;
- **AC-15:** TZ-17 contains executable verification for the minimal trust boundary.

---

## 29. Final invariant

AstraIndexator 1.0 is intentionally simple from an application-security perspective:

```text
trusted internal producer/platform
        ↓
PostgreSQL + SeaweedFS
        ↓
AstraIndexator internal worker
        ↓
AstraVector
```

The service does **not** become an authentication platform.

Its security baseline is limited to:

```text
controlled internal reachability
+
externalized least-privilege secrets
+
approved source/storage boundaries
+
no privilege/access-zone broadening
+
model integrity
+
no direct downstream database/Qdrant bypass
+
no secret/document-content leakage
```

If AstraIndexator is later exposed to untrusted/public clients, this specification MUST be revised before that deployment model is allowed.
