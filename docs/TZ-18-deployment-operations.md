# TZ-18 — Deployment & Operations

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-18
- **Title:** Deployment & Operations
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-01 through TZ-17
- **Primary goal:** define a reproducible, horizontally scalable and recoverable production deployment model for AstraIndexator without changing the contracts established by TZ-00..TZ-17.

---

## 2. Purpose

TZ-18 converts the logical architecture into an operable runtime topology.

The deployment model SHALL preserve these invariants:

```text
PostgreSQL = durable job/coordinator authority
SeaweedFS  = immutable source + prepared artifact store
AstraIndexator replicas = stateless/recoverable processing workers
AstraVector = vector/search lifecycle authority
Nexus = model artifact supply, not document-time dependency
local workspace/model cache = replaceable runtime state
```

Operations SHALL assume process, pod and node failure are normal events. Correctness must come from leases/fencing, durable checkpoints, immutable artifacts and downstream reconciliation rather than from pod affinity or a permanently surviving worker.

---

## 3. Supported deployment profiles

### 3.1 Portable / standalone

For development, integration environments and small internal installations:

```text
Spring Boot producer
      |
PostgreSQL + SeaweedFS
      |
AstraIndexator process/container
      |
AstraVector
```

The same application configuration schema and persistence contracts used in Kubernetes SHALL be used here. Standalone mode MUST NOT introduce a different queue, local-only document identity or alternate ingestion API.

### 3.2 Kubernetes production baseline

```text
                       +----------------------+
                       | Spring Boot producer |
                       +----------+-----------+
                                  |
                     source + durable job
                                  |
                +-----------------+------------------+
                |                                    |
         +------v------+                      +------v------+
         |  SeaweedFS  |                      | PostgreSQL  |
         +------+------+                      +------+------+ 
                |                                    |
                +----------------+-------------------+
                                 |
                    +------------v-------------+
                    | AstraIndexator replicas  |
                    | CPU worker pool          |
                    | N >= 1                   |
                    +------------+-------------+
                                 |
                  optional OCR routing/profile
                                 |
                    +------------v-------------+
                    | GPU OCR worker pool      |
                    | optional                 |
                    +------------+-------------+
                                 |
                    +------------v-------------+
                    | AstraVector facade       |
                    +------------+-------------+
                                 |
                        AstraVector-owned
                     PostgreSQL / Qdrant state

Nexus -> model preload/init -> verified read-only model volume/cache

Prometheus / logs / traces <- AstraIndexator observability
Operator/internal tooling -> Knowledge Inventory + Smoke Test API
```

A deployment MAY combine control/API and CPU processing in one application artifact. Operationally, worker concurrency and optional GPU capability SHALL remain separately configurable.

---

## 4. Runtime roles

AstraIndexator SHOULD support explicit runtime roles from the same versioned application artifact:

```text
CONTROL
WORKER_CPU
WORKER_GPU
ALL
```

### CONTROL

Owns lightweight internal operational surfaces where enabled:

```text
health/readiness
Knowledge Inventory
Smoke Test API
operator status/recovery endpoints
metrics
```

It does not need to process documents.

### WORKER_CPU

Claims normal jobs and executes the portable processing path. CPU OCR is the baseline OCR capability when OCR is enabled for the profile.

### WORKER_GPU

Optional worker pool with verified GPU/CUDA/model capability. It MUST NOT silently degrade to a different device/model profile.

### ALL

Permitted for standalone/dev/small installations. Production Kubernetes MAY also use it initially, but separate roles are recommended when control traffic, CPU parsing and GPU OCR require different scaling/resource policies.

---

## 5. Container image contract

The production image SHALL:

- use a pinned Python/runtime base rather than floating `latest`;
- run as a non-root user where platform policy permits;
- contain application code and deterministic Python dependencies;
- NOT require mutable source checkout at runtime;
- NOT contain production credentials;
- NOT require document-time Internet/model downloads;
- expose build/version metadata;
- have a writable bounded workspace separate from application/model paths;
- treat model directories as read-only after provisioning.

