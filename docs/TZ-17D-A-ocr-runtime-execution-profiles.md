# TZ-17D-A — OCR Runtime Execution Profiles: CPU, GPU and ONNX Runtime

## Status

- **System:** AstraIndexator 1.0
- **Parent:** `TZ-17D-ocr-derived-multilingual-corpus-v4.md`
- **Related:** TZ-06, TZ-15, TZ-17, TZ-18, M5 OCR Pipeline
- **Status:** Design baseline; implementation not started
- **Purpose:** define deterministic, comparable OCR execution profiles across CPU/GPU and Paddle/ONNX Runtime without changing the semantic contract of M5 -> M6.

## 1. Core principle

The hardware/runtime backend is an execution concern, not a different knowledge contract.

For the same fixture, approved model family/revision, preprocessing profile and reconciliation profile, AstraIndexator SHALL expect the same semantic output contract regardless of whether inference runs on CPU or GPU.

The benchmark therefore separates:

```text
semantic correctness
  !=
engine confidence
  !=
latency
  !=
throughput
  !=
resource footprint
```

CPU/GPU/ONNX differences must never silently change:

- accepted source text;
- language/script;
- protected spans;
- parent provenance;
- page/slide/paragraph occurrence;
- normalized text contract;
- sentence-boundary gold;
- Access Zone/TTL semantics.

## 2. Required execution-profile matrix

The initial v4 verification matrix SHALL support at least the following profiles when the corresponding runtime is available:

```text
OCR_CPU_PADDLE
OCR_CPU_ONNX
OCR_GPU_PADDLE
OCR_GPU_ONNX
```

Recommended baseline priority:

1. `OCR_CPU_PADDLE` — portability/reference baseline;
2. `OCR_CPU_ONNX` — framework-light portable alternative;
3. `OCR_GPU_ONNX` — accelerated ONNX Runtime profile;
4. `OCR_GPU_PADDLE` — optional/native Paddle GPU comparison.

A deployment MAY expose only a subset, but every enabled production profile must have a pinned manifest and verification evidence.

## 3. ONNX model/runtime position

ONNX Runtime is an approved candidate inference backend for OCR verification and deployment.

The model-supply contract SHALL distinguish:

```text
model family
model artifact format
inference engine
execution provider
hardware device
```

Example conceptual identities:

```text
modelFamily      = PP-OCRv6 / approved equivalent
artifactFormat   = ONNX
engine           = onnxruntime
executionProvider= CPUExecutionProvider
hardware         = CPU
```

or:

```text
artifactFormat   = ONNX
engine           = onnxruntime
executionProvider= CUDAExecutionProvider
hardware         = GPU
```

The exact approved OCR model family is evidence-driven and must pass the v4 corpus before production promotion.

## 4. Execution-provider contract

For ONNX Runtime, the configured execution provider is part of processing identity.

Examples:

```text
CPUExecutionProvider
CUDAExecutionProvider
TensorRTExecutionProvider   // optional, separately verified
```

There SHALL be no silent fallback from GPU to CPU in a profile that claims GPU execution.

If `OCR_GPU_ONNX` is configured and the required GPU provider is unavailable, readiness SHALL fail explicitly.

A separately named fallback profile may be configured operationally, but it is a new execution decision and must be visible in diagnostics and processing identity.

## 5. CPU profile contract

CPU profiles are the portability baseline.

Required controls:

```text
intraOpThreads
interOpThreads / execution mode where applicable
worker OCR concurrency
max pages in flight
max pixels per page
max total pixels per job
memory soft/hard guards
candidate timeout
job timeout
```

CPU tests SHALL record:

```text
wall-clock latency
CPU time where measurable
peak RSS / memory high-water
pages/sec
pixels/sec
model load time
warm inference latency
```

Thread counts must be explicit. Benchmark runs with different thread counts are different runtime profiles.

## 6. GPU profile contract

GPU profiles SHALL additionally identify:

```text
gpu vendor/device class
deviceId
runtime/provider version
CUDA/cuDNN versions where applicable
VRAM limit/configuration
precision mode
batch size
concurrency
```

Required GPU metrics:

```text
model load time
cold latency
warm latency
pages/sec
pixels/sec
peak VRAM
host RSS
GPU utilization where available
host<->device transfer overhead when measurable
```

A GPU profile must fail before document processing if the selected provider/device/model combination is incompatible.

## 7. Precision contract

Precision is part of the execution profile.

Possible modes may include:

```text
FP32
FP16
INT8
```

No precision change is considered transparent until corpus evidence proves it.

Therefore:

```text
same model + FP32
!=
same model + FP16
!=
same model + INT8
```

