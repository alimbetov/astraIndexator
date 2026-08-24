# TZ-17F — Production Robustness & Recovery Corpus v6

## Status

- **System:** AstraIndexator 1.0
- **Parent:** TZ-17 Testing & Verification
- **Related:** TZ-02, TZ-03, TZ-05, TZ-06, TZ-07, TZ-08, TZ-09, TZ-11, TZ-13, TZ-14, TZ-15, TZ-17D, TZ-18
- **Status:** Design baseline; implementation intentionally deferred
- **Purpose:** prove that AstraIndexator preserves correctness, recoverability and operational evidence under process, dependency, resource, network and infrastructure failure.

---

## 1. Why v6 exists

Functional correctness is insufficient for production RAG ingestion. AstraIndexator is explicitly an at-least-once, lease/fencing, checkpoint/reconciliation system. Therefore production readiness requires executable evidence that the following remain true while failures occur:

```text
accepted work is never silently lost
stale workers cannot commit authoritative state
repeated execution does not create unsafe duplicates
ambiguous downstream mutation is reconciled before replay
source/prepared artifacts are never trusted after integrity failure
resource exhaustion fails predictably before node-wide damage when possible
CPU/GPU/ONNX runtime differences do not change lifecycle semantics
recovery preserves document identity, access semantics, TTL intent and processing evidence
operator-visible state explains what happened
```

v6 is not a chaos-demo suite. Every experiment has an invariant, a deterministic fault point, an expected final state, and a required observability proof.

---

## 2. Reliability model under test

The suite SHALL validate the already-approved reliability model:

```text
at-least-once execution
+
PostgreSQL lease/fencing ownership
+
deterministic processing identity
+
durable checkpoints/prepared artifacts
+
idempotent or reconcilable downstream effects
+
bounded retry/backoff
+
operator-recoverable terminal failure
```

The suite SHALL NOT claim distributed exactly-once semantics across PostgreSQL, SeaweedFS and AstraVector.

---

## 3. Universal execution matrix

v6 SHALL describe each experiment against one or more execution profiles instead of assuming one machine type.

### 3.1 Required runtime profiles

```text
STANDALONE_CPU
K8S_CPU
K8S_GPU
K8S_ONNX_CPU
K8S_ONNX_GPU
```

GPU/ONNX profiles are capability-gated. A profile is tested only when the target environment declares the required hardware/runtime capability.

### 3.2 Hardware metadata

Every run report SHALL record at least:

```text
os/kernel
container image digest
python/runtime version
cpu architecture
cpu model/vendor where available
worker role
ocr engine/backend
onnx execution provider when used
gpu vendor/model/device id where used
driver/cuda/runtime versions where used
modelId/artifactRevision/bundleSha256
resource requests/limits
workspace limit
```

### 3.3 Portability principle

Correctness expectations are profile-independent. Performance expectations are profile-specific.

Allowed to differ:

```text
latency
throughput
CPU utilization
RAM high-water
GPU utilization
VRAM high-water
engine confidence within documented tolerance
```

Not allowed to differ solely because hardware changed:

```text
document identity
job lifecycle semantics
access zone / TTL intent
source/provenance identity
stale-worker fencing
checkpoint compatibility rules
idempotency/reconciliation identity
searchability completion condition
```

Silent runtime fallback is forbidden. Example:

```text
configured K8S_ONNX_GPU
+ CUDA provider unavailable
-> readiness/capability failure
```

not:

```text
-> silently run CPU and report GPU profile
```

---

## 4. Failure-domain taxonomy

Every experiment belongs to exactly one primary failure domain while optionally exercising secondary effects.

