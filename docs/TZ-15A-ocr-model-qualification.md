# TZ-15A — OCR Model Evaluation, Legal Provenance, Qualification & Nexus Promotion

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-15A
- **Title:** OCR Model Evaluation, Legal Provenance, Qualification & Nexus Promotion
- **Parent:** TZ-15 Configuration & Model Delivery
- **Related:** TZ-06, TZ-09, TZ-13, TZ-14, TZ-15, TZ-17, TZ-17D, TZ-17D-A, TZ-17F, TZ-18
- **Target internal registry:** `https://nexus.astrabase.asia`
- **Status:** Normative qualification baseline; no production OCR model is approved by this document alone
- **Primary language requirement:** `kk`, `ru`, `en`, mixed `kk/ru/en`

---

## 2. Purpose

TZ-15 defines how approved model artifacts are delivered and verified at runtime. TZ-15A defines the prerequisite process by which an OCR artifact becomes eligible for that delivery plane.

Canonical lifecycle:

```text
upstream model / artifact
        ↓
CANDIDATE
        ↓ legal + provenance gate
technical structural validation
        ↓
TZ-17D v4 corpus qualification
        ↓
runtime / performance qualification
        ↓
QUALIFIED
        ↓ explicit promotion decision
immutable Nexus publication
        ↓
APPROVED
        ↓
TZ-15 preload/runtime delivery
```

TZ-15A SHALL prevent AstraIndexator from treating popularity, model-card claims, file extension, successful ONNX load or permissive source-code license as sufficient proof for production use.

No candidate receives `QUALIFIED` or `APPROVED` status merely by being listed in this specification.

---

## 3. Design principles

### Q-01 — Model code license is not model artifact license

The following SHALL be evaluated separately:

```text
source-code license
model weights license
character-dictionary license
conversion-tool license
third-party notices
redistribution rights
commercial/internal-use rights
modification/conversion rights
```

A permissive repository license SHALL NOT be assumed to cover every downloaded binary artifact unless the upstream evidence supports that conclusion.

### Q-02 — Exact revision is the unit of qualification

Qualification applies to an immutable artifact revision, not to a moving family name such as `PP-OCRv5`, `latest`, `main` or `mobile`.

### Q-03 — CPU is mandatory portable baseline

A primary AstraIndexator OCR candidate MUST support CPU execution. GPU capability is optional and separately qualified.

### Q-04 — ONNX is mandatory for primary runtime candidates

Primary production candidates MUST have either:

1. an official upstream ONNX artifact; or
2. a reproducible, legally permitted and audited conversion path to ONNX.

Non-ONNX engines MAY participate only as `CONTROL` unless a later architecture decision explicitly changes this requirement.

### Q-05 — Kazakh support must be proven, not inferred

`Cyrillic` support alone does not prove Kazakh OCR capability.

At minimum the recognizer output vocabulary SHALL be able to represent:

```text
Ә ә
Ғ ғ
Қ қ
Ң ң
Ө ө
Ұ ұ
Ү ү
Һ һ
І і
```

Vocabulary presence is admission evidence only; OCR accuracy for these characters MUST be proven by TZ-17D v4.

### Q-06 — No runtime acquisition

Qualification/build/preload may access approved upstream sources. Document-time inference MUST NOT access GitHub, Hugging Face, ModelScope, public Paddle services or any model registry.

### Q-07 — Quality claims require corpus evidence

No model is declared the production winner by desk review. Recognition quality, critical-token quality, embedded-image quality and runtime equivalence are established only through executable evidence.

### Q-08 — Precision variants are distinct artifacts

```text
FP32 != FP16 != INT8
```

Each variant has a separate artifact revision, checksum, compatibility record and qualification result.

### Q-09 — Runtime/backend identity is explicit

At minimum the following are distinct processing profiles where used:

```text
ONNX Runtime CPUExecutionProvider
ONNX Runtime CUDAExecutionProvider
ONNX Runtime TensorRTExecutionProvider
native Paddle CPU
native Paddle GPU
```

Silent fallback between them is prohibited.

### Q-10 — Nexus contains controlled artifacts, not arbitrary experiments

