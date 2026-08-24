# TZ-17D — OCR-Derived Multilingual Corpus v4

## Status

- System: AstraIndexator 1.0
- Parent: TZ-17 Testing & Verification
- Related: TZ-05, TZ-06, TZ-07, TZ-08, TZ-09, TZ-14, TZ-15, TZ-17A/B/C
- Status: Design baseline; implementation not started
- Primary languages: kk, ru, en, mixed kk/ru/en

## 1. Purpose

v4 proves the real OCR-derived knowledge path rather than isolated sentence splitting:

```text
scanned/raster/embedded visual source
  -> M4 OCR candidate
  -> M5 render/extract/OCR
  -> native/OCR reconciliation
  -> canonical OCR-derived DocumentElement[]
  -> M6 normalization
  -> sentence boundaries
  -> LogicalFragment[]
```

The benchmark must identify which layer first corrupts knowledge: OCR recognition, reading order, reconciliation, normalization, sentence segmentation, or fragmentation.

## 2. Core principles

1. Gold text is source/human-derived, never copied from current OCR output.
2. OCR quality and linguistic quality are scored separately.
3. Original script is authoritative; translation/transliteration is forbidden.
4. Critical enterprise tokens receive exact-match metrics.
5. Provenance is part of correctness.
6. Blank/decorative inputs must not generate searchable hallucinated text.
7. Every run records OCR engine/model/bundle/profile/preprocessing/reconciliation identity.
8. Runtime model download is forbidden.
9. Corpus fixtures are immutable after merge; annotation changes require versioning.
10. Failing gates are diagnosed before thresholds are changed.

## 3. Required language scope

Mandatory baseline:

```text
kk
ru
en
mixed kk/ru/en
```

Kazakh-specific characters are mandatory in upper/lower case:

```text
Ә ә Ғ ғ Қ қ Ң ң Ө ө Ұ ұ Ү ү Һ һ І і
```

Russian `Ё/ё` is mandatory.

## 4. Corpus tiers

### Tier A — clean rendered reference

High-quality digital render used to establish upper-bound OCR quality.

### Tier B — deterministic synthetic degradation

Versioned degradation profiles shall include representative combinations of:

```text
rotation/skew
perspective distortion
blur
low contrast
uneven illumination/shadow
JPEG compression
low effective DPI
scanner noise
background texture
partial crop/edge proximity
stamp/seal overlap
thin/faded text
```

Every transformation must be reproducible from fixture ID + degradation profile/version + deterministic seed.

### Tier C — representative real scan/photo

Synthetic, non-sensitive, licensed, or correctly redacted examples of:

```text
office scanner PDF
phone photo
grayscale copy
photocopy degradation
skewed page
mixed native/scanned PDF
```

Production/customer documents must not be committed without explicit approval.

## 5. Required content domains

```text
general prose
enterprise prose
legal/regulatory
money/currency
dates/times
contract/document identifiers
names/initials
Access Zone codes
API/Java/package/method identifiers
versions/IP/UUID/hash
bilingual/trilingual text
tables
forms/labels-values
page headers/footers
```

## 6. OCR confusion taxonomy

The evaluator must classify substitutions, not only report aggregate CER.

### Latin/Cyrillic homoglyphs

```text
0 <-> O / О
1 <-> I / l / І
C <-> С
P <-> Р
H <-> Н
K <-> К
M <-> М
T <-> Т
X <-> Х
Y <-> У
```

### Kazakh-specific confusions

```text
Ә <-> А
Ғ <-> Г
Қ <-> К
Ң <-> Н
Ө <-> О
Ұ / Ү / У
Һ <-> Н
І <-> I / l / 1
```

This taxonomy is for measurement only; it does not authorize auto-correction.

### Russian-specific confusions

```text
Ё <-> Е
Й <-> И
Ь/Ъ loss
```

### Punctuation/symbol loss

At minimum:

```text
. , : ; ! ? …
- – —
( ) [ ]
« » “ ”
№
₸ $ € ₽
% / + =
@ # _
```

## 7. Line-wrap and reading-order scenarios

The corpus must distinguish physical wrapping from semantic boundaries.

