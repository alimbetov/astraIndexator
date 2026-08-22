# TZ-06 — OCR Pipeline

## 1. Document status

- **System:** AstraIndexator 1.0
- **Specification:** TZ-06
- **Title:** OCR Pipeline
- **Status:** Consolidated design baseline
- **Parent specification:** `TZ-00-system-architecture.md`
- **Related specifications:** TZ-03, TZ-04, TZ-05, TZ-07, TZ-08, TZ-09, TZ-13, TZ-14, TZ-15, TZ-16, TZ-17, TZ-18
- **Model registry target:** `https://nexus.astrabase.asia`
- **Reference implementation source:** `alimbetov/llm-indexator` OCR/model-supply components, adapted to AstraIndexator boundaries

---

## 2. Purpose

TZ-06 defines the OCR processing contract for documents and images that cannot be represented adequately through native parsing alone.

OCR is not the primary parser. It is a conditional enrichment stage that consumes page/image candidates produced by TZ-05 and emits provenance-preserving recognized text blocks back into the canonical document model.

Canonical flow:

```text
AcquiredSource
  ↓
TZ-05 native parser
  ↓
ParsedDocument + OcrCandidate[]
  ↓
OCR decision policy
  ↓
page/image rendering
  ↓
OCR engine
  ↓
OcrObservation[]
  ↓
native/OCR reconciliation
  ↓
DocumentElement[] with provenance
  ↓
TZ-07 normalization
```

---

## 3. Core invariants

### OCR-01 — OCR is conditional

AstraIndexator SHALL NOT OCR every document/page by default.

### OCR-02 — Decision granularity is page/region-aware

A PDF may contain native, scanned and mixed pages. OCR SHALL be selected per page and, where supported, per image/region rather than only per whole document.

### OCR-03 — Native text is not blindly duplicated

When native text is trustworthy, OCR output MUST NOT be appended as a second copy of the same content.

### OCR-04 — Original language is preserved

OCR SHALL recognize source text; it SHALL NOT translate or transliterate it as part of indexing.

### OCR-05 — Provenance is mandatory

Every accepted OCR text block MUST retain page/image/region origin, OCR engine/model identity and confidence evidence when available.

### OCR-06 — Runtime model downloads are forbidden

Production workers SHALL use pinned, pre-provisioned and verified model artifacts. Model delivery is handled through TZ-15, with Nexus as the target internal registry.

### OCR-07 — Resource use is bounded

Page count, rendered pixels, DPI/scale, memory, concurrency, execution time and intermediate artifacts SHALL be bounded by server-side configuration.

### OCR-08 — OCR failure does not invent text

Low confidence, timeout, unavailable model or decoding failure MUST be represented explicitly; the system SHALL NOT fabricate replacement text.

### OCR-09 — OCR is deterministic relative to its processing fingerprint

The chosen engine/model/profile/preprocessing version SHALL be part of processing identity so replay/reindex semantics are auditable.

### OCR-10 — Access-zone and TTL are transparent

OCR does not derive or modify document access zone or TTL.

---

## 4. Responsibility boundary

### 4.1 TZ-05 owns

- native parsing;
- page classification evidence;
- image extraction/reference;
- reading-order reconstruction for native elements;
- generation of OCR candidates.

### 4.2 TZ-06 owns

- OCR eligibility/decision;
- rendering/preprocessing needed for OCR;
- engine selection by configured profile;
- OCR execution;
- block-level confidence/bounding boxes;
- native/OCR overlap reconciliation;
- OCR diagnostics;
- OCR provenance and model identity.

### 4.3 TZ-07 owns

- canonical textual cleanup after native/OCR reconciliation;
- whitespace/Unicode normalization while preserving provenance.

### 4.4 TZ-15 owns

- Nexus coordinates;
- model artifact manifest;
- checksums/signatures;
- preload/cache/startup verification;
- rollout/version selection.

TZ-06 consumes only a verified local `OcrModelBundle` abstraction and SHALL NOT contain repository credentials or dynamic model-resolution business logic.

---

## 5. OCR modes

Supported job/profile intent SHALL include at least:

```text
OCR_DISABLED
OCR_IF_NEEDED
OCR_FORCE
```

Semantics:

### `OCR_DISABLED`

No OCR is executed. If required content cannot be recovered natively, the outcome is explicit (`OCR_REQUIRED_BUT_DISABLED` or equivalent policy result).

### `OCR_IF_NEEDED`

