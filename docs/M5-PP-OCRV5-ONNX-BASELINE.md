# M5 — PP-OCRv5 ONNX Working Baseline

## Status

This document defines the first working OCR runtime profile selected from TZ-15A for implementation and TZ-17D v4 qualification.

It does **not** promote the model to `QUALIFIED` or `APPROVED`.

## Baseline candidate

```text
PP-OCRv5 mobile detector ONNX
+
cyrillic PP-OCRv5 mobile recognizer ONNX
+
ONNX Runtime
+
CPUExecutionProvider
```

Candidate identity:

```text
TZ-15A-CANDIDATE-A
```

The Cyrillic PP-OCRv5 recognition family is the current desk-review baseline because the upstream model family declares Russian, Kazakh and English support. Actual Kazakh OCR quality remains a TZ-17D v4 requirement.

## Why CPU first

CPU is the mandatory portable profile. GPU/CUDA is a separate qualified execution profile and MUST NOT be treated as a silent fallback or automatic equivalent artifact.

## Runtime installation

CPU profile:

```bash
pip install -e '.[ocr-onnx-cpu]'
```

GPU profile for later v4 qualification:

```bash
pip install -e '.[ocr-onnx-gpu]'
```

The GPU image/environment must expose `CUDAExecutionProvider`; otherwise readiness fails.

## Model acquisition boundary

AstraIndexator does not download models during document processing.

Until `nexus.astrabase.asia` contains an approved bundle, official upstream model directories are downloaded manually or by a qualification job outside the worker runtime.

The worker receives only a verified local bundle.

## Building Candidate A bundle

After obtaining the official detector/recognizer ONNX directories and Cyrillic dictionary:

```bash
python scripts/build_ppocrv5_candidate_a_bundle.py \
  --det-dir /qualification/PP-OCRv5_mobile_det_onnx \
  --rec-dir /qualification/cyrillic_PP-OCRv5_mobile_rec_onnx \
  --dictionary /qualification/ppocrv5_cyrillic_dict.txt \
  --output /models/ppocrv5-candidate-a/2026.08.candidate1 \
  --artifact-revision 2026.08.candidate1
```

The builder:

- requires ONNX files in both detector and recognizer directories;
- verifies the dictionary contains `Ә Ғ Қ Ң Ө Ұ Ү Һ І` in upper/lower case;
- copies the immutable local model directories;
- creates file SHA-256 values;
- creates an AstraIndexator OCR manifest;
- marks the artifact as `CANDIDATE`.

## Runtime manifest fields

Candidate A uses:

```json
{
  "engine": "paddleocr",
  "inferenceEngine": "onnxruntime",
  "executionProvider": "CPUExecutionProvider",
  "precision": "fp32",
  "languages": ["kk", "ru", "en"]
}
```

`verify_local_bundle()` fails closed when:

- detector/recognizer ONNX is missing;
- execution provider value is unsupported;
- precision is unsupported;
- any file checksum changes;
- any required directory/file is missing.

## Engine behavior

`PaddleOnnxOcrEngine` passes only local model directories to PaddleOCR and selects `engine="onnxruntime"`.

Provider preflight is explicit:

```text
CPU profile + CPUExecutionProvider unavailable
→ OCR_ONNX_PROVIDER_UNAVAILABLE

CUDA profile + CUDAExecutionProvider unavailable
→ OCR_ONNX_PROVIDER_UNAVAILABLE

CUDA provider + device=cpu
→ OCR_ONNX_DEVICE_PROVIDER_MISMATCH
```

No provider fallback is permitted under the same processing identity.

## What this baseline proves

The implementation proves:

- AstraIndexator can select ONNX Runtime through the existing M5 abstraction;
- model input remains a verified local bundle;
- CPU provider readiness is fail-closed;
- GPU provider selection is explicit;
- the runtime remains compatible with M5 process-isolation/timeout/reconciliation contracts;
- Candidate A can now be consumed by TZ-17D v4.

## What it does not prove

It does not yet prove:

- production Kazakh accuracy;
- CER/WER thresholds;
- protected glyph/token accuracy;
- mobile detector superiority;
- CPU/GPU semantic equivalence;
- FP16/INT8 equivalence;
- embedded-image quality;
- throughput/RAM/VRAM production targets.

Those remain TZ-17D v4 evidence gates.