```text
PROCESS
LEASE_OWNERSHIP
POSTGRESQL
OBJECT_STORAGE
LOCAL_WORKSPACE
PARSER_NORMALIZER_SPLITTER
OCR_RUNTIME
GPU_RUNTIME
MODEL_SUPPLY
NETWORK
ASTRAVECTOR_TRANSPORT
ASTRAVECTOR_STATE
RESOURCE_MEMORY
RESOURCE_STORAGE
RESOURCE_CPU
KUBERNETES_LIFECYCLE
NODE
ROLLING_UPGRADE
CANCELLATION
CONCURRENCY
CHECKPOINT_ARTIFACT
OBSERVABILITY
```

Failure domains SHALL be reported separately so a global pass rate cannot hide a broken recovery class.

---

## 5. Canonical experiment contract

Each v6 scenario SHALL have a versioned manifest containing conceptually:

```yaml
id: v6-crash-ocr-001
category: PROCESS
fixtureId: ...
executionProfiles: [K8S_CPU, K8S_ONNX_GPU]
preconditions:
  jobState: PROCESSING
  stage: OCR_PROCESSING
fault:
  type: SIGKILL
  trigger: after_ocr_candidate_accepted_before_checkpoint
  seed: 1234
expected:
  firstAttemptOutcome: ABANDONED_OR_CRASHED
  reclaimRequired: true
  finalJobState: COMPLETED
  maxAuthoritativeOwners: 1
  duplicateSearchableVersions: 0
  requiredEvidence:
    - lease_generation_incremented
    - stale_attempt_cannot_finalize
    - recovery_reason_visible
```

The corpus harness SHALL reject scenarios without explicit pass criteria.

---

## 6. Core correctness invariants

These invariants are hard gates across applicable experiments.

### R-01 — No accepted-job loss

An accepted durable job ends as one of:

```text
COMPLETED
RETRY_WAIT
FAILED
DEAD_LETTER
CANCELLED
```

or remains actively PROCESSING with a valid current lease. It must never disappear because a worker/pod/node died.

### R-02 — One authoritative lease generation

At any instant only the current generation may perform authoritative job mutations.

### R-03 — Stale worker safety

A stale worker may perform local cleanup, but cannot:

```text
renew stale lease
advance durable checkpoint
mark COMPLETED/FAILED/RETRY_WAIT
publish a conflicting prepared artifact
start/append/finalize authoritative downstream mutation after ownership loss is known
```

### R-04 — Identity stability

Retry/reclaim never changes:

```text
documentId
documentVersion
accepted access semantics
TTL intent
source processing intent
```

### R-05 — Ambiguous mutation reconciliation

Lost ACK / connection reset after a mutating downstream call yields UNKNOWN_OUTCOME until reconciled.

### R-06 — Deterministic replay

Replay uses the same deterministic artifact/batch identity when the processing fingerprint is compatible.

### R-07 — Corrupt evidence is never trusted

Checksum/schema/source identity mismatch causes fail-closed regeneration or terminal classification, never silent reuse.

### R-08 — Completion means searchable

Local completion, RPC success or artifact publication alone cannot mark the job COMPLETED. AstraVector searchability proof remains required.

---

## 7. Process and pod failure corpus

Required scenarios:

```text
SIGTERM during ACQUIRING
SIGTERM during PARSING
SIGTERM during OCR_PROCESSING
SIGTERM during NORMALIZING/SPLITTING
SIGTERM during prepared-artifact publication
SIGTERM during AstraVector delivery
SIGKILL at each of the same checkpoints
process exception after local side effect before DB checkpoint
worker child-process OCR crash
parent worker crash while OCR child is alive
```

For graceful termination, tests SHALL prove:

```text
worker becomes draining/not-ready
new claims stop
safe checkpoint attempted
unfinished work is not marked COMPLETED merely to exit
lease/reclaim restores progress after forced termination
```

Kubernetes-specific tests SHALL include grace-period expiry followed by forced kill.

---

## 8. Lease, heartbeat and fencing corpus

Mandatory race scenarios:

```text
worker A pauses heartbeat
lease expires
worker B reclaims generation N+1
worker A resumes generation N
```

Test variants:

