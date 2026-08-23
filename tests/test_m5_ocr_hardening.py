from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image

from astra_indexator.acquisition import AcquiredSource
from astra_indexator.config import OcrSettings
from astra_indexator.ocr import (
    OcrDecision,
    OcrMode,
    OcrModelIdentity,
    OcrObservation,
    OcrPipelineService,
    OcrProfile,
    ResolvedOcrInput,
    check_ocr_readiness,
)
from astra_indexator.parser import (
    DocumentElement,
    ElementType,
    OcrCandidate,
    ParsedDocument,
    ParseQuality,
    ParserIdentity,
    QualityStatus,
    SourceGeometry,
)
from astra_indexator.storage import StorageRef


def _source(tmp_path: Path) -> AcquiredSource:
    path = tmp_path / "image.png"
    Image.new("RGB", (120, 80), "white").save(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return AcquiredSource(StorageRef.parse("seaweed://docs/image.png"), path, "image.png", "PNG", "image/png",
                          path.stat().st_size, sha, None, None, "default-v1", (), datetime.now(timezone.utc))


def _bundle(root: Path) -> Path:
    (root / "det").mkdir(parents=True)
    (root / "rec").mkdir(parents=True)
    files = []
    for rel, data in (("det/model.bin", b"det"), ("rec/model.bin", b"rec")):
        path = root / rel
        path.write_bytes(data)
        files.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {
        "schemaVersion": "astra-indexator-ocr-model-v1",
        "modelKind": "OCR",
        "modelId": "ocr_cpu_ru_kk_en",
        "engine": "paddleocr",
        "engineVersion": "3.x",
        "artifactRevision": "r1",
        "languages": ["ru", "kk", "en"],
        "textDetectionModelDir": "det",
        "textRecognitionModelDir": "rec",
        "files": files,
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


class Resolver:
    def __init__(self, path: Path):
        self.path = path

    def resolve(self, *, source, document, candidate, profile):
        return ResolvedOcrInput(candidate.candidate_id, self.path, 120, 80, candidate.page_number,
                                candidate.element_id, candidate.geometry, False)


class Engine:
    def __init__(self, revision="r1", checksum="sha1"):
        self.calls = 0
        self.model_identity = OcrModelIdentity("ocr", "paddleocr", "3.x", revision, checksum, ("ru", "kk", "en"))

    def recognize(self, request):
        self.calls += 1
        return (OcrObservation("Құжат", 0.95, 0, request.candidate_id, request.source_element_id,
                               request.page_number, SourceGeometry(page_number=request.page_number),
                               self.model_identity),)


def _document(src: AcquiredSource, *, two_images=False, good_native=False) -> ParsedDocument:
    elements = []
    if good_native:
        elements.append(DocumentElement("p", ElementType.PARAGRAPH, 0, text="native text"))
    elements.append(DocumentElement("img1", ElementType.IMAGE, len(elements), role="EMBEDDED_IMAGE"))
    candidates = [OcrCandidate("ocr:img1", "EMBEDDED_IMAGE", None, "docx_embedded_image", "img1")]
    if two_images:
        elements.append(DocumentElement("img2", ElementType.IMAGE, len(elements), role="EMBEDDED_IMAGE"))
        candidates.append(OcrCandidate("ocr:img2", "EMBEDDED_IMAGE", None, "docx_embedded_image", "img2"))
    quality = ParseQuality(QualityStatus.GOOD if good_native else QualityStatus.OCR_REQUIRED,
                           len("native text") if good_native else 0, len(candidates), (), ())
    return ParsedDocument("astra-indexator-document-v1", uuid4(), 1, src.sha256, "PNG",
                          ParserIdentity("fixture", "v1", "default"), tuple(elements), tuple(candidates), quality)


def test_disabled_optional_embedded_image_does_not_downgrade_good_native_document(tmp_path):
    src = _source(tmp_path)
    doc = _document(src, good_native=True)
    engine = Engine()
    result = OcrPipelineService(engine=engine, resolver=Resolver(src.local_path)).process(
        source=src, document=doc, mode=OcrMode.DISABLED
    )
    assert engine.calls == 0
    assert result.candidate_results[0].decision == OcrDecision.NOT_REQUIRED
    assert result.document.quality.status == QualityStatus.GOOD


def test_repeated_identical_image_is_recognized_once_but_projected_per_occurrence(tmp_path):
    src = _source(tmp_path)
    doc = _document(src, two_images=True)
    engine = Engine()
    result = OcrPipelineService(engine=engine, resolver=Resolver(src.local_path)).process(source=src, document=doc)
    assert engine.calls == 1
    assert len(result.accepted_ocr_elements) == 2
    assert {element.parent_element_id for element in result.accepted_ocr_elements} == {"img1", "img2"}
    assert "OCR_RESULT_REUSED_BY_IMAGE_HASH" in result.candidate_results[1].reason_codes


def test_model_revision_changes_processing_fingerprint(tmp_path):
    src = _source(tmp_path)
    doc = _document(src)
    first = OcrPipelineService(engine=Engine("r1", "sha1"), resolver=Resolver(src.local_path)).process(source=src, document=doc)
    second = OcrPipelineService(engine=Engine("r2", "sha2"), resolver=Resolver(src.local_path)).process(source=src, document=doc)
    assert first.processing_fingerprint != second.processing_fingerprint


def test_parser_only_readiness_can_verify_bundle_without_paddle_runtime(tmp_path):
    root = _bundle(tmp_path / "bundle")
    settings = OcrSettings(model_bundle_root=root)
    readiness = check_ocr_readiness(settings, require_runtime=False)
    assert readiness.ready is True
    assert readiness.code == "OCR_MODEL_VERIFIED"
    assert readiness.bundle is not None