The production Nexus layout SHOULD contain `QUALIFIED` / `APPROVED` immutable artifacts and associated evidence. Mutable experimental model files SHALL NOT be treated as production coordinates.

---

## 4. Qualification lifecycle

Canonical states:

```text
DISCOVERED
CANDIDATE
QUALIFIED
APPROVED
DEPRECATED
REVOKED
RETIRED
```

### 4.1 DISCOVERED

A model family/artifact has been identified but has not passed formal admission review.

### 4.2 CANDIDATE

Minimum requirements:

- exact upstream project/model/revision known;
- source URL(s) recorded;
- preliminary license/provenance evidence available;
- artifact size known or measurable;
- claimed language/runtime characteristics recorded;
- no known hard exclusion against AstraIndexator requirements.

`CANDIDATE` does not authorize production publication or runtime selection.

### 4.3 QUALIFIED

Requires all applicable gates in this specification to pass, including TZ-17D v4 evidence for the exact artifact/profile.

### 4.4 APPROVED

Requires explicit project promotion decision after qualification and publication of an immutable verified bundle to Nexus.

`QUALIFIED` does not automatically imply `APPROVED`.

### 4.5 DEPRECATED

Still usable for rollback/reference during a controlled migration window but not selected for new default deployments.

### 4.6 REVOKED

Immediate prohibition due to reasons such as:

```text
license/provenance problem
security issue
artifact corruption
serious corpus regression
runtime incompatibility
supply-chain compromise
```

A revoked revision MUST NOT become ready for new OCR work.

### 4.7 RETIRED

No longer supported or distributed for active deployment. Historical manifests/reports SHOULD remain auditable according to retention policy.

---

## 5. Current model shortlist

The shortlist is an input to TZ-17D v4, not a production decision.

### Candidate A — Portable ONNX baseline

```text
PP-OCRv5_mobile_det_onnx
+
cyrillic_PP-OCRv5_mobile_rec_onnx
+
ONNX Runtime
```

Current desk-review role:

```text
Priority A
portable CPU baseline
GPU acceleration candidate
```

Current research evidence:

- upstream family: PaddlePaddle / PaddleOCR;
- code license: Apache-2.0;
- official model repositories expose Apache-2.0 metadata for the selected ONNX artifacts;
- detector size: approximately `4.83 MB`;
- recognizer size: approximately `8.05 MB`;
- neural bundle size: approximately `12.9 MB` excluding ancillary/config evidence;
- official ONNX artifact path exists;
- recognizer is documented for Russian, Kazakh and English among Cyrillic languages;
- upstream character vocabulary contains the required Kazakh characters;
- CPUExecutionProvider is mandatory qualification target;
- CUDAExecutionProvider is an optional accelerated target.

Status:

```text
CANDIDATE
```

Open proof:

```text
actual KK/RU/EN CER/WER
critical Kazakh-glyph accuracy
embedded PDF/DOCX/PPTX image quality
mobile detector recall on real enterprise scans
CPU/GPU semantic equivalence
FP16/INT8 impact if produced
```

### Candidate B — Detection-quality challenger

```text
PP-OCRv5_server_det_onnx
+
cyrillic_PP-OCRv5_mobile_rec_onnx
```

Current desk-review role:

```text
Priority B
quality-oriented detector challenger
```

Current research evidence:

- same recognition layer/language vocabulary as Candidate A;
- server detector ONNX size approximately `88.1 MB`;
- combined neural bundle approximately `96.2 MB`;
- official PP-OCRv5 detection metrics indicate higher detection Hmean for server detector than mobile detector in upstream benchmark material;
- CPU/runtime cost is substantially higher and MUST be justified by AstraIndexator corpus gain.

Status:

```text
CANDIDATE
```

Required decision question for v4:

```text
does the larger detector materially improve OCR on:
small-font scans
tables
stamps/seals
screenshots
low-contrast PDF
embedded Office images
without unacceptable CPU/RAM/startup cost?
```

### Candidate C — Alternate ONNX packaging / supply path

```text
RapidOCR PP-OCRv5 mobile detector
+
RapidOCR cyrillic PP-OCRv5 mobile recognition
```

