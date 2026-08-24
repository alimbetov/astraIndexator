from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .model import OcrModelIdentity


class OcrModelBundleError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VerifiedOcrModelBundle:
    root: Path
    manifest: dict[str, Any]
    identity: OcrModelIdentity

    @property
    def text_detection_model_dir(self) -> Path:
        return self.root / self.manifest["textDetectionModelDir"]

    @property
    def text_recognition_model_dir(self) -> Path:
        return self.root / self.manifest["textRecognitionModelDir"]

    @property
    def inference_engine(self) -> str | None:
        value = self.manifest.get("inferenceEngine")
        return str(value) if value else None

    @property
    def execution_provider(self) -> str | None:
        value = self.manifest.get("executionProvider")
        return str(value) if value else None

    @property
    def precision(self) -> str:
        return str(self.manifest.get("precision", "fp32")).lower()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_onnx_bundle(root: Path, manifest: dict[str, Any]) -> None:
    if str(manifest.get("inferenceEngine", "")).lower() != "onnxruntime":
        return
    provider = str(manifest.get("executionProvider", ""))
    if provider not in {"CPUExecutionProvider", "CUDAExecutionProvider", "TensorrtExecutionProvider"}:
        raise OcrModelBundleError("OCR_MODEL_EXECUTION_PROVIDER_INVALID")
    precision = str(manifest.get("precision", "fp32")).lower()
    if precision not in {"fp32", "fp16", "int8"}:
        raise OcrModelBundleError("OCR_MODEL_PRECISION_INVALID")
    for directory_key in ("textDetectionModelDir", "textRecognitionModelDir"):
        model_dir = root / str(manifest[directory_key])
        if not any(path.is_file() and path.suffix.lower() == ".onnx" for path in model_dir.rglob("*.onnx")):
            raise OcrModelBundleError(f"OCR_ONNX_MODEL_MISSING:{directory_key}")


def verify_local_bundle(root: Path) -> VerifiedOcrModelBundle:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise OcrModelBundleError("OCR_MODEL_MANIFEST_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != "astra-indexator-ocr-model-v1":
        raise OcrModelBundleError("OCR_MODEL_MANIFEST_UNSUPPORTED")
    if manifest.get("modelKind", "OCR") != "OCR":
        raise OcrModelBundleError("OCR_MODEL_KIND_INVALID")
    required = ["modelId", "engine", "engineVersion", "artifactRevision", "languages", "files",
                "textDetectionModelDir", "textRecognitionModelDir"]
    missing = [key for key in required if key not in manifest]
    if missing:
        raise OcrModelBundleError(f"OCR_MODEL_MANIFEST_INVALID:{','.join(missing)}")
    if not isinstance(manifest["files"], list) or not manifest["files"]:
        raise OcrModelBundleError("OCR_MODEL_MANIFEST_INVALID:files")
    for item in manifest["files"]:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise OcrModelBundleError("OCR_MODEL_PATH_INVALID")
        path = root / relative
        if not path.is_file():
            raise OcrModelBundleError(f"OCR_MODEL_FILE_MISSING:{relative.as_posix()}")
        if _sha256_file(path).lower() != str(item["sha256"]).lower():
            raise OcrModelBundleError(f"OCR_MODEL_CHECKSUM_MISMATCH:{relative.as_posix()}")
    for directory_key in ("textDetectionModelDir", "textRecognitionModelDir"):
        relative = Path(manifest[directory_key])
        if relative.is_absolute() or ".." in relative.parts or not (root / relative).is_dir():
            raise OcrModelBundleError(f"OCR_MODEL_DIRECTORY_MISSING:{directory_key}")
    _validate_onnx_bundle(root, manifest)
    manifest_digest = hashlib.sha256(manifest_path.read_bytes())
    for item in sorted(manifest["files"], key=lambda value: value["path"]):
        manifest_digest.update(item["path"].encode("utf-8"))
        manifest_digest.update(str(item["sha256"]).lower().encode("ascii"))
    identity = OcrModelIdentity(
        model_id=str(manifest["modelId"]),
        engine=str(manifest["engine"]),
        engine_version=str(manifest["engineVersion"]),
        artifact_revision=str(manifest["artifactRevision"]),
        bundle_sha256=manifest_digest.hexdigest(),
        languages=tuple(str(value) for value in manifest["languages"]),
    )
    return VerifiedOcrModelBundle(root=root, manifest=manifest, identity=identity)


class NexusOcrBundlePreloader:
    """Explicit startup/init utility; never used from per-document recognition."""

    def __init__(self, *, client: httpx.Client, allowed_origin: str = "https://nexus.astrabase.asia"):
        self.client = client
        parsed = urlparse(allowed_origin)
        self.allowed_origin = f"{parsed.scheme}://{parsed.netloc}"

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin != self.allowed_origin:
            raise OcrModelBundleError("OCR_MODEL_DOWNLOAD_ORIGIN_FORBIDDEN")

    def download_file(self, url: str, target: Path, expected_sha256: str) -> None:
        self._validate_url(url)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        digest = hashlib.sha256()
        with self.client.stream("GET", url) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_bytes():
                    digest.update(chunk)
                    output.write(chunk)
        if digest.hexdigest().lower() != expected_sha256.lower():
            temporary.unlink(missing_ok=True)
            raise OcrModelBundleError("OCR_MODEL_DOWNLOAD_CHECKSUM_MISMATCH")
        temporary.replace(target)

    def preload(self, *, manifest_url: str, expected_manifest_sha256: str, target_root: Path) -> VerifiedOcrModelBundle:
        self._validate_url(manifest_url)
        response = self.client.get(manifest_url)
        response.raise_for_status()
        manifest_bytes = response.content
        if hashlib.sha256(manifest_bytes).hexdigest().lower() != expected_manifest_sha256.lower():
            raise OcrModelBundleError("OCR_MODEL_MANIFEST_CHECKSUM_MISMATCH")
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        target_root.mkdir(parents=True, exist_ok=True)
        for item in manifest.get("files", []):
            relative = Path(item["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise OcrModelBundleError("OCR_MODEL_PATH_INVALID")
            download_url = item.get("downloadUrl")
            if not download_url:
                raise OcrModelBundleError(f"OCR_MODEL_DOWNLOAD_URL_MISSING:{relative.as_posix()}")
            self.download_file(str(download_url), target_root / relative, str(item["sha256"]))
        manifest_part = target_root / "manifest.json.part"
        manifest_part.write_bytes(manifest_bytes)
        manifest_part.replace(target_root / "manifest.json")
        return verify_local_bundle(target_root)