for processing identity and quality reporting.

Each precision profile receives its own v4 quality report.

## 8. Model manifest additions

The OCR model/runtime manifest SHOULD include at least:

```text
modelId
modelFamily
modelKind = OCR
artifactRevision
artifactFormat = PADDLE_STATIC|ONNX|...
engine
engineVersion
executionProvider
supportedDevices[]
precision
languages[]
requiredFiles[]
sha256 for every file
bundleSha256
license
conversionSourceRevision       // for converted ONNX artifacts
conversionToolVersion          // when applicable
opsetVersion                    // ONNX when applicable
```

Converted ONNX artifacts SHALL be treated as independent immutable artifacts with their own checksums, even when derived from the same source model.

## 9. Nexus layout

Nexus model delivery SHALL make runtime/backend identity explicit.

Conceptual layout:

```text
ocr/
  <model-family>/
    <artifact-revision>/
      paddle-static/
        manifest.json
        ...
      onnx/
        manifest.json
        detector.onnx
        recognizer.onnx
        dictionary...
```

Runtime workers SHALL never download or convert a model during processing.

Conversion to ONNX, quantization and validation are build/release activities, not request-path activities.

## 10. Cross-backend semantic equivalence

v4 SHALL execute the same fixture set across every enabled execution profile and compare outputs at multiple layers.

### Layer A — visual transcription

Measure per profile:

```text
CER
WER where reliable
protected-span exact accuracy
Kazakh critical-character accuracy
punctuation/symbol preservation
```

### Layer B — OCR block structure

Compare:

```text
block count/order
page/region association
normalized bbox validity
parent sourceElement relationship
```

Exact engine-native block segmentation need not be byte-identical if downstream canonical reconciliation is equivalent.

### Layer C — canonical post-reconciliation text

This is the strongest cross-backend semantic comparison.

For approved profiles, the corpus SHOULD target identical canonical text for deterministic clean fixtures. Where exact identity is not realistic, the permitted variance must be explicitly declared and measured.

### Layer D — M6 normalized text and sentence boundaries

For the same source fixture and accepted OCR result, the downstream quality gates remain the same:

```text
normalized text correctness
protected-span preservation
sentence precision/recall/F1
fragment hard guards
provenance preservation
```

## 11. Allowed CPU/GPU differences

Differences may be accepted in:

```text
raw confidence values
engine-native polygon coordinates within declared tolerance
latency
throughput
memory/VRAM footprint
internal kernel/provider choices
```

Differences are NOT automatically accepted in:

```text
recognized business identifiers
money values
dates
Access Zone codes
Kazakh-specific letters
sentence boundaries
parent provenance
searchable hallucinated text
```

## 12. Cross-backend comparison gates

The evaluator SHALL report a matrix:

```text
fixture x execution profile
```

and additionally pairwise comparisons:

```text
CPU_PADDLE vs CPU_ONNX
CPU_ONNX   vs GPU_ONNX
CPU_PADDLE vs GPU_PADDLE
GPU_PADDLE vs GPU_ONNX
```

Required pairwise evidence:

```text
canonicalTextExactMatch (where required)
protectedSpanAgreement
criticalCharacterAgreement
sentenceBoundaryAgreement
provenanceAgreement
CER delta
latency ratio
throughput ratio
resource delta
```

A faster backend must not be promoted solely because of performance if semantic quality regresses beyond the approved corpus threshold.

## 13. No silent fallback

Forbidden:

```text
GPU unavailable
-> silently run CPU
```

Forbidden:

```text
ONNX model invalid
-> silently use Paddle model
```

Forbidden:

```text
INT8 unsupported
-> silently switch FP32
```

Every fallback must be an explicit, named operational policy and must change processing identity.

## 14. Processing fingerprint additions

M5 processing fingerprint SHALL include, when applicable:

```text
engine
engineVersion
artifactFormat
executionProvider
hardwareProfileId
device class/id policy
precision
modelId
artifactRevision
bundleSha256
preprocessingVersion
reconciliationVersion
```

This prevents CPU/GPU/ONNX variants from masquerading as the same processing result.

## 15. Readiness contract

Readiness SHALL verify the selected profile before jobs are claimed for OCR execution.

CPU readiness:

```text
verified model bundle
runtime import/load
required execution provider
memory/thread configuration valid
```

GPU readiness:

```text
verified model bundle
GPU visible
selected provider available
runtime/provider versions compatible
model can create session
VRAM admission baseline passes
```

A worker that cannot satisfy its declared OCR execution profile SHALL not advertise OCR readiness.

## 16. Worker topology