Role:

```text
Priority C
portability / packaging / supply-chain comparison
```

Important classification:

```text
NOT an independent ML-family accuracy vote
```

because the underlying model family originates from PaddleOCR.

Research evidence:

- RapidOCR engineering/runtime project: Apache-2.0;
- RapidOCR publishes ONNX-oriented model manifests and checksums for PP-OCR-derived artifacts;
- useful for verifying an alternate conversion/package/runtime path;
- model copyright/provenance still traces to the Paddle/Baidu upstream and SHALL be reviewed accordingly.

Status:

```text
CANDIDATE
```

Preferred canonical production source remains the shortest provable chain when equivalent official Paddle ONNX artifacts exist:

```text
PaddlePaddle official artifact
→ Astra qualification
→ Nexus
```

rather than adding an unnecessary intermediary.

### Candidate D — Independent CPU control

```text
Tesseract 5
+
kaz.traineddata
+
rus.traineddata
+
eng.traineddata
```

Role:

```text
independent CPU CONTROL
```

Research evidence:

- Tesseract source: Apache-2.0;
- official tessdata: Apache-2.0;
- explicit `kaz`, `rus`, `eng` language data exist;
- approximate language-data footprint:
  - `kaz` ~8.8 MB;
  - `rus` ~19 MB;
  - `eng` ~22.4 MB;
  - total ~50 MB;
- fully offline CPU operation;
- no primary ONNX/GPU path.

Status:

```text
CONTROL
```

Tesseract SHALL NOT become the primary ONNX profile through this specification, but it SHOULD remain in the evaluation corpus as an independent reference baseline.

---

## 6. Explicitly excluded / watchlist families

### 6.1 PP-OCRv6

Status:

```text
WATCHLIST
```

Reason: technological interest alone is insufficient. The current admission evidence used by this project does not yet establish the required `RU + KK + EN` profile with Kazakh proof strongly enough to replace the PP-OCRv5 Cyrillic candidate.

A future exact PP-OCRv6 artifact MAY enter `CANDIDATE` when upstream language/vocabulary/license/runtime evidence satisfies this specification.

### 6.2 EasyOCR

Status:

```text
OUT_OF_BASELINE / WATCHLIST
```

Reasons:

- popular permissive project and useful RU/EN support;
- official Kazakh production support is not sufficiently established for this hard requirement;
- primary runtime remains PyTorch-centric;
- third-party ONNX conversions increase provenance complexity.

A future official release MAY be reconsidered.

### 6.3 Heavy VLM/document models

Large VLM/OCR-document models are out of the baseline unless a future capability requires semantic visual understanding beyond text OCR.

Reason: M4 already owns document structure and M6 owns linguistic normalization/splitting; a heavy VLM is not justified merely to recover text from scans/embedded images.

---

## 7. Legal and provenance gate

Before `QUALIFIED`, every artifact MUST have an evidence record for:

| Evidence | Requirement |
|---|---|
| Official upstream project | MUST |
| Exact upstream revision/commit/model revision | MUST |
| Download/source URL | MUST |
| Source-code SPDX/license copy | MUST |
| Model-weight license evidence | MUST |
| Dictionary/source vocabulary provenance | MUST |
| Dictionary license evidence | MUST where separately licensed |
| Conversion-tool license/version | MUST if converted |
| Third-party notices | MUST when applicable |
| Commercial/internal-use permissibility | MUST be reviewed |
| Redistribution permissibility | MUST be reviewed |
| Modification/conversion permissibility | MUST if conversion/quantization occurs |
| Local SHA-256 | MUST |
| Artifact size | MUST |
| Archived model-card/provenance metadata | SHOULD |
| Legal review/reference ID | MUST before `APPROVED` |

Engineering review is not legal advice. If licensing evidence is ambiguous, the artifact SHALL NOT progress to `APPROVED` until the responsible legal/compliance process resolves the ambiguity.

---

## 8. Canonical qualification manifest

Every candidate/qualified bundle SHALL have a machine-readable manifest. Recommended schema:

```yaml
schemaVersion: astra.ocr-model-bundle/v1

model:
  id: ppocrv5-cyrillic-mobile
  displayName: PP-OCRv5 Cyrillic Mobile
  upstreamProject: PaddlePaddle/PaddleOCR
  upstreamModel: cyrillic_PP-OCRv5_mobile_rec
  upstreamRevision: <immutable-revision>
  artifactFormat: onnx
  precision: fp32

languages:
  required: [kk, ru, en]

criticalAlphabet:
  - Ә
  - ә
  - Ғ
  - ғ
  - Қ
  - қ
  - Ң
  - ң
  - Ө
  - ө
  - Ұ
  - ұ
  - Ү
  - ү
  - Һ
  - һ
  - І
  - і

license:
  codeSpdx: Apache-2.0
  weightsSpdx: <verified-or-null>
  dictionarySpdx: <verified-or-null>
  reviewed: false
  reviewId: null
  notices: []

artifacts:
  detector:
    file: detector.onnx
    sizeBytes: <value>
    sha256: <sha256>
    irVersion: <value>
    opsets: []
  recognizer:
    file: recognizer.onnx
    sizeBytes: <value>
    sha256: <sha256>
    irVersion: <value>
    opsets: []
  dictionary:
    file: character_dict.txt
    sizeBytes: <value>
    sha256: <sha256>

runtime:
  engine: onnxruntime
  testedProviders:
    - CPUExecutionProvider
  onnxRuntimeVersion: <pinned-version>
  executionProfileId: <profile-id>

provenance:
  downloadedAt: <timestamp>
  sourceUrls: []
  converted: false
  sourceArtifactSha256: <sha256-or-null>
  converter: null

qualification:
  status: CANDIDATE
  corpusId: TZ-17D-v4
  corpusRevision: null
  reportSha256: null
  cpuPassed: false
  gpuPassed: false
  legalPassed: false

promotion:
  nexusCoordinate: null
  approvedAt: null
  approvedBy: null
```

If converted:

```yaml
provenance:
  converted: true
  sourceArtifactSha256: <sha256>
  converter:
    name: <converter>
    version: <version>
    command: <canonical-command>
    requestedOpset: <value>
    producedOpsets: []
```

The manifest SHALL describe what was actually produced. It MUST NOT assume a universal ONNX opset value.

---

## 9. Artifact identity

At minimum, a qualified artifact identity includes:

```text
modelId
artifactRevision
artifactFormat
precision
engine/runtime
executionProvider profile
model file hashes
dictionary hash
preprocessing profile version
```

Changing any semantic model artifact or precision variant SHALL produce a new artifact identity.

Example distinct artifacts:

```text
ppocrv5-cyrillic-mobile / r1 / onnx / fp32 / cpu
ppocrv5-cyrillic-mobile / r1-fp16 / onnx / fp16 / cuda
ppocrv5-cyrillic-mobile / r1-int8 / onnx / int8 / cpu
```

A worker/job MUST NOT claim that two variants are equivalent unless a separate qualification report proves the allowed equivalence contract.

---

## 10. Immutable acquisition and integrity

Candidate acquisition SHALL:

1. use an approved upstream source;
2. pin exact revision where supported;
3. record source URL and revision;
4. compute SHA-256 locally;
5. archive license/model-card evidence;
6. avoid mutable aliases as qualification identity.

A changed file under the same apparent upstream label SHALL be treated as a new candidate revision until proven identical by hash.

---

## 11. ONNX structural qualification

For primary candidates, the qualification harness MUST validate each ONNX artifact before functional OCR tests.

Required evidence:

```text
onnx.checker success
IR version
opset domain/version list
input names/shapes/types
output names/shapes/types
file size
SHA-256
external-data references if any
```

Structural validity only proves that the model is a valid ONNX graph; it does not prove correct preprocessing, postprocessing, dictionary mapping or OCR accuracy.

---

## 12. CPU runtime gate

Primary candidates MUST pass a pinned ONNX Runtime `CPUExecutionProvider` smoke and functional run.

Evidence:

```text
onnxruntime version
provider requested
provider actually active
model load result
functional gold-image result
cold startup latency
warm startup latency
peak RSS baseline
```

If CPU execution is unavailable, the artifact cannot be the portable primary AstraIndexator profile.

