# TZ-15 — Configuration & Model Delivery

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-15
- **Title:** Configuration & Model Delivery
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-02, TZ-03, TZ-04, TZ-05, TZ-06, TZ-07, TZ-08, TZ-10, TZ-11, TZ-13, TZ-14, TZ-16, TZ-17, TZ-18
- **Target internal artifact registry:** `https://nexus.astrabase.asia`
- **Reference implementation source:** `alimbetov/llm-indexator` model supply contract and model registry, adapted to AstraIndexator boundaries

---

## 2. Purpose

TZ-15 defines how AstraIndexator obtains, validates and applies application configuration and versioned ML/OCR artifacts without making document processing depend on ad-hoc runtime downloads or untracked mutable state.

The configuration and model-delivery plane SHALL provide:

1. deterministic startup configuration;
2. explicit configuration precedence;
3. startup validation of required values and safety limits;
4. separation of ordinary configuration from secrets;
5. immutable model artifact identities;
6. checksum/manifest verification;
7. preloading from internal Nexus;
8. locally available verified model bundles at runtime;
9. offline document-time model execution;
10. controlled CPU/GPU profiles;
11. explicit rollout and rollback;
12. processing-fingerprint integration;
13. observability of active configuration/model versions without leaking credentials.

Canonical model-supply flow:

```text
approved model manifest
        ↓
internal Nexus
        ↓ explicit preload/init/build step
artifact revision + checksum verification
        ↓
local immutable/cache location
        ↓ startup validation
worker readiness
        ↓
document processing uses verified local bundle only
```

Document processing MUST NOT become a model-download workflow.

---

## 3. Core invariants

### CFG-01 — Configuration is explicit and versionable

Operationally meaningful configuration SHALL have documented keys, types, defaults where safe, validation rules and ownership.

### CFG-02 — Safety limits fail closed

Missing/invalid mandatory resource, storage, OCR or downstream limits SHALL fail startup/readiness rather than silently creating an unbounded worker.

### CFG-03 — Secrets are not ordinary configuration artifacts

Passwords/tokens/private keys SHALL be supplied through the deployment secret mechanism defined by TZ-16/TZ-18 and SHALL NOT be committed to repository YAML or model manifests.

### CFG-04 — Runtime models are pinned

A model used for production document processing SHALL be identified by immutable artifact revision and verified content hash/manifest.

### CFG-05 — No document-time runtime download

A processing worker SHALL NOT fetch a model from Nexus or the public internet when a document arrives.

### CFG-06 — No silent model fallback

If the required model/profile is unavailable or invalid, the worker SHALL fail capability/readiness or the OCR operation explicitly; it SHALL NOT silently switch to another model/runtime/device.

### CFG-07 — Model change affects processing identity

OCR model/engine/preprocessing version changes SHALL participate in the processing fingerprint defined by TZ-03/TZ-06.

### CFG-08 — Application-config change and model change are distinct

Changing a timeout or Nexus URL does not by itself imply a new OCR semantic result; changing the OCR model artifact/profile does.

### CFG-09 — CPU is portable baseline

CPU OCR/profile SHALL be supported as the minimum portable deployment capability. GPU profiles are explicit optional capabilities.

### CFG-10 — Runtime is offline from artifact registries

Once ready, a worker SHOULD remain able to process documents without contacting Nexus, assuming required verified artifacts already exist locally.

---

## 4. Configuration domains

AstraIndexator configuration SHALL be partitioned by responsibility rather than placed into one unstructured environment-variable namespace.

Recommended domains:

```text
application
postgresql
seaweedfs
acquisition
parser
ocr
normalization
splitter
astravector
coordinator
reliability
observability
security
modelSupply
runtime
```

Each domain SHALL have a typed configuration object/schema in implementation.

---

## 5. Configuration precedence

Recommended precedence from lowest to highest:

```text
1. code-safe defaults
2. packaged application config
3. environment/profile config
4. mounted ConfigMap/config file
5. explicit environment variables
6. command-line/runtime override only where approved
```

Secrets are resolved separately and are not treated as ordinary final overrides.

Rules:

