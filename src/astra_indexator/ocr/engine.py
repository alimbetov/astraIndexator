from __future__ import annotations

import json
import multiprocessing as mp
import queue
from pathlib import Path
from typing import Protocol

from .bundle import VerifiedOcrModelBundle, verify_local_bundle
from .model import OcrModelIdentity, OcrObservation, OcrRequest, SourceGeometry


class OcrEngine(Protocol):
    @property
    def model_identity(self) -> OcrModelIdentity: ...

    def recognize(self, request: OcrRequest) -> tuple[OcrObservation, ...]: ...


class PaddleOcrEngine:
    """Direct PaddleOCR v3 adapter using only verified local model directories."""

    def __init__(self, bundle: VerifiedOcrModelBundle, *, device: str = "cpu"):
        if bundle.identity.engine.lower() != "paddleocr":
            raise ValueError("OCR bundle engine must be paddleocr")
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("PaddleOCR runtime is not installed; install astra-indexator[ocr]") from exc
        self._identity = bundle.identity
        self._ocr = PaddleOCR(
            text_detection_model_dir=str(bundle.text_detection_model_dir),
            text_recognition_model_dir=str(bundle.text_recognition_model_dir),
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            device=device,
        )

    @property
    def model_identity(self) -> OcrModelIdentity:
        return self._identity

    @staticmethod
    def _payload(result) -> dict:
        if isinstance(result, dict):
            return result
        value = getattr(result, "json", None)
        if callable(value):
            value = value()
        if isinstance(value, str):
            return json.loads(value)
        if isinstance(value, dict):
            return value
        to_dict = getattr(result, "to_dict", None)
        if callable(to_dict):
            return to_dict()
        raise RuntimeError("OCR_OUTPUT_INVALID")

    def recognize(self, request: OcrRequest) -> tuple[OcrObservation, ...]:
        observations: list[OcrObservation] = []
        results = self._ocr.predict(str(request.image_path))
        block_order = 0
        for result in results:
            payload = self._payload(result)
            nested = payload.get("res", {}) if isinstance(payload.get("res"), dict) else {}
            texts = payload.get("rec_texts") or nested.get("rec_texts") or []
            scores = payload.get("rec_scores") or nested.get("rec_scores") or []
            polys = payload.get("rec_polys") or payload.get("dt_polys") or nested.get("rec_polys") or []
            for index, text in enumerate(texts):
                text = str(text).strip()
                if not text:
                    continue
                confidence = float(scores[index]) if index < len(scores) else 0.0
                geometry = None
                if index < len(polys) and polys[index]:
                    points = polys[index]
                    xs = [float(point[0]) for point in points]
                    ys = [float(point[1]) for point in points]
                    geometry = SourceGeometry(page_number=request.page_number, x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys), coordinate_space="ocr-image-pixels")
                observations.append(OcrObservation(text, confidence, block_order, request.candidate_id,
                                                   request.source_element_id, request.page_number, geometry, self._identity))
                block_order += 1
        return tuple(observations)


def _isolated_worker(bundle_root: str, device: str, requests: mp.Queue, responses: mp.Queue) -> None:  # pragma: no cover
    try:
        engine = PaddleOcrEngine(verify_local_bundle(Path(bundle_root)), device=device)
        responses.put(("READY", None))
        while True:
            request = requests.get()
            if request is None:
                return
            try:
                responses.put(("OK", engine.recognize(request)))
            except BaseException as exc:
                responses.put(("ERR", repr(exc)))
    except BaseException as exc:
        responses.put(("INIT_ERR", repr(exc)))


class IsolatedPaddleOcrEngine:
    """Production CPU/GPU wrapper with killable OCR worker process.

    A candidate timeout terminates the OCR process, so the timeout is not merely
    a caller-side ThreadPool timeout. The process is lazily restarted for the next request.
    """

    def __init__(self, bundle: VerifiedOcrModelBundle, *, device: str = "cpu", startup_timeout_seconds: float = 120.0):
        self.bundle = bundle
        self.device = device
        self.startup_timeout_seconds = startup_timeout_seconds
        self._identity = bundle.identity
        self._ctx = mp.get_context("spawn")
        self._process = None
        self._requests = None
        self._responses = None

    @property
    def model_identity(self) -> OcrModelIdentity:
        return self._identity

    def _stop(self) -> None:
        if self._process is not None and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=5)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=5)
        self._process = self._requests = self._responses = None

    def _ensure_started(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        self._requests = self._ctx.Queue()
        self._responses = self._ctx.Queue()
        self._process = self._ctx.Process(target=_isolated_worker,
                                          args=(str(self.bundle.root), self.device, self._requests, self._responses),
                                          daemon=True)
        self._process.start()
        try:
            status, payload = self._responses.get(timeout=self.startup_timeout_seconds)
        except queue.Empty as exc:
            self._stop()
            raise TimeoutError("OCR_ENGINE_STARTUP_TIMEOUT") from exc
        if status != "READY":
            self._stop()
            raise RuntimeError(f"OCR_ENGINE_UNAVAILABLE:{payload}")

    def recognize(self, request: OcrRequest) -> tuple[OcrObservation, ...]:
        self._ensure_started()
        self._requests.put(request)
        try:
            status, payload = self._responses.get(timeout=request.profile.timeout_per_candidate_seconds)
        except queue.Empty as exc:
            self._stop()
            raise TimeoutError("OCR_TIMEOUT") from exc
        if status == "ERR":
            raise RuntimeError(f"OCR_ENGINE_FAILED:{payload}")
        if status != "OK":
            raise RuntimeError(f"OCR_OUTPUT_INVALID:{status}")
        return tuple(payload)

    def close(self) -> None:
        if self._requests is not None:
            try:
                self._requests.put_nowait(None)
            except Exception:
                pass
        self._stop()