---

## 13. GPU runtime gate

GPU qualification is optional but explicit.

Candidate GPU provider baseline:

```text
CUDAExecutionProvider
```

Optional:

```text
TensorRTExecutionProvider
```

Required evidence:

```text
GPU model
VRAM
driver version
CUDA/runtime versions
onnxruntime-gpu version
provider requested
provider actually active
model load/warmup
functional result
peak VRAM
host RSS
```

Configured GPU profile + unavailable GPU/provider SHALL fail readiness/capability. Silent CPU fallback is prohibited.

---

## 14. FP32 / FP16 / INT8 policy

FP32 is the reference precision unless the exact upstream artifact is defined otherwise.

FP16 and INT8 require independent qualification.

For each precision variant, TZ-17D MUST report at least:

```text
CER delta vs reference
WER delta where reliable
critical-token delta
Kazakh critical-glyph delta
confidence distribution delta
latency
throughput
RAM / VRAM
```

Special confusion checks SHOULD include:

```text
І / I / l / 1
О / Ө / 0
К / Қ
Н / Ң
У / Ұ / Ү
Ә / А
Ғ / Г
Һ / Н / h
```

No precision variant inherits `QUALIFIED` or `APPROVED` from another precision.

---

## 15. TZ-17D v4 quality gate

A candidate cannot become `QUALIFIED` until the exact artifact/profile has been exercised by the approved TZ-17D OCR-derived multilingual corpus.

Mandatory language/content scope:

```text
kk
ru
en
mixed kk/ru/en
```

Mandatory source classes include:

```text
clean render
synthetic degradation
representative scan/photo where permitted
PDF OCR pages/regions
PDF embedded images
DOCX embedded images
PPTX images
screenshots
scanned tables
forms/labels
stamps/seals where relevant
negative/decorative images
```

Required measurements:

```text
CER
WER where segmentation is reliable
protected-token exact accuracy
Kazakh critical-character precision/recall/F1
confusion matrix
punctuation/symbol preservation
reading order evidence
provenance correctness
native/OCR duplicate suppression
sentence-boundary impact after M6
```

---

## 16. Critical-token qualification

Average CER is insufficient for enterprise OCR qualification.

The corpus SHALL separately score exact preservation of critical tokens including at minimum:

```text
Ә Ғ Қ Ң Ө Ұ Ү Һ І
Ё
₸
№
Access Zone 0000
Access Zone 0100
UUID
hash
IP
URL
email
money amounts
dates/times
contract/document identifiers
API identifiers
```

Conceptual metric:

```text
CriticalTokenExactAccuracy =
correct_critical_tokens / all_critical_tokens
```

Examples of business-significant failure even when average CER is low:

```text
0000 -> OOOO
0100 -> O1OO
₸ -> T
№ -> N
2026-08-24 -> 2026-O8-24
Қ -> К
І -> I
```

No universal threshold is invented by TZ-15A. Thresholds SHALL be promoted only after baseline distributions exist from v4.

---

## 17. Embedded visual qualification

Candidate qualification SHALL include OCR on images embedded inside:

```text
PDF
DOCX
PPTX
```

Required categories are inherited from TZ-17D and include:

```text
TEXT_SCREENSHOT
SCANNED_PARAGRAPH
SCANNED_TABLE
FORM_OR_LABELS
DIAGRAM_TEXT
STAMP_TEXT
PHOTO_TEXT where eligible
LOGO
WATERMARK
DECORATIVE_IMAGE
```

Hard requirements:

```text
useful OCR text retains parent provenance
native/OCR duplicates are suppressed
repeated logos/watermarks do not pollute retrieval
decorative/no-text images do not generate searchable hallucinated text
critical values inside raster tables remain measurable
```

---

## 18. CPU/GPU/runtime equivalence

The same artifact/profile across CPU/GPU execution MAY differ in:

```text
latency
throughput
resource usage
confidence within reviewed tolerance
raw floating-point output
```

It MUST NOT silently change:

```text
document identity
source provenance
language profile
critical-token semantics
normalized downstream text beyond accepted corpus tolerance
processing profile identity
```