Default production mode. OCR runs only for candidates whose native extraction/layout evidence is insufficient.

### `OCR_FORCE`

For diagnostic/reprocessing workflows. OCR may be run on all eligible pages/images, but native/OCR reconciliation still prevents duplicate canonical text.

`OCR_FORCE` MUST NOT silently become the default for ordinary jobs.

---

## 6. OCR candidate contract

TZ-05 SHALL produce candidates conceptually equivalent to:

```json
{
  "candidateId": "...",
  "candidateType": "PAGE",
  "pageNumber": 3,
  "sourceElementId": null,
  "bbox": null,
  "nativeTextChars": 18,
  "nativeTextCoverage": 0.04,
  "pageClass": "SCANNED_IMAGE",
  "reasonCodes": ["LOW_NATIVE_TEXT", "FULL_PAGE_IMAGE"],
  "imageRef": null,
  "renderRequired": true
}
```

Candidate types SHOULD support:

```text
PAGE
EMBEDDED_IMAGE
REGION
```

A candidate is evidence for OCR consideration, not an instruction to accept all OCR output.

---

## 7. OCR decision policy

Decision inputs MAY include:

```text
job OCR mode
canonical file format
page classification
native extracted character count
native text area coverage
image coverage ratio
parser confidence/diagnostics
presence of text-bearing images
repeated header/footer exclusion
resource limits
available verified OCR profile
```

Canonical decision result:

```text
NOT_REQUIRED
REQUIRED
REQUIRED_BUT_DISABLED
REJECTED_RESOURCE_LIMIT
UNSUPPORTED
```

AstraIndexator SHALL persist decision reason codes for observability and replay diagnostics.

---

## 8. PDF page policy

For the TZ-05 page classes:

```text
NATIVE_TEXT
SCANNED_IMAGE
MIXED
LOW_SIGNAL
EMPTY
```

baseline behavior is:

| Page class | OCR_IF_NEEDED |
|---|---|
| `NATIVE_TEXT` | normally no OCR |
| `SCANNED_IMAGE` | OCR required |
| `MIXED` | OCR only for uncovered/image regions where possible; page OCR allowed with dedup reconciliation |
| `LOW_SIGNAL` | OCR candidate; policy uses native quality/layout evidence |
| `EMPTY` | OCR only if raster/image evidence exists; otherwise remain empty |

A whole PDF MUST NOT be classified as scanned solely because one page lacks native text.

---

## 9. Standalone image policy

Baseline image formats admitted by TZ-04:

```text
JPEG
PNG
TIFF
```

Standalone image documents are OCR candidates by default under `OCR_IF_NEEDED`.

Multi-page TIFF is processed page/frame-wise under configured limits.

Decorative or near-empty images SHOULD be filterable using dimensions/content heuristics before expensive recognition.

---

## 10. Embedded image policy

Not every embedded image deserves OCR.

Candidate filtering SHOULD consider:

```text
minimum width/height
minimum pixel count
page-relative area
repetition across pages
image hash reuse
decorative/logo likelihood
caption/context proximity
native text already covering same bbox
```

Repeated logos/watermarks SHOULD NOT generate repeated searchable OCR text.

Images likely to contain tables, scanned paragraphs, forms, labels or diagrams with textual annotations SHOULD remain eligible.

---

## 11. Multilingual baseline

The baseline language set is:

```text
ru
kk
en
```

The OCR profile SHALL support Cyrillic content needed for Russian and Kazakh plus Latin English content.

Language handling rules:

1. language changes inside one document/page are allowed;
2. language switching is not an OCR split boundary by itself;
3. no automatic translation;
4. no automatic transliteration;
5. original recognized Unicode is preserved;
6. model/profile metadata records supported languages;
7. unsupported language evidence generates diagnostics rather than silent substitution.

A single page may contain Russian, Kazakh and English text.

---

## 12. Model profiles

Baseline profiles SHALL be capability-oriented rather than hard-coded into business logic.

Conceptual examples:

```text
ocr_cpu_ru_kk_en
ocr_gpu_ru_kk_en
```

The existing `llm-indexator` reference already distinguishes CPU/GPU PaddleOCR bundles with `ru/kk/en` support. AstraIndexator may reuse this concept but MUST pin its own approved artifact/version/checksum contract.

Required model bundle metadata SHOULD include:

```text
modelId
modelKind = OCR
engine
engineVersion
backend
runtime
device
languages[]
artifactRevision
checksumSha256
requiredFiles[]
license
modelManifestVersion
```