- precedence SHALL be deterministic;
- startup SHALL log non-secret effective configuration identity/hash and active profile;
- unknown critical keys SHOULD fail validation where practical;
- deprecated keys SHALL produce explicit warnings and migration guidance;
- runtime mutation of processing-significant config is not baseline behavior unless separately designed.

---

## 6. Effective configuration identity

AstraIndexator SHOULD compute a deterministic effective configuration fingerprint for the non-secret processing-significant subset.

Conceptually:

```text
canonical validated config subset
    ↓ stable serialization
SHA-256
    ↓
effectiveConfigSha256
```

This value may be stored in attempts/prepared manifests/observability metadata where useful.

Secrets MUST NOT participate as raw values in the hash/log output; secret version/reference MAY participate if safe and operationally useful.

---

## 7. Processing-significant configuration

Examples that SHALL participate directly or indirectly in processing fingerprint/version evidence:

```text
parser profile/version
OCR decision profile
OCR modelId + artifactRevision + manifest/hash
OCR preprocessing/rendering profile
normalizer profile/version
splitter profile/version
canonical schema version
```

Examples that generally do not alter document semantics and SHOULD remain operational config only:

```text
PostgreSQL pool size
HTTP/gRPC timeout
worker poll interval
Nexus hostname
metrics scrape port
log level
```

A change that affects output semantics MUST NOT be hidden as merely an infrastructure change.

---

## 8. Secret configuration

Secrets MAY include:

```text
PostgreSQL credentials
SeaweedFS/S3 credentials
AstraVector mTLS/client credentials where applicable
Nexus preload credentials
observability exporter credentials
```

Rules:

1. secrets MUST NOT be committed to Git;
2. secrets MUST NOT appear in model manifests;
3. secrets MUST NOT be logged;
4. signed URLs/tokens MUST NOT be copied into permanent source metadata;
5. document-time workers SHOULD NOT require Nexus credentials when using preloaded local artifacts;
6. rotation SHALL be possible without changing document processing identity unless the rotated secret changes actual content/service behavior.

Detailed secret management belongs to TZ-16/TZ-18.

---

## 9. Nexus role

The internal registry target is:

```text
https://nexus.astrabase.asia
```

Nexus is an artifact distribution source, not the runtime model API.

AstraIndexator SHALL use Nexus for approved versioned artifacts such as:

```text
OCR detection models
OCR recognition models
OCR classification/orientation models
character dictionaries
model manifests
checksums/signature metadata
optional parser ML artifacts in future
```

Embedding/BGE-M3 model ownership remains AstraVector, not AstraIndexator.

---

## 10. Repository/layout recommendation

Exact Nexus repository names are deployment decisions, but the logical path SHOULD make model identity immutable and human-auditable.

Recommended shape:

```text
astra-models/
  ocr/
    paddleocr/
      ru-kk-en/
        cpu/
          <artifactRevision>/
            model-bundle.tar.zst
            model_manifest.json
            SHA256SUMS
        cuda/
          <artifactRevision>/
            model-bundle.tar.zst
            model_manifest.json
            SHA256SUMS
```

Alternative packaging is allowed if the same properties hold:

- immutable revision path;
- explicit manifest;
- checksum verification;
- no mutable `latest` as production identity.

A `latest` alias MAY exist for humans/development but production config MUST resolve to an immutable revision before readiness.

---

## 11. Canonical model registry entry

AstraIndexator SHOULD keep a small approved model registry/config manifest conceptually similar to the proven `llm-indexator` pattern.

Example:

```yaml
schemaVersion: 1
models:
  paddleocr_cpu_ru_kk_en:
    modelKind: ocr
    engine: paddleocr
    backend: paddle_inference
    runtime: cpu
    device: cpu
    languages: [ru, kk, en]
    artifactRevision: "2026.08.1"
    nexusArtifact: "ocr/paddleocr/ru-kk-en/cpu/2026.08.1/model-bundle.tar.zst"
    localPath: "/models/paddleocr-cpu-ru-kk-en/2026.08.1"
    checksumSha256: "..."
    requiredFiles:
      - model_manifest.json
      - det/inference.pdmodel
      - det/inference.pdiparams
      - rec/inference.pdmodel
      - rec/inference.pdiparams
      - rec/character_dict.txt
      - cls/inference.pdmodel
      - cls/inference.pdiparams
```

