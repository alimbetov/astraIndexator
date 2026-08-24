from __future__ import annotations

import hashlib
import json
import sys
import types

import pytest

from astra_indexator.ocr import PaddleOnnxOcrEngine, verify_local_bundle


def _bundle(tmp_path, *, provider="CPUExecutionProvider", include_rec_onnx=True):
    root = tmp_path / "bundle"
    det = root / "det"
    rec = root / "rec"
    det.mkdir(parents=True)
    rec.mkdir(parents=True)
    det_file = det / "inference.onnx"
    rec_file = rec / "inference.onnx"
    det_file.write_bytes(b"det-onnx")
    if include_rec_onnx:
        rec_file.write_bytes(b"rec-onnx")
    else:
        rec_file = rec / "model.bin"
        rec_file.write_bytes(b"rec-not-onnx")
    dictionary = rec / "character_dict.txt"
    dictionary.write_text("Ә\nҒ\nҚ\nҢ\nӨ\nҰ\nҮ\nҺ\nІ\nA\n", encoding="utf-8")
    files = []
    for path in (det_file, rec_file, dictionary):
        files.append({
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    manifest = {
        "schemaVersion": "astra-indexator-ocr-model-v1",
        "modelKind": "OCR",
        "modelId": "ppocrv5-mobile-cyrillic-onnx-fp32",
        "engine": "paddleocr",
        "engineVersion": "3.5",
        "inferenceEngine": "onnxruntime",
        "executionProvider": provider,
        "precision": "fp32",
        "artifactRevision": "candidate-a-test",
        "languages": ["kk", "ru", "en"],
        "textDetectionModelDir": "det",
        "textRecognitionModelDir": "rec",
        "files": files,
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_candidate_a_bundle_requires_onnx_detector_and_recognizer(tmp_path):
    bundle = verify_local_bundle(_bundle(tmp_path))
    assert bundle.inference_engine == "onnxruntime"
    assert bundle.execution_provider == "CPUExecutionProvider"
    assert bundle.precision == "fp32"
    assert bundle.identity.languages == ("kk", "ru", "en")


def test_onnx_bundle_fails_closed_when_recognizer_is_not_onnx(tmp_path):
    with pytest.raises(RuntimeError, match="OCR_ONNX_MODEL_MISSING:textRecognitionModelDir"):
        verify_local_bundle(_bundle(tmp_path, include_rec_onnx=False))


def test_cpu_onnx_engine_uses_only_verified_local_directories(tmp_path, monkeypatch):
    bundle = verify_local_bundle(_bundle(tmp_path))
    captured = {}

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def predict(self, _path):
            return []

    fake_paddleocr = types.ModuleType("paddleocr")
    fake_paddleocr.PaddleOCR = FakePaddleOCR
    fake_ort = types.ModuleType("onnxruntime")
    fake_ort.get_available_providers = lambda: ["CPUExecutionProvider"]
    monkeypatch.setitem(sys.modules, "paddleocr", fake_paddleocr)
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)

    engine = PaddleOnnxOcrEngine(bundle, device="cpu")
    assert engine.model_identity.model_id == "ppocrv5-mobile-cyrillic-onnx-fp32"
    assert captured["engine"] == "onnxruntime"
    assert captured["device"] == "cpu"
    assert captured["text_detection_model_dir"] == str(bundle.text_detection_model_dir)
    assert captured["text_recognition_model_dir"] == str(bundle.text_recognition_model_dir)
    assert "text_detection_model_name" not in captured
    assert "text_recognition_model_name" not in captured


def test_cpu_profile_does_not_silently_fallback_when_provider_missing(tmp_path, monkeypatch):
    bundle = verify_local_bundle(_bundle(tmp_path))
    fake_ort = types.ModuleType("onnxruntime")
    fake_ort.get_available_providers = lambda: ["AzureExecutionProvider"]
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
    with pytest.raises(RuntimeError, match="OCR_ONNX_PROVIDER_UNAVAILABLE:CPUExecutionProvider"):
        PaddleOnnxOcrEngine(bundle, device="cpu")


def test_cuda_profile_requires_gpu_device_and_cuda_provider(tmp_path, monkeypatch):
    bundle = verify_local_bundle(_bundle(tmp_path, provider="CUDAExecutionProvider"))
    fake_ort = types.ModuleType("onnxruntime")
    fake_ort.get_available_providers = lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"]
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)
    with pytest.raises(RuntimeError, match="OCR_ONNX_DEVICE_PROVIDER_MISMATCH:CUDAExecutionProvider"):
        PaddleOnnxOcrEngine(bundle, device="cpu")
