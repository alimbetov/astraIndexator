from __future__ import annotations

import json
from typing import Protocol

from .bundle import VerifiedOcrModelBundle
from .model import OcrModelIdentity, OcrObservation, OcrRequest, SourceGeometry


class OcrEngine(Protocol):
    @property
    def model_identity(self) -> OcrModelIdentity: ...

    def recognize(self, request: OcrRequest) -> tuple[OcrObservation, ...]: ...


class PaddleOcrEngine:
    """PaddleOCR v3 adapter using only verified local model directories.

    Import and initialization are lazy so the base package/CI does not require the
    heavy Paddle runtime. Production installs the `ocr` optional dependency.
    """

    def __init__(self, bundle: VerifiedOcrModelBundle, *, device: str = "cpu"):
        if bundle.identity.engine.lower() != "paddleocr":
            raise ValueError("OCR bundle engine must be paddleocr")
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:  # pragma: no cover - depends on production OCR image
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
        raise RuntimeError("unsupported PaddleOCR result payload")

    def recognize(self, request: OcrRequest) -> tuple[OcrObservation, ...]:
        observations: list[OcrObservation] = []
        results = self._ocr.predict(str(request.image_path))
        block_order = 0
        for result in results:
            payload = self._payload(result)
            texts = payload.get("rec_texts") or payload.get("res", {}).get("rec_texts") or []
            scores = payload.get("rec_scores") or payload.get("res", {}).get("rec_scores") or []
            polys = payload.get("rec_polys") or payload.get("dt_polys") or payload.get("res", {}).get("rec_polys") or []
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
                    geometry = SourceGeometry(
                        page_number=request.page_number,
                        x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys),
                        coordinate_space="ocr-image-pixels",
                    )
                observations.append(OcrObservation(
                    text=text,
                    confidence=confidence,
                    block_order=block_order,
                    candidate_id=request.candidate_id,
                    source_element_id=request.source_element_id,
                    page_number=request.page_number,
                    geometry=geometry,
                    model=self._identity,
                ))
                block_order += 1
        return tuple(observations)