Exact PaddleOCR file names may evolve with engine versions; therefore required files are manifest data, not universal constants.

---

## 12. Model manifest contract

Every production model bundle SHALL include a machine-readable manifest.

Required/expected fields:

```text
manifestSchemaVersion
modelId
modelKind
engine
engineVersion
backend
runtime
device
languages[]
artifactRevision
createdAt
license
source/provenance where permitted
requiredFiles[]
fileChecksums{}
bundleChecksumSha256
compatibility{}
```

Optional compatibility fields may include:

```text
pythonVersionRange
paddleVersion
cudaVersion
cudnnVersion
architecture
minimumMemory
preprocessingProfileVersion
characterDictionaryVersion
```

A worker MUST NOT infer compatibility merely from directory names.

---

## 13. Integrity verification

Before a model becomes eligible for runtime use, verification SHALL check:

```text
artifact exists
immutable revision matches config
bundle checksum matches
manifest parses
modelId matches expected
modelKind matches expected
engine/backend/runtime/device match profile
languages include required baseline set
required files exist
per-file checksums match when declared
license/provenance fields are present when required
compatibility constraints pass
```

Failure is explicit and blocks model capability/readiness.

---

## 14. Supply modes

Supported deployment strategies MAY include:

### 14.1 Image-baked model

```text
CI/build
  -> fetch approved artifact
  -> verify
  -> bake into immutable OCR worker image
```

Advantages: strongest immutability/offline startup.

Trade-off: larger image and model update requires image rebuild.

### 14.2 Init-container preload

```text
Pod starts
  -> init container authenticates to Nexus
  -> downloads immutable revision
  -> verifies bundle
  -> writes shared model volume
  -> runtime container starts offline
```

This is the preferred Kubernetes baseline when image size/model rollout independence matter.

### 14.3 Node/host preload

Useful for standalone/VM deployments:

```text
operator/preload job
 -> Nexus
 -> verified host model cache
 -> runtime mounts read-only
```

All three modes SHALL converge on the same verified local model layout/manifest semantics.

---

## 15. Runtime local-cache semantics

Runtime model path SHOULD be read-only from the worker perspective.

Recommended layout:

```text
/models/
  <modelId>/
    <artifactRevision>/
      model_manifest.json
      ... model files ...
```

Rules:

- immutable revision directories are never modified in place;
- partial downloads use staging paths and are atomically promoted only after verification;
- a failed preload does not replace a previously valid revision;
- cache cleanup is age/reference-aware;
- the currently active revision MUST NOT be deleted while referenced by running workers/jobs/prepared processing fingerprints.

---

## 16. Offline runtime policy

The document-processing container SHALL be configured so model frameworks do not attempt public/runtime downloads.

Where applicable:

```text
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

or equivalent engine-specific offline controls SHALL be enabled.

A network call to Nexus/public model registry from `OcrEngine.recognize()` or equivalent document-time path is prohibited.

---

## 17. Worker capability profiles

Baseline profiles:

### `budget_cpu`

```text
OCR runtime: CPU
languages: ru/kk/en
bounded concurrency
portable deployment
```

### `enterprise_gpu`

```text
OCR runtime: CUDA
languages: ru/kk/en
explicit CUDA/cuDNN/runtime compatibility
gpu-memory guard
explicit worker scheduling
```

### `development`

May use smaller resource limits or test artifacts but MUST NOT silently become production profile.

A worker advertises only capabilities whose artifacts/runtime passed startup verification.

---

## 18. CPU/GPU fallback policy

Silent device fallback is prohibited.

Example:

```text
configured enterprise_gpu
GPU unavailable
```

MUST result in one of the explicitly configured outcomes:

```text
worker not ready for OCR capability
or
job routed to a CPU-capable worker pool by scheduler/coordinator policy
```

It MUST NOT execute the same job on CPU inside the GPU worker without an explicit profile/routing decision.

This keeps latency, capacity and reproducibility observable.

---

## 19. Startup validation

Before becoming ready, each worker SHALL validate all required configuration for its advertised capabilities.

Checks include at least:

```text
configuration schema valid
mandatory safety limits present
PostgreSQL configuration shape valid
SeaweedFS configuration shape valid
AstraVector endpoint/protocol config valid
workspace limits valid
OCR model registry entry exists if OCR capability enabled
local model revision exists
model manifest/checksums valid
runtime engine importable
CPU/GPU capability matches profile
Nexus runtime download disabled
fallback disabled unless explicitly modeled
```

Dependency liveness checks may be separated from static configuration validation according to TZ-18 readiness design.

---

## 20. Liveness versus readiness

Configuration/model failure SHOULD generally affect readiness, not process liveness.

Examples:

```text
process loop healthy + OCR model invalid
→ live=true
→ ready=false for OCR-capable workload
```

A complete worker-pool design MAY expose capability-scoped readiness so native-only parsing capacity is not necessarily removed when an optional OCR profile is unavailable.

Exact Kubernetes probe behavior belongs to TZ-18.

---

## 21. Model rollout

A production model rollout SHALL be explicit.

Recommended sequence:

```text
1. publish immutable model revision to Nexus
2. publish/approve manifest + checksums
3. update AstraIndexator model registry/profile config
4. preload new revision
5. verify startup/capability tests
6. deploy canary worker(s)
7. run golden OCR/RAG verification
8. expand rollout
9. retain previous revision for rollback window
```

No mutable artifact should change under an existing `artifactRevision`.

---

## 22. Rollback

Rollback SHALL select a previously approved immutable revision rather than mutate current files.

```text
new revision N+1 fails
  -> config selects revision N
  -> workers restart/reload through approved lifecycle
```

Prepared artifacts generated with N+1 remain identifiable because the model revision belongs to the processing fingerprint.

Whether they may be reused after rollback is decided by processing-fingerprint compatibility; they MUST NOT be silently treated as revision N output.

---

## 23. Hot reload

Hot-reloading model binaries inside a running worker is NOT baseline behavior.

Reasons:

- in-flight document reproducibility;
- memory fragmentation/leaks;
- mixed model versions inside one job;
- difficult rollback semantics.

Baseline:

```text
configuration/model revision change
→ controlled worker restart/rolling deployment
```

Safe dynamic reload MAY be designed later with explicit per-job model pinning.

---

## 24. Per-job model pinning

Once an OCR-capable job begins semantic processing, the effective model identity used for that attempt SHALL be recorded:

```text
modelId
artifactRevision
engineVersion
preprocessingProfileVersion
```

A running attempt MUST NOT switch model revision halfway through a document due to configuration refresh.

If a reclaimed job reuses a compatible prepared artifact, the manifest retains the original processing fingerprint/model revision.

If OCR must be rerun, the new attempt uses the then-selected approved revision and produces a new processing fingerprint.

---

## 25. Nexus availability semantics

Nexus availability is required for preload/update operations, not for ordinary document-time inference when artifacts are already present.

Therefore:

```text
Nexus down
+ verified required local model present
→ existing worker may remain OCR-ready
```

while:

```text
Nexus down
+ required revision absent
→ preload fails
→ new worker not ready for that OCR profile
```

This prevents registry outages from unnecessarily stopping already provisioned runtime capacity.

---

## 26. Nexus credentials

Nexus credentials SHOULD be scoped to the preload/build component rather than every processing container.

Preferred Kubernetes split:

```text
init container / preload job
  -> has read-only Nexus credential

runtime worker
  -> mounts verified model volume read-only
  -> no Nexus credential required
```

This reduces blast radius and belongs to the TZ-16 trust-boundary design.

---

## 27. Configuration example

Conceptual application config:

```yaml
astraIndexator:
  profile: production

  coordinator:
    claimLimit: 2
    leaseSeconds: 120
    heartbeatSeconds: 30

  acquisition:
    maxSourceBytes: 1073741824

  parser:
    profile: structure-v1

  ocr:
    enabled: true
    mode: OCR_IF_NEEDED
    capabilityProfile: budget_cpu
    modelId: paddleocr_cpu_ru_kk_en
    preprocessingProfile: ocr-preprocess-v1

  normalization:
    profile: multilingual-general-v1

  splitter:
    profile: multilingual-general-v1

  modelSupply:
    runtimeDownloadAllowed: false
    localRoot: /models
    registryFile: /etc/astra-indexator/models.yaml
    nexus:
      baseUrl: https://nexus.astrabase.asia
      repository: astra-models