---

## 13. Nexus model-delivery contract

Target registry:

```text
https://nexus.astrabase.asia
```

TZ-06 SHALL NOT call Nexus during recognition of an individual document.

Production model flow:

```text
Nexus
  ↓ explicit preload/init/build step
artifact download
  ↓
checksum + manifest verification
  ↓
local immutable/cache directory
  ↓
startup/readiness validation
  ↓
OCR worker uses local bundle
```

Requirements:

- artifact coordinates are explicit;
- artifact revision is pinned;
- SHA-256 is pinned/verified;
- required files are enumerated;
- model-local manifest is validated;
- runtime fallback to arbitrary public download is forbidden;
- credentials are supplied as runtime secrets;
- rollback to a previously approved model version is operationally possible.

Exact Nexus repository/layout/credential mechanism belongs to TZ-15.

---

## 14. No runtime download

The OCR execution path MUST fail explicitly if the selected local model bundle is missing or invalid.

Forbidden behavior:

```text
model missing
→ silently download from internet
```

Also forbidden:

```text
model missing
→ silently fall back to another model/engine
```

The reference `llm-indexator` already uses this fail-closed model-supply principle; AstraIndexator adopts it as baseline.

---

## 15. OCR engine abstraction

Conceptual interface:

```python
class OcrEngine(Protocol):
    def recognize(self, request: OcrRequest) -> OcrResult:
        ...
```

`OcrRequest` SHOULD identify:

```text
candidate/page/image
local rendered/image source
language profile
engine profile
processing fingerprint
resource budget
```

`OcrResult` SHOULD include:

```text
blocks[]
pagesProcessed
engine
engineVersion
modelId
artifactRevision
modelChecksum
preprocessingVersion
memoryHighWater
processingDuration
warnings[]
```

---

## 16. OCR block contract

Each OCR observation SHALL preserve at least:

```json
{
  "text": "Құжаттың атауы",
  "bbox": [0.12, 0.18, 0.74, 0.24],
  "confidence": 0.96,
  "pageNumber": 3,
  "blockOrder": 7,
  "candidateId": "...",
  "sourceImageId": "...",
  "engine": "...",
  "modelId": "..."
}
```

Bounding boxes SHOULD use a canonical coordinate convention defined with TZ-09. Normalized coordinates are preferred for cross-render consistency when exact source units are unavailable.

The raw engine-specific geometry MAY be retained in diagnostics but SHALL NOT leak into downstream contracts as the only coordinate representation.

---

## 17. Confidence semantics

OCR confidence is evidence, not truth.

The system SHALL distinguish:

```text
engine confidence
block acceptance threshold
document/page quality assessment
```

A single global confidence threshold MUST NOT be treated as a substitute for corpus-level validation.

Low-confidence blocks MAY be:

- retained with `LOW_CONFIDENCE` quality flag;
- excluded when below a hard floor;
- routed to review/reprocessing policy;

according to an explicit versioned OCR profile.

The original confidence value MUST remain available for diagnostics when the engine provides it.

---

## 18. Rendering policy

PDF OCR requires rasterization.

Rendering MUST be page-selective whenever possible.

Configurable bounds SHALL include:

```text
render_dpi or scale
max_render_width
max_render_height
max_pixels_per_page
max_total_ocr_pixels_per_job
max_pages_per_job
```

AstraIndexator SHALL NOT render all pages of a large PDF into a list of full-resolution images before processing.

Preferred flow:

```text
page N
  → render bounded image
  → OCR
  → emit observations
  → release image memory
page N+1
```

This improves memory safety versus materializing the full document raster set.

---

## 19. Preprocessing

Optional preprocessing MAY include:

```text
orientation correction
rotation/deskew
contrast normalization
grayscale/binarization
safe resize/downscale
noise reduction
```

Every preprocessing algorithm/profile that can affect recognized text SHALL be versioned as part of `ocrPreprocessingVersion` and processing fingerprint.

Preprocessing MUST preserve mapping back to source page coordinates or record the transformation needed to reconstruct provenance.

---

## 20. Native/OCR reconciliation

This is a critical RAG rule.

For `MIXED` pages, AstraIndexator SHALL NOT simply concatenate:

```text
nativeText + ocrText
```

Reconciliation SHOULD compare spatial/textual overlap using available provenance:

```text
native bbox/region
OCR bbox/region
normalized text similarity
reading-order neighborhood
```

Outcomes:

```text
KEEP_NATIVE
KEEP_OCR
KEEP_BOTH_DISTINCT_REGIONS
MERGE_COMPLEMENTARY
DROP_DUPLICATE_OCR
FLAG_CONFLICT
```

Baseline preference:

- high-quality native text wins for the same region;
- OCR fills regions where native extraction is absent/low quality;
- distinct text-bearing image regions are preserved;
- conflicting evidence is flagged rather than silently concatenated.

---

## 21. Reading order after OCR

OCR block order returned by an engine is not automatically canonical document order.

Accepted OCR observations SHALL be integrated into TZ-05/TZ-09 layout using:

```text
page number
bbox
column/region grouping
native neighbors
caption/image relationships
```

Final logical order MUST remain deterministic for the same input and processing versions.

---

## 22. Tables and forms

OCR may recover text from image-based tables/forms, but plain OCR lines are not automatically a trustworthy table structure.

Baseline:

```text
OCR text/bboxes
→ preserve observations
→ optional table/layout reconstruction capability
```

If table structure cannot be reliably reconstructed, AstraIndexator SHOULD retain a region/image-derived element with ordered OCR text and provenance instead of inventing cell boundaries.

Advanced table-structure recognition may be introduced as a separately versioned OCR/layout capability.

---

## 23. Diagrams and non-text visual semantics

TZ-06 baseline recognizes textual content in images.

It does NOT claim to semantically understand arbitrary charts, diagrams, photos or visual relationships.

A future VLM/vision-captioning capability, if introduced, MUST be a separately identified model/profile and SHALL NOT masquerade as OCR output.

---

## 24. Deduplication of repeated visual content

For repeated embedded images such as logos/watermarks, the implementation SHOULD compute/use a stable image-content fingerprint when practical.

Within one document version:

```text
same image hash
+ same OCR processing fingerprint
→ OCR result may be reused
```

Reuse MUST preserve each occurrence's page/region provenance if the recognized text is projected into canonical elements.

---

## 25. Resource governance

Required configurable guards:

```text
ocr_max_pages_per_job
ocr_max_pixels_per_page
ocr_max_total_pixels_per_job
ocr_max_concurrent_pages_per_worker
ocr_max_concurrent_jobs_per_worker
ocr_timeout_per_page
ocr_timeout_per_job
ocr_memory_soft_limit
ocr_memory_hard_limit
ocr_max_derived_bytes
```

CPU and GPU workers MAY have different bounds.

Admission and execution SHALL fail predictably before pod-level OOM whenever possible.

---

## 26. CPU profile

A CPU profile is required as a production baseline for portability.

Characteristics:

```text
bounded concurrency
local pinned model bundle
no runtime download
page-at-a-time processing
memory guard
hard timeouts
```

The reference `llm-indexator` provides a PaddleOCR CPU `ru/kk/en` bundle concept that can seed implementation experiments, but AstraIndexator shall validate quality/performance independently in TZ-17.

---

## 27. GPU profile

GPU support is optional but architecturally supported.

GPU profile SHALL define:

```text
compatible model artifact
CUDA/runtime version
GPU memory budget
worker concurrency
batching policy if used
startup capability check
fallback policy
```

Production fallback from GPU to CPU MUST be explicit. A hidden fallback can radically change latency and capacity and is therefore forbidden.

---

## 28. Model cache and readiness

Worker readiness SHALL fail when OCR is required for the worker profile and:

```text
model bundle missing
manifest invalid
checksum mismatch
required file missing
engine runtime unavailable
configured device unavailable
```

If deployment separates parser-only and OCR workers, parser-only readiness need not depend on OCR models.

This distinction SHOULD be preserved in TZ-18 deployment topology.

---

## 29. Failure taxonomy

Canonical OCR errors SHOULD include:

```text
OCR_REQUIRED_BUT_DISABLED
OCR_PROFILE_UNAVAILABLE
OCR_MODEL_MISSING
OCR_MODEL_MANIFEST_INVALID
OCR_MODEL_CHECKSUM_MISMATCH
OCR_ENGINE_UNAVAILABLE
OCR_DEVICE_UNAVAILABLE
OCR_RENDER_FAILED
OCR_PAGE_LIMIT_EXCEEDED
OCR_PIXEL_LIMIT_EXCEEDED
OCR_TOTAL_PIXEL_LIMIT_EXCEEDED
OCR_MEMORY_LIMIT_EXCEEDED
OCR_TIMEOUT
OCR_ENGINE_FAILED
OCR_OUTPUT_INVALID
OCR_LOW_CONFIDENCE
OCR_LANGUAGE_UNSUPPORTED
OCR_RECONCILIATION_FAILED
OWNERSHIP_LOST
```

