from __future__ import annotations

from dataclasses import dataclass

from astra_indexator.config import OcrSettings

from .bundle import VerifiedOcrModelBundle, verify_local_bundle


@dataclass(frozen=True, slots=True)
class OcrReadiness:
    ready: bool
    code: str
    bundle: VerifiedOcrModelBundle | None = None


def check_ocr_readiness(settings: OcrSettings, *, require_runtime: bool = True) -> OcrReadiness:
    if not settings.enabled:
        return OcrReadiness(True, "OCR_DISABLED")
    try:
        bundle = verify_local_bundle(settings.model_bundle_root)
    except Exception as exc:
        return OcrReadiness(False, str(exc))
    configured_languages = {
        value.strip() for value in settings.languages.split(",") if value.strip()
    }
    if not configured_languages.issubset(set(bundle.identity.languages)):
        return OcrReadiness(False, "OCR_LANGUAGE_UNSUPPORTED", bundle)
    if not require_runtime:
        return OcrReadiness(True, "OCR_MODEL_VERIFIED", bundle)
    try:
        import paddleocr  # noqa: F401
    except ImportError:
        return OcrReadiness(False, "OCR_ENGINE_UNAVAILABLE", bundle)
    if settings.device.lower().startswith("gpu"):
        try:
            import paddle
        except ImportError:
            return OcrReadiness(False, "OCR_DEVICE_UNAVAILABLE", bundle)
        if not paddle.device.is_compiled_with_cuda():
            return OcrReadiness(False, "OCR_DEVICE_UNAVAILABLE", bundle)
    return OcrReadiness(True, "OCR_READY", bundle)