```text
A resumes during local parse
A resumes before DB completion update
A resumes before artifact publication checkpoint
A resumes during downstream delivery
A resumes after B completes successfully
```

Hard assertion: generation N cannot commit authoritative durable state after N+1 exists.

Clock-based assertions SHOULD use PostgreSQL/server time as ownership authority rather than worker wall-clock time.

---

## 9. PostgreSQL failure corpus

Required faults:

```text
connection refused before claim
connection lost after claim
connection lost during heartbeat
statement timeout on checkpoint write
transaction serialization/deadlock retry
connection pool exhaustion
read succeeds but ownership renewal cannot be proven
DB restart while jobs are active
```

Key invariant:

```text
cannot prove ownership
-> no new authoritative downstream mutation
```

Tests SHALL distinguish dependency outage from process liveness to avoid restart storms.

---

## 10. SeaweedFS / object-storage failure corpus

Required scenarios:

```text
timeout before source read
mid-stream TCP reset
short/truncated source read
object disappears after job acceptance
source bytes differ from durable hash/etag/version intent
prepared artifact missing
prepared artifact checksum mismatch
prepared artifact manifest corrupt
slow-read/backpressure
write succeeds but client receives timeout where applicable
```

Expected behavior follows TZ-03/TZ-13:

```text
transient dependency failure -> bounded retry
missing immutable source -> explicit failure
source content mismatch -> fail closed
corrupt prepared artifact -> regenerate only when source+fingerprint permit
```

---

## 11. Local workspace and ephemeral-storage corpus

Required scenarios:

```text
workspace byte quota exceeded
filesystem ENOSPC
inode exhaustion
read-only workspace
orphaned previous-attempt directory
partial OCR raster left after crash
partial prepared staging file
workspace cleanup failure
```

Hard invariant: local workspace is never a durable recovery authority.

On retry, attempt-scoped workspaces SHALL prevent mixed-generation scratch reuse.

For Kubernetes, v6 SHALL include ephemeral-storage pressure/eviction behavior separately from application-level ENOSPC.

---

## 12. Memory and CPU pressure corpus

### Memory

Required:

```text
near-soft-limit workload
application hard-limit rejection
container memory-limit OOMKill
node memory pressure / pod eviction when test environment permits
```

A controlled resource-limit rejection SHALL be distinguishable from an unexpected OOMKill.

### CPU

Required:

```text
CPU saturation
throttled CPU quota
long parser/OCR stage approaching lease window
many small concurrent jobs
one huge job plus small jobs
```

Assertions:

```text
heartbeat/lease safety remains valid
backpressure prevents uncontrolled local concurrency
queue fairness does not collapse under one huge job
```

---

## 13. OCR runtime and GPU robustness corpus

The OCR robustness suite SHALL be backend-aware.

### CPU variants

```text
Paddle CPU runtime crash
ONNX Runtime CPU session failure
model load timeout
runtime unavailable at readiness
```

### GPU variants

```text
GPU unavailable before readiness
GPU disappears / device error during runtime where test harness supports it
CUDA provider initialization failure
GPU OOM
VRAM saturation under concurrent OCR
GPU worker process crash
ONNX CUDA session failure
```

GPU OOM SHALL NOT be treated as ordinary recognized-document failure. It is a resource/runtime failure with explicit classification and retry/routing policy.

A GPU-configured job/profile MUST NOT silently become CPU execution under the same processing fingerprint.

The report SHALL distinguish:

```text
GPU_OOM
GPU_UNAVAILABLE
GPU_PROVIDER_INIT_FAILED
OCR_ENGINE_CRASH
MODEL_LOAD_FAILED
OCR_TIMEOUT
```

---

## 14. Model supply and cache corruption corpus

Required scenarios:

```text
Nexus unavailable but verified required model already cached
Nexus unavailable and required model absent
manifest missing
manifest checksum mismatch
model file checksum mismatch
partial preload directory
wrong artifact revision mounted
wrong execution-provider-compatible artifact
model directory mutated after readiness
```