TZ-13 determines transient/permanent/resource classification and retry budget.

---

## 30. Retry and recovery

OCR can be expensive, so retry must be stage-aware.

Rules:

1. deterministic resource-limit failures are not blindly retried;
2. transient device/runtime failure may be retried within budget;
3. lease loss stops authoritative progress;
4. accepted OCR outputs become reusable only when incorporated into a valid TZ-03 prepared artifact/checkpoint;
5. local page renders are attempt-scoped and disposable;
6. model version changes require a new processing fingerprint/reprocess decision.

A worker crash after page 90 of 100 MUST NOT imply that another worker can trust page 90 local memory/files. Durable reuse is based on published canonical/prepared artifacts, not local OCR scratch space.

---

## 31. Partial OCR checkpoint strategy

Baseline 1.0 SHOULD avoid making every page a separate durable object merely for checkpointing.

For very large OCR jobs, implementations MAY publish bounded intermediate canonical parts according to TZ-03, but publication MUST remain manifest/fencing-safe.

The chosen checkpoint granularity SHALL balance:

```text
recompute cost
object count
publication complexity
recovery latency
```

No intermediate checkpoint may bypass TZ-02 ownership fencing.

---

## 32. Processing fingerprint

OCR-affecting fields SHALL participate in processing identity, including at least:

```text
source SHA-256
parser version/profile
OCR mode
OCR engine
OCR modelId
OCR artifactRevision/checksum
OCR preprocessingVersion
OCR decision policy version
normalizer version
splitter version
```

Changing the OCR model without changing processing identity is forbidden.

---

## 33. Observability

Structured diagnostics SHALL answer:

```text
why was OCR selected?
which pages/images were processed?
which engine/model/version ran?
what languages were configured?
how many pixels/pages were processed?
what was average/low confidence?
how much native/OCR overlap was removed?
how long did render and recognition take?
what resource guard fired?
```

Metrics SHOULD include:

```text
ocr_candidates_total{type,reason}
ocr_decisions_total{decision}
ocr_pages_processed_total
ocr_images_processed_total
ocr_pixels_processed_total
ocr_duration_seconds
ocr_page_duration_seconds
ocr_blocks_total
ocr_low_confidence_blocks_total
ocr_duplicate_blocks_dropped_total
ocr_failures_total{reason}
ocr_model_info{model_id,revision,device}
ocr_memory_high_water_bytes
```

Logs MUST NOT contain full recognized document text by default.

---

## 34. Security and privacy

1. OCR models come only from approved model supply.
2. Runtime internet model download is disabled.
3. Model registry credentials are secrets and never logged.
4. Temporary rendered pages inherit restrictive workspace permissions.
5. Derived images are deleted after use unless TZ-03 retention explicitly requires them.
6. OCR text is treated with the same access scope as the source document.
7. OCR SHALL NOT reduce/classify access zone based on recognized content.
8. Sensitive recognized content MUST NOT be emitted into routine logs/metrics.

---

## 35. Model rollout

Model upgrades are controlled configuration/deployment events.

Required rollout semantics:

```text
new artifact published to Nexus
→ manifest/checksum approved
→ preload/cache
→ verification corpus
→ canary profile
→ rollout
```

The model revision MUST be observable in every processing result.

Rollback SHALL restore a previously pinned artifact/profile without requiring changes to document contracts.

Reindexing existing documents after a model upgrade is a TZ-12 lifecycle decision, not an automatic side effect of deploying the new model.

---

## 36. Reference reuse from llm-indexator

The following concepts are explicitly suitable for adaptation:

```text
OcrEngine abstraction
OcrBlock{text,bbox,confidence,page,order}
OcrResult engine/model metadata
CPU/GPU OCR profiles
ru/kk/en model capability
model_manifest.json
required-file checks
runtime-download prohibition
memory guard
timeout guard
low-signal/OCR-required diagnostics
```

The following legacy behavior SHALL NOT be copied unchanged:

```text
whole-document OCR based only on a single useful_text_ratio
loading all rendered PDF pages into memory
extension-only OCR dispatch
embedding/vector/search ownership
Celery-specific queue semantics as an architectural requirement
```

AstraIndexator uses PostgreSQL TZ-02 coordination and page/region-aware OCR decisions.