Recommended topology separates OCR capability from generic parsing.

```text
parser/general workers
  -> can execute CPU OCR only if configured

ocr-cpu workers
  -> CPU profiles

ocr-gpu workers
  -> GPU profiles
```

The durable M2 job/fencing contract remains authoritative. Hardware-specific workers do not bypass queue ownership or create a second job model.

Routing SHOULD use explicit capability/profile labels, not ad-hoc host inspection inside document logic.

## 17. Deterministic profile selection

Execution-profile selection is a server-side policy.

Inputs may include:

```text
job OCR policy
fixture/production profile
available worker capability
source size/page count
latency class
approved model profile
resource admission
```

The selected profile SHALL be persisted/observable before inference and included in processing evidence.

A retry may move to a different hardware profile only under an explicit retry/fallback policy; such reprocessing has a different processing fingerprint.

## 18. Performance benchmark protocol

Performance measurements are valid only under recorded conditions.

Every benchmark report SHALL record:

```text
host CPU model / allocated cores
RAM limit
GPU model / VRAM when present
container image digest
OS/runtime versions
OCR engine/provider versions
model bundle identity
precision
thread count
batch size
worker concurrency
fixture set/version
cold/warm state
```

Do not compare unrecorded laptop CPU measurements against warmed data-center GPU measurements as if they were one benchmark.

## 19. Required benchmark views

Report at minimum:

```text
p50/p95/p99 candidate latency
p50/p95 page latency
pages/sec
pixels/sec
job wall time
model startup time
CPU peak RSS
GPU peak VRAM
error/timeout rate
quality metrics by language/severity
```

Performance gates must be defined only after representative hardware measurements exist.

## 20. Resource admission differences

CPU and GPU profiles MAY have different limits, for example:

```text
max concurrent pages
max pixel budget
batch size
candidate timeout
memory/VRAM guard
```

But semantic acceptance/rejection must remain explicit and observable.

A GPU OOM risk SHALL produce controlled resource rejection/retry policy, not process-level undefined behavior.

## 21. Embedded visual content

The same CPU/GPU/ONNX matrix applies to embedded image candidates from:

```text
PDF REGION/EMBEDDED_IMAGE
DOCX embedded image
PPTX picture/shape image
```

The benchmark must verify that hardware/backend changes do not alter parent provenance or create duplicate searchable OCR text.

Repeated-image reuse keys SHALL include OCR processing identity, so a result produced under one model/runtime profile is not blindly reused under a semantically different profile.

## 22. Tables and layout components

Where table/layout OCR components are enabled, they SHALL have independent model/runtime identities.

Conceptual pipeline:

```text
text detection
text recognition
optional orientation
optional table/layout analysis
```

Each optional component must be explicitly listed in the bundle manifest and fingerprint.

A table/layout model may use ONNX Runtime independently of the text-recognition backend, but mixed-engine pipelines must be represented explicitly rather than hidden behind one generic `OCR` label.

## 23. Verification phases

### R0 — manifest/runtime contract

- schema for CPU/GPU/ONNX profiles;
- provider availability checks;
- no-silent-fallback tests;
- processing fingerprint tests.

### R1 — CPU reference baseline

- approved CPU Paddle profile;
- approved CPU ONNX profile;
- full v4 Tier A/P1 corpus;
- quality and resource report.

### R2 — GPU equivalence

- GPU ONNX profile;
- optional GPU Paddle profile;
- semantic equivalence report against CPU baseline;
- VRAM/throughput report.

### R3 — degradation/embedded corpus

- Tier B degradations;
- PDF/DOCX/PPTX embedded visuals;
- repeated-image reuse;
- mixed native/OCR reconciliation.

### R4 — release profile promotion

A runtime profile becomes production-approved only after:

```text
model bundle verified
v4 quality gates pass
cross-backend semantic gates pass
resource tests pass
readiness/no-fallback tests pass
crash/reclaim integration passes
```

## 24. Production recommendation

Do not make GPU mandatory for AstraIndexator 1.0.

Recommended deployment strategy:

```text
CPU profile = mandatory portability baseline
ONNX CPU   = preferred framework-light candidate after corpus proof
GPU ONNX   = optional acceleration profile after equivalence proof
GPU Paddle = optional comparison/production profile where operationally justified
```

This preserves portability while allowing acceleration without forking the RAG semantics.

## 25. Non-goals

This document does not choose a final winner between Paddle-native and ONNX Runtime.

The winner is selected by evidence from:

```text
quality
latency
throughput
memory/VRAM
operational complexity
artifact portability
hardware availability
```

No backend is approved merely because it is faster or easier to deploy.