Positive join example:

```text
The obligation shall remain
in force until termination.
```

Negative join example:

```text
Payment terms

Liability terms
```

Required cases:

```text
hyphenated physical wrap
true lexical hyphen
multi-column OCR
label beside value
header/footer interference
table row ordering
```

Recognition accuracy and reading-order accuracy are separate evidence dimensions.

## 8. Embedded Visual Content Corpus

Images inside PDF, DOCX and PPTX are a mandatory v4 class and must not be treated as standalone images detached from their parent document.

### 8.1 Required source classes

```text
PDF embedded image / image region
DOCX inline or anchored image
PPTX picture/shape image
```

Where M4 emits an `EMBEDDED_IMAGE` or `REGION` OCR candidate, v4 must preserve the parent document identity and source locator.

### 8.2 Required embedded-visual categories

The corpus shall contain at least:

```text
TEXT_SCREENSHOT      screenshot containing useful text
SCANNED_PARAGRAPH    image of a paragraph/page fragment
SCANNED_TABLE        raster table inside office/PDF
FORM_OR_LABELS       labels/values in a form
DIAGRAM_TEXT         diagram labels where textual OCR is useful
STAMP_TEXT           stamp/seal with readable text, when relevant
PHOTO_TEXT           sign/label text inside a photo, where explicitly eligible
LOGO                 repeated decorative logo
WATERMARK            repeated watermark
DECORATIVE_IMAGE     no useful text
```

### 8.3 Parent provenance contract

Each accepted OCR-derived element from an embedded visual must retain enough evidence to trace it back to the exact occurrence.

Conceptual source locators:

```text
PDF:  documentId + pageNumber + sourceElementId + bbox/imageRef
DOCX: documentId + paragraphIndex + drawingIndex + sourceElementId
PPTX: documentId + slideNumber + shapeId + sourceElementId
```

The benchmark must fail if recognized text survives but this parent provenance is lost.

### 8.4 Context association

The benchmark must test that OCR text from an embedded image remains structurally associated with the image and nearby canonical context.

Examples:

```text
caption -> image -> OCR_TEXT
paragraph -> embedded screenshot -> OCR_TEXT
slide title -> picture -> OCR_TEXT
```

Nearby caption/heading may be used as synthetic RAG context later, but it must not be falsified as source text inside the image.

### 8.5 OCR eligibility

Not every embedded image should be OCRed. v4 shall cover positive and negative eligibility cases based on evidence such as:

```text
minimum dimensions/pixels
page-relative area
text-bearing likelihood
repetition/image hash
native text already covering same region
caption/context proximity
logo/decorative likelihood
```

### 8.6 Repeated image deduplication

Required scenario:

```text
same logo/image bytes repeated on N pages/slides
+ same OCR processing fingerprint
-> OCR engine invoked once where reuse applies
-> each retained occurrence keeps its own page/slide/region provenance
```

Repeated logo/watermark text must not create repeated searchable pollution.

### 8.7 Embedded table images

For scanned/raster tables inside PDF/DOCX/PPTX, baseline v4 scores:

1. text recovery;
2. row/reading-order preservation when evidence exists;
3. no invented values/cells;
4. region provenance;
5. preservation of critical numbers/currencies/codes.

Plain OCR lines must not be promoted to invented canonical cell boundaries unless a separately versioned table-structure capability proves them.

### 8.8 Diagrams and photos

v4 baseline tests OCR of visible textual labels only. It does not claim semantic understanding of arbitrary diagrams/charts/photos.

A future VLM/captioning pipeline must have a separate model/profile and must not masquerade as OCR.

## 9. Mixed native/OCR scenarios

Mandatory cases:

```text
native paragraph + scanned signature label
native body + embedded scanned table
native page + repeated logo
native text overlapping OCR candidate region
same text available natively and via OCR
```

Assertions:

```text
duplicate OCR suppressed
distinct OCR-only region preserved
high-quality native text wins same region
repeated logo/watermark does not pollute retrieval
retained OCR occurrence has correct provenance
```

## 10. Multi-layer gold contract

Each case shall expose separate gold layers.

