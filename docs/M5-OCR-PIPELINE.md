# M5 — OCR Pipeline & Nexus Model Supply

## Status

**IMPLEMENTED BASELINE — merge requires green full CI.**

Specification authority: `TZ-06-ocr-pipeline.md`, with model-delivery details owned by TZ-15 and verification depth owned by TZ-17.

## Implemented boundary

```text
ParsedDocument + OcrCandidate[]
        ↓
OcrDecisionPolicy
        ↓
PAGE / REGION / EMBEDDED_IMAGE
        ↓
DefaultOcrInputResolver
        ↓
bounded local raster/image
        ↓
OcrEngine
        ↓
OcrObservation[]
        ↓
native/OCR reconciliation
        ↓
canonical DocumentElement[]
```

M5 does not own PostgreSQL job coordination, Access Zones/TTL, normalization, logical splitting, embeddings or AstraVector delivery.

## OCR modes

Implemented:

- `OCR_DISABLED`
- `OCR_IF_NEEDED` — default
- `OCR_FORCE`

`OcrCandidate` is evidence for consideration. An optional embedded-image candidate in an otherwise healthy native document does not downgrade the whole document when OCR is disabled.

## Candidate inputs

The baseline resolver supports:

- standalone JPEG/PNG;
- page/frame-wise TIFF;
- PDF `PAGE` and `REGION` rendering through `pypdfium2`;
- DOCX inline-image extraction using M4 `paragraphIndex + drawingIndex` provenance;
- PPTX image extraction using `slideNumber + shapeId` provenance.

PDF rendering is page-selective. The resolver does not rasterize all PDF pages into memory at once.

## OCR engine

`PaddleOcrEngine` is the initial engine adapter. It is isolated behind the `OcrEngine` protocol.

Production model directories are always explicit:

- `text_detection_model_dir`;
- `text_recognition_model_dir`.

The document execution path never resolves or downloads models from the Internet.

`IsolatedPaddleOcrEngine` runs PaddleOCR in a persistent child process. Candidate timeout terminates the child process and the next request lazily starts a fresh worker, providing a real kill boundary rather than a caller-only timeout.

## Model supply

The approved runtime input is a verified local bundle containing `manifest.json`.

Required manifest evidence includes:

```text
schemaVersion = astra-indexator-ocr-model-v1
modelKind = OCR
modelId
engine
engineVersion
artifactRevision
languages[]
textDetectionModelDir
textRecognitionModelDir
files[{path,sha256}]
```

`verify_local_bundle()` fails closed on:

- missing manifest;
- unsupported schema/model kind;
- path traversal;
- missing required file/directory;
- SHA-256 mismatch.

`NexusOcrBundlePreloader` is an explicit startup/init utility. It is restricted by default to the origin:

```text
https://nexus.astrabase.asia
```

It verifies the expected manifest SHA-256, downloads each explicitly declared file with checksum verification, and publishes the local manifest last as the bundle commit marker.

No Nexus call exists in per-document recognition.

Exact Nexus repository coordinates and credential injection remain TZ-15 deployment configuration.

## Readiness

`check_ocr_readiness()` verifies:

- bundle integrity;
- configured language capability;
- PaddleOCR runtime when OCR workers require it;
- CUDA capability for explicit GPU configuration.

Parser-only topology may call readiness with `require_runtime=False`, preserving independence from OCR runtime/model execution.

## Multilingual baseline

The default profile is:

```text
ocr_cpu_ru_kk_en_v1
languages = ru, kk, en
device = cpu
```

The OCR layer preserves Unicode text as returned by the engine. It performs no translation or transliteration.

## Provenance

Accepted OCR elements retain:

- candidate ID;
- source element ID;
- page number;
- block order;
- normalized bounding box where available;
- raw engine polygon as diagnostics;
- confidence;
- engine/version;
- model ID;
- artifact revision;
- bundle checksum;
- OCR profile;
- decision/preprocessing/reconciliation versions.

PaddleOCR pixel geometry is normalized at the adapter boundary to `normalized-top-left` coordinates. Raw polygons remain metadata only.

## Reconciliation

The baseline never performs blind:

```text
nativeText + ocrText
```

Native text for the same page/region is preferred when normalized similarity identifies the OCR observation as a duplicate. Distinct OCR text is projected into canonical elements under the source image/page element.

Repeated identical visual content is SHA-256 keyed within one processing invocation:

```text
same image bytes + same processing context
→ recognize once
→ project observations to each occurrence provenance
```

## Confidence

Two distinct thresholds are modeled:

- hard floor: observations below it are discarded;
- acceptance threshold: observations below it but above the hard floor are retained with `LOW_CONFIDENCE` evidence.

Thresholds are profile/configuration values, not corpus-independent truth constants.

## Resource governance

The baseline profile contains guards for:

- minimum image dimensions/pixels;
- maximum pages per job;
- maximum pixels per page;
- maximum total pixels per job;
- maximum derived bytes;
- approximate rendered-memory soft/hard limits;
- maximum concurrent pages (CPU baseline = 1);
- candidate timeout;
- job deadline;
- render DPI.

Actual process/container RSS enforcement still belongs to deployment/cgroup limits in TZ-18; the current in-process memory estimate is an early rejection guard, not a substitute for pod memory limits.

## Processing fingerprint

Fingerprint includes at least:

```text
source SHA-256
parser identity/profile
OCR mode
OCR profile
OCR decision-policy version
OCR preprocessing version
OCR reconciliation version
engine/version
model ID
artifact revision
bundle SHA-256
languages
```

Changing model revision/checksum changes the fingerprint.

## Executable evidence in ordinary CI

The M5 tests prove without requiring heavyweight production models:

- multilingual Unicode projection (`kk/ru/en`);
- native/OCR duplicate suppression;
- `OCR_DISABLED` behavior;
- optional embedded image does not downgrade a good native document;
- pixel resource rejection before engine execution;
- bundle checksum tamper rejection;
- model revision changes fingerprint;
- identical image recognition reuse with per-occurrence provenance;
- parser-only readiness against a verified local bundle;
- real page-wise PDF rendering with `pypdfium2`.

The complete M1–M5 suite continues to run together with PostgreSQL/Testcontainers CI.

## Evidence intentionally deferred to TZ-17 / M11

The ordinary public CI does **not** claim proof of OCR recognition quality with the approved production model bundle because Nexus credentials/model artifacts are not part of the repository.

The following remain mandatory verification gates before production promotion:

- real approved PaddleOCR CPU bundle smoke;
- representative Russian recognition corpus;
- Kazakh corpus including `Ә Ғ Қ Ң Ө Ұ Ү Һ І`;
- English and mixed `ru/kk/en` pages;
- character/word error measurements;
- repeated-logo/decorative-image corpus;
- mixed native/scanned PDF corpus;
- process RSS/high-water measurement;
- crash/reclaim/fencing E2E through the worker orchestrator;
- GPU smoke only if a GPU profile is enabled;
- retrieval impact after normalization/splitting/AstraVector integration.

These are quality/deployment evidence gaps, not hidden claims of the M5 component tests.

## Completion boundary

M5 is considered implementation-complete when its full repository CI is green. Production OCR readiness additionally requires a TZ-15 approved Nexus bundle and TZ-17 corpus evidence.
