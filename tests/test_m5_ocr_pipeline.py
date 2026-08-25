from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image
from reportlab.pdfgen import canvas

from astra_indexator.acquisition import AcquiredSource
from astra_indexator.config import OcrSettings
from astra_indexator.ocr import (
    DefaultOcrInputResolver,
    OcrDecision,
    OcrMode,
    OcrModelIdentity,
    OcrObservation,
    OcrPipelineService,
    OcrProfile,
    ResolvedOcrInput,
    verify_local_bundle,
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

MODEL = OcrModelIdentity(
    "paddle-ru-kk-en", "paddleocr", "3.x", "r1", "bundle-sha", ("ru", "kk", "en")
)


class FakeEngine:
    model_identity = MODEL

    def __init__(self, observations):
        self.observations = observations
        self.calls = 0

    def recognize(self, request):
        self.calls += 1
        return tuple(
            OcrObservation(
                text=text,
                confidence=confidence,
                block_order=index,
                candidate_id=request.candidate_id,
                source_element_id=request.source_element_id,
                page_number=request.page_number,
                geometry=SourceGeometry(
                    page_number=request.page_number,
                    x0=1,
                    y0=1,
                    x1=10,
                    y1=10,
                    coordinate_space="ocr-image-pixels",
                ),
                model=MODEL,
            )
            for index, (text, confidence) in enumerate(self.observations)
        )


class FakeResolver:
    def __init__(self, image_path: Path, width=100, height=100):
        self.image_path = image_path
        self.width = width
        self.height = height

    def resolve(self, *, source, document, candidate, profile):
        return ResolvedOcrInput(
            candidate.candidate_id,
            self.image_path,
            self.width,
            self.height,
            candidate.page_number,
            candidate.element_id,
            candidate.geometry,
            False,
        )


def source(tmp_path: Path, fmt="PNG") -> AcquiredSource:
    path = tmp_path / "source.png"
    Image.new("RGB", (100, 100), "white").save(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return AcquiredSource(
        StorageRef.parse("seaweed://docs/source.png"),
        path,
        "source.png",
        fmt,
        "image/png",
        path.stat().st_size,
        sha,
        None,
        None,
        "default-v1",
        (),
        datetime.now(timezone.utc),
    )


def parsed(
    src: AcquiredSource, *, native_text=None, scope="PAGE", page_mode="SCANNED_IMAGE"
) -> ParsedDocument:
    image = DocumentElement(
        "img", ElementType.IMAGE, 0, geometry=SourceGeometry(page_number=1), role="SCANNED_PAGE"
    )
    elements = [image]
    if native_text:
        elements.insert(
            0,
            DocumentElement(
                "native",
                ElementType.PARAGRAPH,
                0,
                text=native_text,
                geometry=SourceGeometry(page_number=1),
            ),
        )
        elements[1] = DocumentElement(
            "img", ElementType.IMAGE, 1, geometry=SourceGeometry(page_number=1), role="IMAGE"
        )
    candidate = OcrCandidate(
        "ocr:img",
        scope,
        1,
        "standalone_image" if scope == "PAGE" else "embedded_image",
        "img",
        SourceGeometry(page_number=1),
    )
    return ParsedDocument(
        "astra-indexator-document-v1",
        uuid4(),
        1,
        src.sha256,
        src.detected_format,
        ParserIdentity("fake", "v1", "default"),
        tuple(elements),
        (candidate,),
        ParseQuality(
            QualityStatus.OCR_REQUIRED if scope == "PAGE" else QualityStatus.GOOD,
            len(native_text or ""),
            1,
            (),
            (page_mode,),
        ),
    )


def test_if_needed_accepts_multilingual_ocr_and_preserves_provenance(tmp_path):
    src = source(tmp_path)
    doc = parsed(src)
    engine = FakeEngine([("Құжат атауы", 0.96), ("Документ", 0.94), ("Document", 0.93)])
    result = OcrPipelineService(engine=engine, resolver=FakeResolver(src.local_path)).process(
        source=src, document=doc
    )
    assert [element.text for element in result.accepted_ocr_elements] == [
        "Құжат атауы",
        "Документ",
        "Document",
    ]
    assert all(element.parent_element_id == "img" for element in result.accepted_ocr_elements)
    assert result.document.quality.status == QualityStatus.GOOD
    assert result.pages_processed == 1
    assert result.processing_fingerprint


def test_duplicate_ocr_is_not_appended_to_native_text(tmp_path):
    src = source(tmp_path)
    doc = parsed(src, native_text="Клиенттің өтініші Document 42")
    engine = FakeEngine([("Клиенттің өтініші Document 42", 0.99)])
    result = OcrPipelineService(engine=engine, resolver=FakeResolver(src.local_path)).process(
        source=src, document=doc
    )
    assert result.accepted_ocr_elements == ()
    assert (
        sum(
            1
            for element in result.document.elements
            if element.text == "Клиенттің өтініші Document 42"
        )
        == 1
    )


def test_disabled_mode_never_calls_engine(tmp_path):
    src = source(tmp_path)
    doc = parsed(src)
    engine = FakeEngine([("text", 0.9)])
    result = OcrPipelineService(engine=engine, resolver=FakeResolver(src.local_path)).process(
        source=src, document=doc, mode=OcrMode.DISABLED
    )
    assert engine.calls == 0
    assert result.candidate_results[0].decision == OcrDecision.REQUIRED_BUT_DISABLED
    assert result.document.quality.status == QualityStatus.PARTIAL


def test_pixel_budget_rejects_before_engine(tmp_path):
    src = source(tmp_path)
    doc = parsed(src)
    engine = FakeEngine([("text", 0.9)])
    result = OcrPipelineService(
        engine=engine, resolver=FakeResolver(src.local_path, 1000, 1000)
    ).process(source=src, document=doc, profile=OcrProfile(max_pixels_per_page=10))
    assert engine.calls == 0
    assert result.candidate_results[0].decision == OcrDecision.REJECTED_RESOURCE_LIMIT


def test_verified_bundle_is_fail_closed_and_checksums_files(tmp_path):
    root = tmp_path / "bundle"
    det = root / "det"
    rec = root / "rec"
    det.mkdir(parents=True)
    rec.mkdir(parents=True)
    det_file = det / "model.pdmodel"
    rec_file = rec / "model.pdmodel"
    det_file.write_bytes(b"det")
    rec_file.write_bytes(b"rec")
    files = []
    for relative, path in (("det/model.pdmodel", det_file), ("rec/model.pdmodel", rec_file)):
        files.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    manifest = {
        "schemaVersion": "astra-indexator-ocr-model-v1",
        "modelId": "ocr_cpu_ru_kk_en",
        "engine": "paddleocr",
        "engineVersion": "3.x",
        "artifactRevision": "2026.08.1",
        "languages": ["ru", "kk", "en"],
        "textDetectionModelDir": "det",
        "textRecognitionModelDir": "rec",
        "files": files,
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    bundle = verify_local_bundle(root)
    assert bundle.identity.languages == ("ru", "kk", "en")
    assert bundle.text_detection_model_dir == det
    rec_file.write_bytes(b"tampered")
    try:
        verify_local_bundle(root)
    except RuntimeError as exc:
        assert "CHECKSUM_MISMATCH" in str(exc)
    else:
        raise AssertionError("tampered model bundle must be rejected")


def test_pdf_page_candidate_is_rendered_incrementally(tmp_path):
    pdf_path = tmp_path / "scan.pdf"
    pdf = canvas.Canvas(str(pdf_path), pagesize=(300, 400))
    pdf.drawString(40, 200, "render fixture")
    pdf.showPage()
    pdf.save()
    sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    src = AcquiredSource(
        StorageRef.parse("seaweed://docs/scan.pdf"),
        pdf_path,
        "scan.pdf",
        "PDF",
        "application/pdf",
        pdf_path.stat().st_size,
        sha,
        None,
        None,
        "default-v1",
        (),
        datetime.now(timezone.utc),
    )
    candidate = OcrCandidate(
        "ocr:page:1", "PAGE", 1, "low_native_text", None, SourceGeometry(page_number=1)
    )
    doc = ParsedDocument(
        "astra-indexator-document-v1",
        uuid4(),
        1,
        sha,
        "PDF",
        ParserIdentity("pdf", "v1", "default"),
        (),
        (candidate,),
        ParseQuality(QualityStatus.OCR_REQUIRED, 0, 1, (), ("LOW_SIGNAL",)),
    )
    resolver = DefaultOcrInputResolver(tmp_path / "workspace")
    resolved = resolver.resolve(
        source=src, document=doc, candidate=candidate, profile=OcrProfile(render_dpi=144)
    )
    try:
        assert resolved.width > 0 and resolved.height > 0
        assert resolved.image_path.exists()
        assert resolved.pixels <= OcrProfile().max_pixels_per_page
    finally:
        resolved.image_path.unlink(missing_ok=True)


def test_ocr_settings_validate_confidence_and_multilingual_default():
    settings = OcrSettings()
    assert settings.languages == "ru,kk,en"
    assert settings.device == "cpu"
    try:
        OcrSettings(hard_confidence_floor=0.9, min_confidence=0.2)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid OCR confidence thresholds must fail startup validation")