### Layer 1 — visual source truth

Exact transcription of relevant visible text.

### Layer 2 — protected spans

Critical exact spans, for example:

```text
ACCESS_ZONE_CODE: 0000
AMOUNT: 1 250 000,50 ₸
API_IDENTIFIER: /api/v1/retrieve
CONTRACT_ID: AB-2026/08-24
```

### Layer 3 — normalized gold

Expected meaning-preserving M6 normalized representation.

### Layer 4 — sentence gold

Exact expected sentences and deterministic source-derived boundaries.

### Layer 5 — structural/provenance assertions

Examples:

```text
page/slide/paragraph locator
sourceElementId
normalized bbox validity
candidate relationship
OCR engine/model identity
expected region/order relation
```

### Layer 6 — selected fragment assertions

Only where stable and justified:

```text
critical protected span remains unsplit
hard guards respected
deterministic fragment IDs for same processing fingerprint
```

Exact fragment boundaries should not be over-specified where TZ-08 permits multiple valid structural groupings.

## 11. Proposed fixture schema

Conceptually:

```json
{
  "schemaVersion": "astra-indexator-ocr-corpus-v1",
  "fixtureVersion": "...",
  "id": "v4-kk-embedded-table-001",
  "language": "kk",
  "contentDomain": "enterprise",
  "source": {
    "path": "...",
    "format": "DOCX",
    "sha256": "...",
    "sourceKind": "CLEAN_RENDER|SYNTHETIC_DEGRADED|REAL_SCAN",
    "embeddedVisual": true
  },
  "degradation": {
    "profileId": "scan-degrade-v1",
    "seed": 1234,
    "operations": []
  },
  "gold": {
    "visualText": "...",
    "normalizedText": "...",
    "expectedSentences": [],
    "protectedSpans": [],
    "provenanceAssertions": []
  },
  "requirements": {
    "requiresOcr": true,
    "expectedCandidateScope": "EMBEDDED_IMAGE"
  }
}
```

Exact JSON schema must be finalized during implementation review.

## 12. Required metrics

### OCR recognition

- CER overall/per language/per degradation/per domain;
- WER where word segmentation is reliable;
- exact protected-span accuracy;
- Kazakh critical-character precision/recall/F1 + confusion matrix;
- Latin/Cyrillic homoglyph confusion rate;
- punctuation/symbol preservation.

CER:

```text
CER = (substitutions + deletions + insertions) / reference_characters
```

### M6 normalization

Measure whether errors first appear before or after normalization.

Required assertions:

```text
no unauthorized letter substitution
no translation/transliteration
protected spans preserved unless already wrong in OCR
paragraph semantics preserved
physical wraps joined only with evidence
API/path/currency/date punctuation preserved
```

### Sentence boundaries

Two separate scores are mandatory:

1. `splitter-isolated`: M6 against gold normalized text;
2. `ocr-derived-e2e`: M6 against actual M5->M6 output.

Report TP/FP/FN/precision/recall/F1 separately. Do not collapse them into one number.

### Structural/provenance

At minimum exact assertions for:

```text
expected source occurrence
page/slide/paragraph locator
bbox validity
parent sourceElement relationship
reading-order constraints
```

## 13. Hallucination / false-positive gate

Negative fixtures:

```text
blank page
near-blank scan
logo-only image
watermark-only image
decorative image
no-text embedded image
very low-signal region
```

Hard requirement: no accepted searchable OCR text when no relevant text exists.

## 14. Confidence calibration

Engine confidence is evidence, not truth.

v4 should report quality by configurable confidence buckets and establish whether lower confidence correlates with higher error for the approved RU/KK/EN bundle.

Production hard floors must be justified by corpus evidence, not chosen from model documentation alone.

## 15. Degradation matrix contract

Degradation profiles are manifest-driven and versioned:

```text
profileId
version
operations[]
parameters
seed policy
severity class
```

Recommended severity:

```text
CLEAN
MILD
MODERATE
SEVERE
```

Raw metrics are always reported by severity; severe cases must not be hidden inside a global average.

## 16. Quality-gate strategy