```

Numeric limits shown here are examples only unless fixed by their owning TZ/deployment configuration.

---

## 28. Configuration validation model

Implementation SHOULD expose a typed validation command/startup phase conceptually equivalent to:

```text
load raw config
  ↓
resolve profile/precedence
  ↓
resolve secret references
  ↓
validate schema/types/ranges
  ↓
validate cross-field invariants
  ↓
validate model registry/profile
  ↓
compute effective config fingerprint
  ↓
start service
```

Cross-field validations include examples such as:

```text
heartbeatSeconds < leaseSeconds
OCR enabled -> approved OCR modelId required
GPU profile -> CUDA runtime/device required
runtimeDownloadAllowed must be false in production
splitter hard max > target
AstraVector client safe batch limits > 0
workspace limits > acquisition/parser temporary requirements
```

---

## 29. Configuration ownership matrix

| Concern | Authority |
|---|---|
| PostgreSQL coordinator timings | AstraIndexator deployment/config |
| source/file safety limits | TZ-04 config |
| parser profile | TZ-05 config |
| OCR mode/model/profile | TZ-06 + TZ-15 |
| OCR artifact bytes/revision | Nexus approved model artifact |
| normalization profile | TZ-07 config |
| splitter profile/guards | TZ-08 config |
| access-zone TTL policy | AstraVector registry, not AstraIndexator config |
| AstraVector server limits | AstraVector runtime; Indexator uses conservative client config |
| secrets | TZ-16/TZ-18 secret provider |
| logging/metrics exporters | TZ-14/TZ-18 |

AstraIndexator config MUST NOT override AstraVector authoritative access-zone/TTL business policy.

---

## 30. Observability

TZ-14 SHALL expose at least:

```text
applicationVersion
activeProfile
effectiveConfigSha256
parserProfile
normalizerProfile
splitterProfile
ocrCapabilityProfile
ocrModelId
ocrArtifactRevision
ocrEngineVersion
modelVerificationStatus
runtimeDownloadAllowed=false
```

Metrics SHOULD remain low-cardinality, e.g.:

```text
astra_indexator_model_ready{model_id,profile} 1
astra_indexator_model_verification_failures_total{reason}
astra_indexator_config_validation_failures_total{reason}
astra_indexator_model_preload_failures_total{reason}
```

Do not expose Nexus credentials, secret values or full artifact URLs containing credentials.

---

## 31. Failure taxonomy

Recommended errors:

```text
CONFIG_SCHEMA_INVALID
CONFIG_REQUIRED_VALUE_MISSING
CONFIG_VALUE_OUT_OF_RANGE
CONFIG_CROSS_FIELD_INVARIANT_FAILED

MODEL_REGISTRY_ENTRY_NOT_FOUND
MODEL_ARTIFACT_NOT_FOUND
MODEL_MANIFEST_MISSING
MODEL_MANIFEST_INVALID
MODEL_ID_MISMATCH
MODEL_REVISION_MISMATCH
MODEL_CHECKSUM_MISMATCH
MODEL_REQUIRED_FILE_MISSING
MODEL_RUNTIME_INCOMPATIBLE
MODEL_LANGUAGE_CAPABILITY_MISSING
MODEL_DEVICE_UNAVAILABLE
MODEL_PRELOAD_FAILED
MODEL_CACHE_INCOMPLETE
RUNTIME_MODEL_DOWNLOAD_FORBIDDEN
MODEL_FALLBACK_FORBIDDEN