CPU/GPU equivalence SHALL be established by corpus evidence, not assumed from framework compatibility.

---

## 19. Benchmark contract

Every qualification report MUST identify the hardware/runtime profile.

### CPU

Record at least:

```text
CPU model/vendor
architecture/ISA where available
physical/logical cores
allocated cores
RAM
ONNX Runtime version
thread count
cold startup
warm startup
p50/p95/p99 latency
images/sec or pages/sec
peak RSS
CPU utilization
```

### GPU

Record at least:

```text
GPU model
VRAM
driver
CUDA/runtime
ONNX Runtime GPU version
cold model load
warmup
p50/p95/p99 latency
images/sec or pages/sec
GPU utilization
peak VRAM
host RSS
```

Candidate comparisons MUST use the same corpus revision and an explicitly recorded `hardwareProfileId`.

---

## 20. Promotion scoring policy

TZ-15A deliberately does not define a single weighted score before baseline measurements exist.

Promotion review SHOULD consider at least:

```text
legal/provenance confidence
KK/RU/EN recognition quality
critical-token accuracy
embedded-image quality
negative/hallucination behavior
CPU portability
GPU acceleration where used
artifact size
cold/warm startup
latency/throughput
RAM/VRAM
supply-chain simplicity
operational maturity
```

A small average CER advantage MUST NOT automatically outweigh severe critical-token failures.

---

## 21. Nexus promotion

Only `QUALIFIED` artifacts are eligible for promotion to the controlled Nexus model repository. `APPROVED` is assigned through an explicit promotion decision.

Recommended immutable layout:

```text
astra-models/
  ocr/
    <family>/
      <modelId>/
        <artifactRevision>/
          <artifactFormat>-<precision>/
            manifest.yaml
            detector.onnx
            recognizer.onnx
            character_dict.txt
            LICENSE
            THIRD_PARTY_NOTICES
            upstream-provenance.json
            qualification-report.json
```

Production identity MUST NOT depend on:

```text
latest
current
mutable model.onnx
```

A human-facing alias MAY exist, but deployment config resolves to an immutable coordinate before readiness.

---

## 22. Nexus publication contract

Before publishing an `APPROVED` bundle, verify:

```text
qualification status = QUALIFIED
legalPassed = true
exact artifact hashes recorded
qualification report hash recorded
corpus revision recorded
runtime providers recorded
manifest self-consistent
all required files present
bundle coordinates immutable
```

Publication SHOULD be atomic from the consumer perspective: incomplete staging content must not masquerade as a valid promoted revision.

---

## 23. Rollback

Rollback selects a previously approved immutable revision.

```text
revision N+1 degraded
→ select revision N
→ preload/verify N
→ restart/roll worker profile
→ run smoke/reconciliation
```

Artifacts produced by N+1 retain their N+1 processing fingerprint. They MUST NOT be silently relabeled as N output.

---

## 24. Deprecation and revocation

### Deprecation

Use when a better approved artifact supersedes an older one but immediate prohibition is unnecessary.

### Revocation

Use for hard stop conditions such as:

```text
license withdrawal/ambiguity
supply-chain compromise
checksum/provenance failure
security vulnerability
severe discovered corpus defect
incompatible runtime defect
```

Revocation requirements:

```text
new readiness fails for revoked revision
new jobs cannot select revoked revision
operator-visible reason recorded
replacement/rollback guidance recorded
historical processing evidence remains readable
```

---

## 25. Runtime readiness integration

TZ-15 remains authoritative for runtime preload/readiness. TZ-15A adds the requirement that a runtime model registry entry reference only an allowed lifecycle state.

Baseline:

```text
CANDIDATE  -> not production ready
QUALIFIED  -> technically/legal eligible for promotion
APPROVED   -> production-selectable
DEPRECATED -> selectable only by explicit rollback/exception policy
REVOKED    -> not selectable
RETIRED    -> not selectable
```

A worker SHALL fail closed if configured for an artifact whose lifecycle state is not permitted by the active environment policy.

---

## 26. Processing fingerprint integration

The effective OCR processing fingerprint SHALL include enough data to distinguish materially different qualified execution:

```text
modelId
artifactRevision
bundle/model hashes
artifactFormat
precision
engine/runtime
executionProvider profile
character dictionary/version/hash
OCR preprocessing version
OCR decision/reconciliation versions
```

Hardware metadata such as GPU serial number is operational evidence, not necessarily semantic identity; execution provider/profile identity is semantic/qualification evidence where backend differences are separately qualified.

---

## 27. Open items reserved for TZ-17D v4

The following SHALL remain OPEN until executable corpus evidence exists:

```text
production winner
mobile-detector vs server-detector winner
absolute Kazakh OCR quality
acceptable CER/WER thresholds
acceptable CriticalTokenExactAccuracy threshold
CPU vs GPU equivalence tolerance
FP16 qualification
INT8 qualification
acceptable latency/throughput targets
acceptable RAM/VRAM budget
confidence hard floor calibration
```

TZ-15A MUST NOT silently convert these open questions into constants.

---

## 28. Current architectural hypothesis

The following is a hypothesis to test, not an approval:

```text
PP-OCRv5_mobile_det_onnx
+
cyrillic_PP-OCRv5_mobile_rec_onnx
+
ONNX Runtime CPU

is the minimal portable baseline candidate
for AstraIndexator KK/RU/EN.

CUDA GPU is an accelerated profile of the same semantic pipeline
only after equivalence proof.

PP-OCRv5_server_det_onnx is permitted only if v4 demonstrates
material quality benefit that justifies its larger footprint.
```

RapidOCR remains an alternate packaging/runtime path. Tesseract remains an independent CPU control.

---

## 29. Acceptance criteria for TZ-15A itself

TZ-15A design is complete when:

### AC-01
A normative model lifecycle exists from discovery through revocation/retirement.

### AC-02
Legal/provenance evidence is separated into code, weights, dictionary, conversion tooling and notices.

### AC-03
Primary candidate policy requires CPU + offline + ONNX.

### AC-04
GPU is explicitly optional and silent fallback is prohibited.

### AC-05
Kazakh vocabulary requirement explicitly lists `Ә Ғ Қ Ң Ө Ұ Ү Һ І` in both cases where applicable.

### AC-06
Candidate A/B/C and Tesseract CONTROL are explicitly classified without declaring a winner.

### AC-07
PP-OCRv6 and EasyOCR are not implicitly promoted without satisfying the same admission gates.

### AC-08
Canonical manifest captures artifact hashes, provenance, licenses, runtime/provider and qualification evidence.

### AC-09
FP32/FP16/INT8 are distinct qualification identities.

### AC-10
TZ-17D v4 is the mandatory quality gate before `QUALIFIED`.

### AC-11
Critical-token metrics are mandatory and separate from average CER/WER.

### AC-12
Embedded PDF/DOCX/PPTX images are part of qualification.

### AC-13
CPU/GPU/runtime equivalence is evidence-based.

### AC-14
Nexus publication is immutable and contains qualification evidence.

### AC-15
Rollback, deprecation and revocation semantics are explicit.

### AC-16
No runtime model download is allowed.

### AC-17
No universal production-quality thresholds are invented before v4 baseline evidence.

---

## 30. Definition of Done

TZ-15A is considered approved as a specification when:

```text
document merged into main
+
current shortlist recorded as CANDIDATE/CONTROL only
+
no production winner declared
+
legal/provenance manifest contract frozen for v4 implementation
+
TZ-17D v4 implementation can consume the shortlist and produce qualification reports
+
TZ-15 runtime model delivery can consume only approved immutable bundles
```

Implementation of model downloading, corpus execution, qualification reporting and Nexus promotion tooling is intentionally subsequent work.

---

## 31. Next step

After TZ-15A approval, the next implementation milestone SHALL be:

```text
TZ-17D v4 — OCR-Derived Multilingual Corpus implementation
```

Initial v4 bake-off target:

```text
Candidate A
Candidate B
Candidate C packaging/runtime path
Tesseract CONTROL
```

The first v4 run produces baseline evidence. A later reviewed change converts evidence-backed thresholds into release gates and may promote one or more exact artifacts from `CANDIDATE` to `QUALIFIED`.