Hard gates independent of OCR percentage:

```text
no runtime model download
model bundle identity present
provenance retained
no searchable hallucination on negative fixtures
no translation/transliteration
Access Zone leading zeroes preserved when OCR is correct
same processing fingerprint is deterministic
```

Recognition thresholds must be established after baseline measurement of the approved Nexus OCR bundle. TZ-17D intentionally does not invent universal CER/WER constants before real corpus evidence exists.

Initial implementation shall first produce a baseline report, then a separate reviewed change shall promote evidence-backed thresholds into release gates.

## 17. Reporting

Every v4 run shall generate a machine-readable report containing at least:

```text
fixture/corpus version
source fixture hash
OCR engine/version
modelId/artifactRevision/bundleSha256
preprocessing/reconciliation versions
per-case status
CER/WER when applicable
protected-span accuracy
critical-character confusion metrics
sentence-boundary metrics
provenance assertions
failure stage classification
```

Recommended failure-stage taxonomy:

```text
SOURCE_RENDER
OCR_RECOGNITION
OCR_ORDER
RECONCILIATION
NORMALIZATION
SENTENCE_BOUNDARY
FRAGMENTATION
PROVENANCE
MODEL_SUPPLY
RESOURCE_LIMIT
```

## 18. CI levels

### PR/public CI

May run:

```text
schema/governance tests
small deterministic synthetic fixtures
fake-engine layer-isolation tests
normalization/sentence gold tests
negative provenance contracts
```

### Internal model-enabled CI

Must run with the approved local Nexus-provisioned OCR bundle:

```text
Tier A clean corpus
Tier B degraded corpus
embedded visual corpus
mixed native/OCR corpus
actual CER/protected-span/critical-character measurements
```

### Release candidate gate

Adds:

```text
Tier C representative real scans/photos
resource measurements
crash/reclaim integration
retrieval impact once AstraVector E2E is available
```

## 19. Dataset governance and privacy

Fixtures must be synthetic, public/licensed, or properly redacted. The repository must not receive real customer/employee confidential documents merely to increase OCR realism.

Each binary fixture should have a manifest with license/provenance status and SHA-256.

## 20. Implementation phases

### V4-P0 — schema/evaluator foundation

- finalize OCR corpus JSON schema;
- layered gold model;
- failure-stage taxonomy;
- CER/edit-distance evaluator;
- protected-span evaluator;
- critical-character/confusion evaluator;
- provenance assertion evaluator;
- reporting format.

### V4-P1 — clean multilingual OCR corpus

- RU/KK/EN/mixed clean renders;
- enterprise/legal/technical content;
- standalone + embedded visual cases;
- baseline approved-model report.

### V4-P2 — deterministic degradation

- versioned degradation generator;
- mild/moderate/severe profiles;
- reproducibility tests;
- per-severity metrics.

### V4-P3 — embedded/mixed document corpus

- PDF/DOCX/PPTX embedded visuals;
- scanned tables/screenshots/forms;
- repeated logo/watermark dedup;
- native/OCR reconciliation cases;
- parent provenance assertions.

### V4-P4 — representative real scans/photos

- approved non-sensitive/controlled real fixtures;
- scanner/photo/photocopy classes;
- corpus review.

### V4-P5 — evidence-backed release thresholds

- collect baseline distributions;
- define reviewed gates by language/content/severity;
- prohibit threshold changes without corpus evidence.

## 21. Acceptance criteria for starting implementation

Implementation may start when all of the following are agreed:

1. layered gold contract is accepted;
2. embedded visual content is first-class in v4;
3. approved OCR model bundle/revision for baseline is identified;
4. corpus privacy/license rules are accepted;
5. synthetic degradation profiles are versioned/deterministic;
6. metrics distinguish OCR recognition from M6 segmentation;
7. no arbitrary CER/WER threshold is invented before baseline measurement;
8. negative hallucination and provenance gates are mandatory.

## 22. Completion boundary

TZ-17D design completion does not mean OCR quality is proven. v4 becomes implementation-complete only after the approved corpus, evaluator and model-enabled runs exist and evidence-backed release thresholds are adopted.