NEXUS_UNAVAILABLE
NEXUS_AUTH_FAILED
NEXUS_ARTIFACT_NOT_FOUND
```

Document/job retry classification depends on whether failure occurs during deployment/preload or after a worker already advertised capability. Most immutable model-contract defects are permanent deployment/configuration defects rather than ordinary document retries.

---

## 32. Security requirements

1. Nexus uses TLS.
2. Credentials are external secrets, never committed model registry data.
3. Preload credentials SHOULD be read-only to approved repositories.
4. Artifact revision/checksum validation is mandatory before activation.
5. Runtime workers SHOULD not possess Nexus credentials where architecture permits.
6. Artifact extraction SHALL use safe paths and bounded resources.
7. Model files SHOULD be mounted read-only during processing.
8. Logs SHALL not print credentials or authorization headers.
9. Public internet model downloads are disabled in production.
10. Unapproved model IDs/revisions fail closed.

Detailed supply-chain hardening/signature policy belongs to TZ-16.

---

## 33. Recovery semantics

Model/config recovery differs from document recovery.

If a worker dies:

```text
job recovery -> TZ-13
model revision -> reconstructed from deployment/profile + durable processing fingerprint/prepared manifest
```

A recovered job that can reuse a prepared artifact does not need the old OCR model merely to deliver existing canonical fragments.

A recovered job that must rerun OCR requires an approved available OCR model and creates output under the active processing fingerprint.

No job may claim that regenerated OCR with a different model revision is byte/semantic-identical to the old prepared artifact without verification.

---

## 34. Verification requirements

TZ-17 SHALL include at least:

1. valid CPU model preload from Nexus fixture/repository;
2. invalid checksum rejects startup/readiness;
3. missing manifest rejects model;
4. missing required file rejects model;
5. wrong modelId/revision rejected;
6. runtime-download attempt blocked;
7. Nexus unavailable + local verified cache -> worker remains capable;
8. Nexus unavailable + cache missing -> worker not ready;
9. partial preload never replaces valid revision;
10. CPU profile OCR smoke;
11. GPU profile compatibility smoke where GPU deployment exists;
12. configured GPU but unavailable device -> no silent CPU fallback;
13. model upgrade changes processing fingerprint;
14. rollback reselects previous immutable revision;
15. in-flight job does not change model halfway through;
16. prepared artifact records model revision;
17. config precedence produces deterministic expected result;
18. secret values absent from effective config/log output;
19. invalid lease/heartbeat cross-field config rejected;
20. missing safety limit causes startup/readiness failure;
21. local model path mounted read-only in production deployment proof;
22. model/version fields visible in TZ-14 inventory/diagnostics.

---

## 35. Acceptance criteria

TZ-15 is satisfied when:

- **AC-01:** configuration domains and precedence are explicit;
- **AC-02:** mandatory safety limits are startup-validated;
- **AC-03:** secrets are separated from ordinary config;
- **AC-04:** production model identity is immutable/pinned;
- **AC-05:** Nexus is artifact supply, not document-time inference dependency;
- **AC-06:** runtime model downloads are prohibited;
- **AC-07:** model manifest and checksum validation are mandatory;
- **AC-08:** invalid/missing model bundle blocks advertised OCR readiness;
- **AC-09:** CPU baseline and optional GPU profile are explicit;
- **AC-10:** silent CPU/GPU/model fallback is prohibited;
- **AC-11:** model revision participates in processing fingerprint;
- **AC-12:** in-flight document processing uses one pinned model revision;
- **AC-13:** preload uses staging + atomic promotion semantics;
- **AC-14:** image-baked/init-container/host-preload modes converge on same local contract;
- **AC-15:** rollback selects prior immutable revision without rewriting artifacts;
- **AC-16:** Nexus outage does not stop workers with already verified required artifacts;
- **AC-17:** effective non-secret config/model identity is observable;
- **AC-18:** runtime worker credentials are minimized;
- **AC-19:** model/config failures have explicit error taxonomy;
- **AC-20:** TZ-17 proves model integrity, offline runtime, rollout/rollback and config validation behavior.

---

## 36. Final invariant

The production model path is:

```text
approved immutable artifact
   -> Nexus
   -> explicit preload/build/init step
   -> manifest + SHA-256 verification
   -> immutable local revision
   -> startup/readiness capability proof
   -> document-time offline inference
```

Never:

```text
document arrives
   -> download whatever model name is configured
   -> fallback if unavailable
   -> process without durable version evidence
```

Configuration and model supply must make document processing reproducible, diagnosable and rollback-safe.