Expected baseline:

```text
verified cached revision present -> worker may remain ready
required revision unavailable -> worker not OCR-ready
invalid/mutated bundle -> fail closed
no document-time public download
no silent artifact revision fallback
```

---

## 15. Network fault corpus

Fault injection SHALL cover at least:

```text
latency
jitter
packet loss
connection reset
DNS resolution failure
one-way partition where reproducible
short outage
long outage beyond retry window
```

Network faults SHALL be placed independently on:

```text
worker <-> PostgreSQL
worker <-> SeaweedFS
worker <-> AstraVector
preload/init <-> Nexus
```

The harness SHALL record the exact injected fault profile and duration.

---

## 16. AstraVector lost-ACK and downstream ambiguity corpus

Mandatory scenarios from TZ-13/TZ-17:

```text
Start accepted, ACK lost
Append N accepted, ACK lost
Finalize accepted, ACK lost
Delete accepted, ACK lost
status query temporarily unavailable after ambiguous mutation
```

Required assertions:

```text
same idempotency key on Start replay
same session + batch index + content hash on Append replay
status reconciliation before replacement Finalize/session
no synthetic documentVersion bump
no duplicate searchable version
COMPLETED only after searchable=true
```

---

## 17. Prepared-artifact/checkpoint recovery corpus

Required scenarios:

```text
crash before artifact publication
crash after data parts but before manifest commit marker
crash after valid manifest publication before DB checkpoint
DB checkpoint exists but artifact missing
artifact exists but DB checkpoint missing
parser/OCR/normalizer/splitter version mismatch
OCR model bundle revision mismatch
processing profile config hash mismatch
```

The recovery validator SHALL prove whether reuse is:

```text
REUSE_ALLOWED
REPROCESS_REQUIRED
ARTIFACT_CORRUPT
ARTIFACT_MISSING
IDENTITY_MISMATCH
```

Large prepared artifacts SHALL be verified/replayed in a streaming/bounded-memory manner.

---

## 18. Concurrency, fairness and backpressure corpus

Required experiments:

```text
3+ workers contend for one job
3+ workers contend for many jobs
expired jobs mixed with new jobs
large OCR jobs mixed with small native-text jobs
CPU pool + GPU pool simultaneously active
GPU backlog while CPU parser work continues
scale-up during active backlog
scale-down during active work
```

Measurements:

```text
claim uniqueness
oldest runnable job age
queue wait distribution
active jobs / declared capacity
reclaim count
retry storm count
OCR queue depth
AstraVector delivery backlog
```

The suite SHALL detect thundering-herd or retry-synchronization behavior. Backoff jitter must be observable, not merely configured.

---

## 19. Cancellation corpus

Required states:

```text
cancel before claim
cancel during acquisition
cancel during OCR
cancel after prepared artifact
cancel during active AstraVector session
cancel while Finalize outcome is ambiguous
cancel after searchability reached
```

Cancellation SHALL remain cooperative and fenced. A stale worker cannot complete cancellation after ownership changes.

---

## 20. Kubernetes lifecycle corpus

Where a Kubernetes test environment is available, v6 SHALL include:

```text
pod delete with normal grace
pod delete with too-short grace causing SIGKILL
rolling deployment
replica scale-down/drain
node drain
node restart/loss where environment permits
pod eviction due to ephemeral-storage pressure
pod OOMKilled due to memory limit
GPU pod reschedule after node/device unavailability
```

The test harness SHALL not rely on liveness probes to trigger expensive dependency or smoke checks.

---

## 21. Rolling upgrade / downgrade corpus

Required scenarios:

```text
old + new workers coexist with compatible DB/schema
attempt started on old version is reclaimed by new version
prepared artifact created by old version is compatible
prepared artifact created by old version is incompatible
model revision changes during rollout
config profile changes during rollout
rollback to previous image/model revision
```

A worker SHALL not silently continue an incompatible attempt under a different processing semantic identity.