Recommended build metadata:

```text
applicationVersion
gitCommit
buildTimestamp
imageDigest
configSchemaVersion
canonicalSchemaVersion
```

Image digest, not mutable tag alone, is the deployable identity used for audit/rollback.

---

## 6. Model provisioning

TZ-15 remains authoritative for model identity and verification.

Kubernetes baseline:

```text
Nexus
  -> init container / explicit preload job
  -> download approved immutable revision
  -> verify manifest + required files + SHA-256
  -> publish verified local directory
  -> runtime container mounts it read-only
```

Target internal registry:

```text
https://nexus.astrabase.asia
```

The runtime worker SHOULD NOT require Nexus credentials when an init/preload component can provision the model.

Nexus outage semantics:

```text
verified required revision already available -> existing worker may remain ready
required revision absent during new pod provisioning -> pod MUST NOT become OCR-ready
```

Runtime SHALL NOT switch to another revision because the configured revision cannot be provisioned.

---

## 7. Persistent and ephemeral storage

### Durable external state

```text
PostgreSQL:
  jobs
  attempts
  leases/fencing
  checkpoints
  inventory/audit projections

SeaweedFS:
  immutable source
  prepared manifests/parts

AstraVector:
  downstream canonical vector lifecycle/search state
```

### Ephemeral state

```text
local source download
rendered pages
OCR scratch
parser scratch
temporary publication files
in-process caches
```

A pod/node loss MAY destroy all ephemeral state without violating correctness.

### Workspace

Each attempt SHALL use an isolated workspace such as:

```text
/work/<jobId>/<attemptId>/
```

Workspace capacity MUST be bounded. Cleanup occurs on normal completion and best-effort startup/periodic orphan sweeping. Workspace cleanup MUST NOT delete SeaweedFS prepared/source artifacts.

For Kubernetes, `emptyDir` or equivalent ephemeral storage is appropriate for processing scratch. `emptyDir.sizeLimit` and pod ephemeral-storage requests/limits SHOULD be configured.

---

## 8. PostgreSQL schema migrations

Database schema changes SHALL be version-controlled and executed exactly once per deployment environment through a migration mechanism independent of ordinary worker startup concurrency.

Preferred pattern:

```text
release
  -> migration Job / controlled migration step
  -> migration success
  -> application rollout
```

Every replica MUST NOT race to perform unrestricted schema migrations on startup.

Migration requirements:

- backward-compatible expand/contract changes for rolling upgrades where practical;
- no destructive migration before old replicas are drained;
- migration history persisted;
- backup/restore procedure tested for destructive changes;
- schema compatibility checked at startup/readiness.

---

## 9. Startup, liveness and readiness

Health endpoints SHALL distinguish process health from production capability.

Recommended internal surfaces:

```text
GET /internal/health/live
GET /internal/health/ready
GET /internal/health/capabilities
```

### Liveness

Liveness answers only whether the process/event loop is alive enough to continue or be restarted.

It MUST NOT synchronously execute:

```text
full PostgreSQL query chain
SeaweedFS upload/download
AstraVector ingestion
OCR inference
Smoke E2E
```

A temporary dependency outage MUST NOT create a restart storm through liveness.

### Readiness

Readiness is role-aware.

A CPU worker is ready to claim new work only when mandatory configuration, workspace, PostgreSQL coordination and required processing capabilities are valid.

An OCR-capable worker additionally requires its pinned local model bundle to pass manifest/checksum/runtime validation.

A CONTROL instance may remain ready for diagnostics even when worker capability is degraded, provided the endpoint reports dependency/capability state honestly.

### Capability status

Capabilities SHOULD expose non-secret state such as:

```text
role
applicationVersion
effectiveConfigSha256
postgresql: READY/DEGRADED
seaweedfs: READY/DEGRADED
astravector: READY/DEGRADED
ocrCpu: READY/DISABLED/FAILED
ocrGpu: READY/DISABLED/FAILED
modelId
artifactRevision
```

---

## 10. Dependency failure semantics

Dependency health and worker admission are separate from pod survival.

### PostgreSQL unavailable

Worker cannot safely claim/renew/finalize jobs. It SHALL stop claiming new work and follow TZ-02/TZ-13 lease semantics. Liveness may remain healthy while readiness for processing becomes false/degraded.

### SeaweedFS unavailable

New acquisition/publication cannot proceed. Existing durable state remains authoritative. Retry/recovery follows TZ-03/TZ-13.

### AstraVector unavailable

Parsing/prepared publication MAY complete if policy permits, but downstream delivery remains retryable/reconcilable. A document MUST NOT be marked COMPLETED until downstream searchability contract is satisfied.

### Nexus unavailable

Already verified local models continue to work. Nexus is not checked for every document.

### Qdrant unavailable

AstraIndexator does not diagnose or repair Qdrant directly. AstraVector status/reconciliation remains authoritative.

---

## 11. Worker concurrency and backpressure

Horizontal replicas do not imply unlimited local concurrency.

Each worker SHALL have explicit limits for:

```text
maxConcurrentJobs
maxConcurrentDownloads
maxConcurrentParses
maxConcurrentOcrPages
maxConcurrentAstraVectorSessions
workspaceBytes
memory soft/hard limits
```

The scheduler/worker SHALL apply backpressure before claiming work that cannot be safely processed with available local capacity.

Large documents MUST NOT cause unbounded page/image accumulation in memory.

---

## 12. Resource classes

No universal production CPU/RAM values are mandated before TZ-17 performance evidence exists. Deployment SHALL use measured profiles.

Initial logical classes:

### CONTROL

Low CPU/memory; network/DB-bound.

### CPU worker

CPU/memory/ephemeral-storage intensive; parsing and CPU OCR determine sizing.

### GPU worker

Explicit GPU resource, CPU/RAM for rendering/preprocessing, bounded GPU memory/concurrency.

Kubernetes manifests SHALL declare requests and limits rather than relying on node spare capacity.

Performance tests SHALL produce recommended values for at least:

```text
small document
medium document
large document
OCR-heavy document
mixed concurrent workload
```

---

## 13. Horizontal scaling

The canonical scale unit is a worker replica.

```text
Indexator-1 --+
Indexator-2 ---+--> PostgreSQL coordinator
Indexator-3 --+
```

Correctness is independent of replica count because TZ-02 uses atomic claim/lease/fencing.

### Scaling signals

CPU alone is insufficient. Preferred signals include:

```text
runnable job backlog
oldest runnable job age
active jobs / worker capacity
processing latency
OCR backlog
AstraVector delivery backlog
CPU/memory/GPU saturation
```

HPA/KEDA-style autoscaling MAY use these metrics when available.

### Scale-up

New replicas validate config/models/capabilities before becoming ready and claiming jobs.

### Scale-down

A replica selected for termination SHALL stop claiming new jobs first, then drain current work within the termination grace period.

---

## 14. Graceful shutdown and lease safety

On SIGTERM/termination:

```text
1. mark worker draining/not-ready
2. stop new claims
3. continue safe in-flight stage where possible
4. persist/checkpoint durable progress
5. finalize current fenced mutation if safely possible
6. stop heartbeat only when ownership is intentionally relinquished/terminated
7. exit before grace period expires
```

If processing cannot finish before termination, correctness relies on lease expiry/reclaim and durable checkpoints. A shutdown handler MUST NOT mark unfinished work COMPLETED merely to exit cleanly.

`terminationGracePeriodSeconds` SHALL exceed the normal time needed to checkpoint/finish the longest non-interruptible bounded operation, but operations themselves MUST have explicit timeouts.

---

## 15. Rolling deployment

Recommended rollout:

```text
CI verification
 -> image build
 -> immutable image digest
 -> model/config compatibility validation
 -> DB migration if required
 -> canary replica/pool
 -> readiness
 -> DEPENDENCIES/PIPELINE smoke as appropriate
 -> E2E_RETRIEVAL smoke in controlled namespace
 -> progressive rollout
 -> post-deploy observation
```

During rolling deployment, old and new replicas may coexist only when their database schema, prepared-artifact schema and AstraVector contracts are compatible.

A processing attempt pins its relevant processing fingerprint/model/profile. A worker upgrade MUST NOT silently continue an incompatible attempt using different processing semantics.

---

## 16. Smoke-after-deploy

TZ-17 Smoke Test API is the deployment verification mechanism.

Recommended sequence:

```text
readiness
  -> DEPENDENCIES smoke
  -> PIPELINE smoke
  -> E2E_RETRIEVAL smoke
  -> optional OCR_E2E for OCR-capable release
  -> cleanup
```

Smoke uses reserved fixtures/document namespace/access zone and normal production paths.

Kubernetes liveness/readiness MUST NOT invoke mutating smoke profiles.

Release policy SHOULD define which smoke profiles are blocking for each environment.

---

## 17. Rollback

Rollback identity consists of more than application code:

```text
image digest
application version
DB schema compatibility
config revision
model artifact revision
parser/normalizer/splitter profiles
AstraVector contract compatibility
```

Rollback SHALL select known immutable application/model revisions.

A rollback MUST NOT:

- overwrite an existing immutable model revision;
- directly mutate Qdrant;
- erase job/attempt history;
- assume jobs owned by terminated replicas were lost;
- downgrade across an incompatible destructive DB migration without an explicit recovery procedure.

After rollback, smoke and reconciliation SHALL run again.

---

## 18. Configuration deployment

Configuration follows TZ-15 deterministic precedence.

Kubernetes mapping SHOULD use:

```text
ConfigMap / mounted config -> non-secret configuration
Secret / external secret provider -> credentials
immutable model volume/cache -> model artifacts
```

Configuration changes SHALL be auditable and preferably versioned.

Processing-affecting configuration changes participate in the processing fingerprint. Purely operational changes such as log level do not require reindexing.

Runtime config reload is NOT required for 1.0 unless a setting is explicitly documented as reloadable. Restart/rolling rollout is the safe baseline.

---

## 19. Internal network topology

AstraIndexator is an internal service per TZ-16.

Required network paths are limited to the configured deployment role, typically:

```text
worker -> PostgreSQL
worker -> SeaweedFS
worker -> AstraVector facade
preload/init -> Nexus
control -> PostgreSQL
control -> AstraVector status APIs where needed
monitoring -> metrics/health
operator/internal gateway -> control API
```

AstraIndexator SHALL NOT require direct Qdrant or AstraVector PostgreSQL access.

NetworkPolicy/firewall rules SHOULD reflect this dependency graph where the platform uses them.

---

## 20. Observability operations

TZ-14 is the telemetry contract. Deployment SHALL make available:

```text
structured logs
Prometheus-compatible metrics
trace export when enabled
health/capability endpoints
Knowledge Inventory
job/attempt/audit views
```

Operational dashboards SHOULD cover:

```text
queue depth / oldest job
workers ready/draining
claim/lease/reclaim rate
stage latency and failures
OCR throughput/failures
prepared artifact publication
AstraVector delivery/reconciliation
searchable knowledge count
TTL/expiry buckets
stale Knowledge Inventory
smoke results
resource saturation
```

Alerts SHOULD be symptom-oriented rather than based on pod restarts alone.

---

## 21. Operational SLO indicators

Exact SLO targets SHALL be chosen from measured business/production needs, but the system SHALL expose enough evidence for at least:

```text
job success rate
queue wait time
end-to-end indexing latency
searchability latency
recovery/reclaim rate
DLQ growth
AstraVector reconciliation lag
smoke success rate
Knowledge Inventory freshness
```

