from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_local_bundle(root: Path) -> VerifiedOcrModelBundle:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise OcrModelBundleError("OCR_MODEL_MANIFEST_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != "astra-indexator-ocr-model-v1":
        raise OcrModelBundleError("OCR_MODEL_MANIFEST_UNSUPPORTED")
    required = ["modelId", "engine", "engineVersion", "artifactRevision", "languages", "files",
                "textDetectionModelDir", "textRecognitionModelDir"]
    missing = [key for key in required if key not in manifest]
    if missing:
        raise OcrModelBundleError(f"OCR_MODEL_MANIFEST_INVALID:{','.join(missing)}")
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
        if not (root / manifest[directory_key]).is_dir():
            raise OcrModelBundleError(f"OCR_MODEL_DIRECTORY_MISSING:{directory_key}")
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
    """Explicit startup/init utility. It is never called from document OCR execution."""

    def __init__(self, *, client: httpx.Client):
        self.client = client

    def download_file(self, url: str, target: Path, expected_sha256: str) -> None:
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