The test report SHALL include image digest, config hash and model revision for every attempt.

---

## 22. Node and disaster-recovery corpus

Release-candidate level scenarios SHOULD include:

```text
worker node loss
restore AstraIndexator PostgreSQL into clean environment
restore SeaweedFS source/prepared artifacts according to retention policy
start new worker pool from clean local disks/model cache
reconcile durable jobs
run controlled pipeline/retrieval smoke
```

AstraVector DR remains owned by AstraVector. AstraIndexator SHALL not treat Qdrant as its backup authority.

---

## 23. Observability is part of pass/fail

A scenario is not fully passed if state eventually recovers but operators cannot determine why it happened.

Each experiment SHALL assert required evidence, including applicable:

```text
jobId/documentId/documentVersion
processingAttemptId
workerId
leaseGeneration
processingStage
failureClass/failureCode
retryable
retry count / nextRetryAt
reclaim event
checkpoint/artifact identity
OCR execution profile
model revision
AstraVector session/batch identity
reconciliation decision
final searchable status
resource high-water / OOM/eviction reason
```

Low-cardinality metrics and structured logs SHALL agree with durable PostgreSQL state.

---

## 24. Recovery-time metrics

v6 SHALL measure, not initially hard-code, at least:

```text
fault detection latency
ownership-loss detection latency
lease expiry/reclaim latency
retry scheduling delay
checkpoint restore latency
searchability recovery latency
DLQ transition latency
```

Evidence-backed SLO/RTO thresholds may be promoted later by reviewed configuration/release policy.

---

## 25. Negative guarantees

Hard negative assertions across the corpus:

```text
no duplicate active authoritative owner
no stale-generation completion
no silent source substitution
no silent model/backend/hardware fallback
no direct Qdrant repair by AstraIndexator
no runtime public model download
no reset/restart of document TTL intent on retry
no access-zone mutation during recovery
no automatic documentVersion bump to escape conflict
no COMPLETED without AstraVector searchable proof
no use of local scratch as durable checkpoint
```

---

## 26. Fault-injection safety and reproducibility

Each destructive test SHALL declare:

```text
scope/environment allowlist
fixture namespace
seed
fault duration
maximum blast radius
cleanup procedure
rollback procedure
```

Production execution is forbidden unless a scenario is explicitly classified as safe production smoke. Destructive chaos experiments belong to dedicated verification environments.

The harness SHALL prefer deterministic trigger points over random kill loops.

---

## 27. Test levels

### PR/public CI

```text
pure coordinator/recovery state tests
fault-classification tests
checkpoint compatibility tests
fake transport lost-ACK tests
model manifest corruption tests
resource-policy unit/component tests
```

### Merge/internal integration

```text
PostgreSQL restart/outage
SeaweedFS fault proxy
multi-worker lease/reclaim
process crash at deterministic checkpoints
workspace/disk faults where safe
CPU OCR runtime crash/restart
```

### GPU/internal capability gate

```text
GPU readiness failure
GPU OOM/resource pressure
ONNX CUDA provider failure
GPU worker restart/reclaim
CPU/GPU semantic equivalence invariants
```

### Release candidate / chaos environment

```text
network partitions
Kubernetes eviction/node drain/node loss
rolling upgrade/rollback
full AstraVector lost-ACK recovery
restore/reconcile from durable authorities
mixed long-running workload under failure
```

---

## 28. Required baseline scenario set

Before v6 may be called implementation-complete, executable evidence SHALL exist for at least:

```text
V6-01 crash during OCR
V6-02 crash after valid prepared artifact publication
V6-03 expired lease + stale worker resumes
V6-04 PostgreSQL outage causes ownership uncertainty
V6-05 SeaweedFS timeout and recovery
V6-06 SeaweedFS source-content mismatch fail-closed
V6-07 local workspace ENOSPC
V6-08 container OOMKill and reclaim
V6-09 Start ACK lost
V6-10 Append ACK lost
V6-11 Finalize ACK lost
V6-12 AstraVector unavailable during delivery
V6-13 cancellation during active session
V6-14 poison job exhausts retry budget -> DEAD_LETTER
V6-15 rolling deployment with compatible artifact reuse
V6-16 incompatible processing fingerprint forces reprocessing
V6-17 GPU unavailable at readiness
V6-18 GPU OOM / provider failure classified explicitly
V6-19 required model missing/corrupt fail-closed
V6-20 repeated recovery does not create duplicate searchable version
V6-21 ephemeral-storage pressure/eviction recovery
V6-22 graceful SIGTERM drain then forced SIGKILL fallback
V6-23 3+ replicas under contention with fault injection
V6-24 restored durable state in clean environment reconciles successfully
```

---

## 29. Reporting contract

Every run SHALL produce a machine-readable report with:

```text
suite/fixture version
scenario id/category
execution profile
hardware/runtime metadata
application image digest/git commit
config hash
model identity
fault definition/seed/timestamps
pre-fault durable state
post-fault durable state
attempt/lease history
checkpoint/reconciliation evidence
resource measurements
final job state
final AstraVector searchable state
pass/fail per invariant
cleanup status
```

A scenario that passes only after manual intervention must be reported as manual-recovery evidence, not automatic-recovery PASS.

---

## 30. Implementation phases

### V6-P0 — harness foundation

- scenario manifest/schema;
- deterministic fault trigger API;
- fault taxonomy;
- invariant assertion library;
- machine-readable reporting;
- safe environment/namespace guardrails.

### V6-P1 — process/lease/database

- deterministic worker crash hooks;
- multi-replica reclaim;
- stale-worker fencing;
- PostgreSQL outage/ownership uncertainty.

### V6-P2 — storage/checkpoint/resource

- SeaweedFS fault proxy;
- source/artifact corruption;
- workspace ENOSPC/inode faults;
- memory/CPU limit scenarios;
- prepared-artifact replay matrix.

### V6-P3 — OCR CPU/GPU/ONNX

- CPU runtime crash;
- GPU readiness/provider/OOM faults;
- execution-profile identity checks;
- model cache corruption;
- no silent fallback proofs.

### V6-P4 — downstream/network ambiguity

- AstraVector lost-ACK Start/Append/Finalize/Delete;
- latency/loss/reset/partition profiles;
- reconciliation and duplicate-prevention proofs.

### V6-P5 — Kubernetes/upgrade/DR

- termination/eviction/node scenarios;
- rolling upgrade/rollback;
- restore into clean environment;
- final smoke/retrieval proof.

---

## 31. Exit criteria for implementation start

Implementation may begin only after review agrees on:

1. scenario manifest schema;
2. deterministic fault-injection mechanism;
3. environment safety guardrails;
4. authoritative invariants and final-state expectations;
5. CPU/GPU/ONNX execution-profile naming;
6. observable failure-code taxonomy;
7. which scenarios belong to public CI vs internal/chaos environments;
8. which v6 scenarios require AstraVector real service versus protocol-compatible fault proxy.

---

## 32. Non-goals

v6 does not itself define:

```text
OCR recognition-quality thresholds        -> TZ-17D
retrieval-ranking quality                 -> future TZ-17E
new business DTOs                         -> TZ-01/TZ-09/TZ-11
new access-zone or TTL semantics          -> TZ-10/TZ-12
Qdrant repair logic                       -> AstraVector responsibility
unbounded random production chaos         -> explicitly prohibited
```

---

## 33. Design conclusion

The production robustness corpus SHALL answer a stronger question than "did the service restart?":

> after a fault, can AstraIndexator prove who owns the job, what side effects may already exist, what state can be safely reused, what must be replayed, and that the intended document version eventually reaches an explicit recoverable or searchable terminal state without semantic identity drift?

If v6 cannot answer that question from durable state and telemetry, the scenario is not considered production-safe.