Document size/OCR class SHOULD be considered when defining latency objectives; one global latency SLO for all documents is misleading.

---

## 22. Backup and disaster recovery boundary

AstraIndexator recovery depends on the durability of its authorities.

Minimum backup/restore scope:

```text
PostgreSQL coordinator/job/checkpoint/audit state
SeaweedFS source and required prepared artifacts according to retention policy
```

AstraVector owns backup/recovery of its PostgreSQL/Qdrant domain.

AstraIndexator DR SHALL NOT assume Qdrant is its backup responsibility.

Recovery verification SHALL include restoration into a clean environment followed by reconciliation/smoke evidence.

RPO/RTO values are deployment/business decisions and are not invented by this specification.

---

## 23. Retention and housekeeping

Periodic operational jobs MAY perform:

```text
orphan local workspace cleanup
orphan SeaweedFS staging cleanup
prepared artifact retention cleanup
completed job history retention
old audit retention
Smoke fixture/result cleanup
model cache cleanup for unreferenced revisions
```

Cleanup MUST be ownership-aware and MUST NOT infer that vector TTL expiry automatically authorizes source/prepared deletion unless the relevant retention policy says so.

Housekeeping SHALL be observable and bounded.

---

## 24. Runbooks

Production readiness requires concise runbooks for at least:

1. PostgreSQL unavailable;
2. SeaweedFS unavailable;
3. AstraVector unavailable/degraded;
4. Nexus unavailable during scale-up;
5. OCR model checksum/startup failure;
6. worker crash/reclaim storm;
7. queue backlog growth;
8. repeated poison job / DLQ;
9. prepared artifact corruption;
10. lost/ambiguous AstraVector ACK;
11. Knowledge Inventory stale/unknown TTL;
12. smoke failure after deploy;
13. disk/ephemeral-storage pressure;
14. memory/OOM pressure;
15. GPU unavailable/driver mismatch;
16. rollback;
17. DB migration failure;
18. disaster restore/reconciliation.

Each runbook SHOULD contain detection, immediate containment, diagnosis, safe recovery, verification and escalation criteria.

---

## 25. Release evidence

A production candidate SHALL have an evidence bundle containing at minimum:

```text
application/image identity
config schema/effective non-secret fingerprint
model revision/checksum evidence
DB migration result
TZ-17 required suite results
multi-replica/recovery evidence
RAG-quality gate result when processing semantics changed
smoke result
known limitations/P0 blockers
rollback target
```

A release with unresolved P0 contract gaps identified by TZ-11/TZ-17 MUST NOT be described as production-ready.

---

## 26. Initial Kubernetes object model

A practical baseline is:

```text
Namespace

ConfigMap
Secret / external secret references

Deployment: astra-indexator-control       (optional separate role)
Deployment: astra-indexator-worker-cpu    (N replicas)
Deployment: astra-indexator-worker-gpu    (optional)

Service: astra-indexator-internal         (control/health/metrics)

Job: astra-indexator-db-migrate
Job or initContainer: model-preload

ServiceMonitor/PodMonitor where applicable
HPA/KEDA where justified by metrics
NetworkPolicy where platform policy uses it
PodDisruptionBudget for multi-replica production pools where useful
```

PostgreSQL, SeaweedFS, Nexus, AstraVector and Qdrant are external dependencies from the AstraIndexator deployment boundary and need not be packaged into the same Helm chart.

---

## 27. Helm/configuration layout recommendation

A future chart SHOULD separate concerns:

```text
values.yaml
  image
  role
  replicas
  resources
  autoscaling
  coordinator
  seaweedfs
  astravector
  workspace
  processing
  ocr
  modelSupply
  observability
  smoke
  housekeeping
  networkPolicy
```

Secrets are referenced, not embedded as literal production values.