---

## 37. Testing and verification requirements

TZ-17 SHALL provide executable evidence for at least:

1. native-text PDF where OCR is skipped;
2. fully scanned PDF where OCR is selected;
3. mixed PDF where only missing regions/pages are enriched;
4. standalone PNG/JPEG OCR;
5. multi-page TIFF bounded processing;
6. Russian text recognition;
7. Kazakh Cyrillic recognition including Kazakh-specific characters;
8. English text recognition;
9. mixed `ru/kk/en` page;
10. native/OCR duplicate removal;
11. distinct native and image text both preserved;
12. repeated logo OCR deduplicated/suppressed;
13. low-confidence block handling;
14. page pixel limit;
15. total job pixel limit;
16. page/job timeout;
17. memory guard behavior;
18. model checksum mismatch -> readiness/failure;
19. missing local model -> no internet download/fallback;
20. CPU profile smoke;
21. GPU profile smoke when supported;
22. crash/reclaim during OCR;
23. model upgrade changes processing fingerprint;
24. parser-only worker independence from OCR model when deployment topology allows;
25. no recognized document text leaked into standard logs.

Quality verification SHALL use a versioned golden corpus, not only synthetic unit tests.

---

## 38. RAG-quality acceptance

OCR success is not merely “engine returned text”.

Corpus-level evaluation SHOULD measure at least:

```text
character/word error on representative ru/kk/en pages
heading preservation
numeric/identifier preservation
table/form text preservation
reading order
native/OCR duplicate rate
false OCR on decorative images
retrieval impact after TZ-08/TZ-11
```

Kazakh-specific characters MUST be represented in the golden corpus.

A model/profile that is operationally faster but materially degrades retrieval quality SHALL NOT be promoted solely on throughput.

---

## 39. Acceptance criteria

TZ-06 is satisfied when:

- **AC-01:** OCR is conditional and defaults to `OCR_IF_NEEDED`;
- **AC-02:** decisions can be made per page/image/region;
- **AC-03:** mixed PDFs do not automatically trigger whole-document duplicate OCR;
- **AC-04:** native/OCR overlap is reconciled deterministically;
- **AC-05:** block provenance includes page/geometry/engine/model/confidence evidence;
- **AC-06:** baseline multilingual capability covers `ru/kk/en`;
- **AC-07:** no translation/transliteration occurs in OCR;
- **AC-08:** page rendering is bounded and stream/page-wise rather than full-document raster accumulation;
- **AC-09:** CPU profile is supported and resource-bounded;
- **AC-10:** GPU profile, when enabled, is explicit and capability-checked;
- **AC-11:** runtime model download and silent fallback are forbidden;
- **AC-12:** Nexus-delivered artifacts are pinned by revision/checksum via TZ-15;
- **AC-13:** missing/invalid model supply fails closed;
- **AC-14:** OCR model/preprocessing/policy versions participate in processing fingerprint;
- **AC-15:** repeated visual content can be deduplicated without losing occurrence provenance;
- **AC-16:** low confidence remains explicit evidence;
- **AC-17:** expensive deterministic failures do not enter infinite retry;
- **AC-18:** crash/reclaim obeys TZ-02 fencing and TZ-13 recovery;
- **AC-19:** observability exposes quality/resource/model metadata without content leakage;
- **AC-20:** TZ-17 includes multilingual, mixed-mode, resource, recovery and RAG-quality proof.

---

## 40. Recommended implementation decomposition

```text
OcrDecisionService
OcrCandidate
OcrDecision
OcrProfileRegistry
OcrModelBundle
OcrEngine
PaddleOcrEngine (initial candidate)
PdfPageRenderer
ImagePreprocessor
OcrObservation
OcrResult
NativeOcrReconciler
OcrQualityEvaluator
OcrResourceGuard
OcrProvenanceMapper
```

Model acquisition/cache remains behind TZ-15 interfaces.

---

## 41. Final invariant

The canonical OCR boundary is:

```text
TZ-05 structural/native parse
        ↓
page/image/region evidence
        ↓
versioned OCR decision policy
        ↓
verified local Nexus-supplied model
        ↓
bounded rendering + recognition
        ↓
text + bbox + confidence + model provenance
        ↓
native/OCR reconciliation
        ↓
canonical DocumentElement[]
```

OCR enriches missing visual text while preserving source structure and provenance; it does not replace the parser, duplicate native content, own embeddings, or silently download/fallback to unapproved models.