Environment overlays SHALL not fork application semantics. Differences such as dev/test/prod endpoints, replica counts and resource budgets belong in deployment configuration.

---

## 28. Operational acceptance criteria

TZ-18 is accepted when executable evidence proves at least:

**AC-01** One and multiple worker replicas use the same durable job contract without duplicate authoritative completion.

**AC-02** A pod can be killed during processing and another replica safely reclaims the job through lease/fencing/checkpoints.

**AC-03** Scale-up replicas do not claim work before required config/model capability validation passes.

**AC-04** SIGTERM stops new claims and permits safe drain/checkpoint/reclaim semantics.

**AC-05** Temporary PostgreSQL/SeaweedFS/AstraVector outages do not cause liveness restart storms or false COMPLETED state.

**AC-06** Existing OCR workers can continue with verified local models during Nexus outage.

**AC-07** A new OCR worker with an unavailable required model revision does not become ready.

**AC-08** Runtime model download and silent model/device fallback are absent.

**AC-09** Worker workspace and memory/concurrency are bounded.

**AC-10** Kubernetes resource requests/limits exist for production worker profiles.

**AC-11** Database migration is serialized/controlled rather than raced by all replicas.

**AC-12** Rolling deployment permits only contract/schema-compatible overlap.

**AC-13** Post-deploy smoke proves the configured production path and cleans its synthetic data.

**AC-14** Rollback restores an immutable known application/model combination and passes smoke/reconciliation.

**AC-15** AstraIndexator deployment does not require direct Qdrant or AstraVector PostgreSQL connectivity.

**AC-16** Secrets are externalized and absent from image/config manifests/logs/metrics/traces.

**AC-17** Queue, worker, stage, downstream, knowledge-lifecycle and smoke telemetry are operationally visible.

**AC-18** Backup/restore of AstraIndexator-owned durable state is tested in a clean environment.

**AC-19** Required operational runbooks exist and have at least tabletop/test evidence for critical failures.

**AC-20** Release evidence identifies image/config/model/schema/test/smoke/rollback state unambiguously.

---

## 29. Mandatory TZ-17 deployment verification scenarios

TZ-17 SHALL include or reference executable scenarios for:

```text
single replica startup
three-replica claim race
pod kill during acquisition
pod kill during OCR
pod kill after prepared publication
SIGTERM drain
lease expiry/reclaim
stale worker fencing
PostgreSQL outage/recovery
SeaweedFS outage/recovery
AstraVector outage/recovery
Nexus outage with warm model cache
Nexus outage with cold model cache
bad model checksum
CPU profile startup
GPU profile startup/compatibility where enabled
workspace exhaustion guard
memory/concurrency guard
migration success/failure
rolling version overlap
post-deploy DEPENDENCIES smoke
post-deploy PIPELINE smoke
post-deploy E2E_RETRIEVAL smoke
OCR_E2E smoke when applicable
rollback + smoke
backup restore + reconciliation
housekeeping safety
```

---

## 30. Production readiness gate

AstraIndexator 1.0 is operationally production-ready only when:

```text
TZ-00..TZ-18 baseline contracts are implemented
+
TZ-17 mandatory verification evidence passes
+
P0 AstraVector integration/hash/version gaps are resolved
+
production config/model revisions are pinned
+
DB migrations are controlled
+
required smoke profiles pass
+
rollback target is known
+
critical runbooks exist
```

Documentation completion alone is not production readiness.

---

## 31. Final deployment invariant

The deployment layer SHALL remain replaceable.

Whether AstraIndexator runs as one process, Docker Compose or multiple Kubernetes pools, its correctness model remains:

```text
immutable source
+
durable PostgreSQL coordination
+
lease/fencing
+
replayable prepared artifacts
+
versioned processing/model identity
+
idempotent/reconcilable AstraVector delivery
+
observable knowledge lifecycle
+
executable verification
```

Kubernetes improves scheduling, isolation and scaling; it is not the mechanism that makes indexing correct.
