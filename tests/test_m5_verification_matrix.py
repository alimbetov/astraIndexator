from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from astra_indexator.acquisition import AcquiredSource
from astra_indexator.ocr import (
    DefaultOcrInputResolver,
    NexusOcrBundlePreloader,
    OcrDecision,
    OcrMode,
    OcrModelBundleError,
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


MODEL = OcrModelIdentity("ocr", "paddleocr", "3.x", "r1", "bundle", ("ru", "kk", "en"))


class Engine:
    model_identity = MODEL

    def __init__(self, *, text="Текст", confidence=0.95, delay=0.0, error=None, geometry=None):
        self.text = text
        self.confidence = confidence
        self.delay = delay
        self.error = error
        self.geometry = geometry or SourceGeometry(page_number=1, x0=10, y0=20, x1=50, y1=40, coordinate_space="ocr-image-pixels")
        self.calls = 0

    def recognize(self, request):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return (OcrObservation(self.text, self.confidence, 0, request.candidate_id, request.source_element_id,
                               request.page_number, self.geometry, self.model_identity),)


class Resolver:
    def __init__(self, path: Path, *, width=100, height=100, fail=False, source_geometry=None):
        self.path = path
        self.width = width
        self.height = height
        self.fail = fail
        self.source_geometry = source_geometry
        self.calls = 0

    def resolve(self, *, source, document, candidate, profile):
        self.calls += 1
        if self.fail:
            raise RuntimeError("broken render")
        return ResolvedOcrInput(candidate.candidate_id, self.path, self.width, self.height,
                                candidate.page_number, candidate.element_id,
                                self.source_geometry if self.source_geometry is not None else candidate.geometry, False)


def source(tmp_path: Path, fmt="PNG") -> AcquiredSource:
    path = tmp_path / "source.png"
    Image.new("RGB", (100, 100), "white").save(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return AcquiredSource(StorageRef.parse("seaweed://docs/source.png"), path, "source.png", fmt, "image/png",
                          path.stat().st_size, sha, None, None, "default-v1", (), datetime.now(timezone.utc))


def document(src: AcquiredSource, *, scope="PAGE", page_mode="SCANNED_IMAGE", native=None,
             candidate_id="ocr:1", element_id="img", reason="low_native_text", geometry=None) -> ParsedDocument:
    elements = []
    if native is not None:
        elements.append(DocumentElement("native", ElementType.PARAGRAPH, 0, text=native,
                                        geometry=SourceGeometry(page_number=1)))
    elements.append(DocumentElement(element_id, ElementType.IMAGE, len(elements), geometry=geometry or SourceGeometry(page_number=1)))
    candidate = OcrCandidate(candidate_id, scope, 1, reason, element_id, geometry or SourceGeometry(page_number=1))
    quality = ParseQuality(QualityStatus.OCR_REQUIRED if scope == "PAGE" else QualityStatus.GOOD,
                           len(native or ""), 1, (), (page_mode,))
    return ParsedDocument("astra-indexator-document-v1", uuid4(), 1, src.sha256, src.detected_format,
                          ParserIdentity("fixture", "v1", "default"), tuple(elements), (candidate,), quality)


def with_candidates(doc: ParsedDocument, candidates, elements=None):
    return ParsedDocument(doc.schema_version, doc.document_id, doc.document_version, doc.source_sha256,
                          doc.detected_format, doc.parser, tuple(elements if elements is not None else doc.elements),
                          tuple(candidates), ParseQuality(doc.quality.status, doc.quality.native_text_chars,
                                                        len(candidates), doc.quality.warnings, doc.quality.page_modes))


def test_native_page_is_skipped_in_if_needed_mode(tmp_path):
    src = source(tmp_path)
    doc = document(src, page_mode="NATIVE_TEXT", reason="parser_advisory")
    engine = Engine()
    resolver = Resolver(src.local_path)
    result = OcrPipelineService(engine=engine, resolver=resolver).process(source=src, document=doc)
    assert result.candidate_results[0].decision == OcrDecision.NOT_REQUIRED
    assert engine.calls == resolver.calls == 0


def test_force_mode_processes_candidate_even_on_native_page(tmp_path):
    src = source(tmp_path)
    doc = document(src, page_mode="NATIVE_TEXT", reason="parser_advisory")
    engine = Engine(text="Forced OCR")
    result = OcrPipelineService(engine=engine, resolver=Resolver(src.local_path)).process(
        source=src, document=doc, mode=OcrMode.FORCE
    )
    assert engine.calls == 1
    assert result.accepted_ocr_elements[0].text == "Forced OCR"


def test_unsupported_scope_is_rejected_without_rendering(tmp_path):
    src = source(tmp_path)
    doc = document(src, scope="UNKNOWN")
    resolver = Resolver(src.local_path)
    result = OcrPipelineService(engine=Engine(), resolver=resolver).process(source=src, document=doc)
    assert result.candidate_results[0].decision == OcrDecision.UNSUPPORTED
    assert resolver.calls == 0


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("hash", "OCR_SOURCE_HASH_MISMATCH"),
        ("format", "OCR_SOURCE_FORMAT_MISMATCH"),
        ("duplicate", "OCR_DUPLICATE_CANDIDATE_ID"),
        ("missing-element", "OCR_CANDIDATE_SOURCE_ELEMENT_MISSING"),
    ],
)
def test_invalid_pipeline_identity_fails_closed(tmp_path, mutation, error):
    src = source(tmp_path)
    doc = document(src)
    if mutation == "hash":
        doc = ParsedDocument(doc.schema_version, doc.document_id, doc.document_version, "0" * 64, doc.detected_format,
                             doc.parser, doc.elements, doc.ocr_candidates, doc.quality)
    elif mutation == "format":
        doc = ParsedDocument(doc.schema_version, doc.document_id, doc.document_version, doc.source_sha256, "JPEG",
                             doc.parser, doc.elements, doc.ocr_candidates, doc.quality)
    elif mutation == "duplicate":
        c = doc.ocr_candidates[0]
        doc = with_candidates(doc, (c, c))
    else:
        c = doc.ocr_candidates[0]
        missing = OcrCandidate(c.candidate_id, c.scope, c.page_number, c.reason, "missing", c.geometry, c.metadata)
        doc = with_candidates(doc, (missing,))
    with pytest.raises(RuntimeError, match=error):
        OcrPipelineService(engine=Engine(), resolver=Resolver(src.local_path)).process(source=src, document=doc)


def test_unsupported_profile_language_fails_explicitly(tmp_path):
    src = source(tmp_path)
    doc = document(src)
    with pytest.raises(RuntimeError, match="OCR_LANGUAGE_UNSUPPORTED:de"):
        OcrPipelineService(engine=Engine(), resolver=Resolver(src.local_path)).process(
            source=src, document=doc, profile=OcrProfile(languages=("ru", "de"))
        )


def test_engine_pixel_geometry_is_normalized(tmp_path):
    src = source(tmp_path)
    doc = document(src)
    result = OcrPipelineService(engine=Engine(), resolver=Resolver(src.local_path, width=100, height=100)).process(
        source=src, document=doc
    )
    g = result.accepted_ocr_elements[0].geometry
    assert g.coordinate_space == "normalized-0-1"
    assert (g.x0, g.y0, g.x1, g.y1) == pytest.approx((0.1, 0.2, 0.5, 0.4))
    assert g.page_width == g.page_height == 1.0


def test_region_geometry_maps_back_to_page_coordinates(tmp_path):
    src = source(tmp_path)
    region = SourceGeometry(page_number=1, x0=20, y0=30, x1=60, y1=70,
                            page_width=100, page_height=100, coordinate_space="pdf-points")
    doc = document(src, scope="REGION", geometry=region)
    engine = Engine(geometry=SourceGeometry(page_number=1, x0=0, y0=0, x1=50, y1=50,
                                            coordinate_space="ocr-image-pixels"))
    result = OcrPipelineService(engine=engine, resolver=Resolver(src.local_path, width=100, height=100,
                                                                  source_geometry=region)).process(source=src, document=doc)
    g = result.accepted_ocr_elements[0].geometry
    assert (g.x0, g.y0, g.x1, g.y1) == pytest.approx((0.2, 0.3, 0.4, 0.5))


def test_low_confidence_policy_keeps_evidence_above_hard_floor_and_drops_below(tmp_path):
    src = source(tmp_path)
    doc = document(src)
    retained = OcrPipelineService(engine=Engine(confidence=0.2), resolver=Resolver(src.local_path)).process(
        source=src, document=doc, profile=OcrProfile(min_confidence=0.35, hard_confidence_floor=0.1)
    )
    assert retained.accepted_ocr_elements[0].metadata["ocrQuality"] == "LOW_CONFIDENCE"
    dropped = OcrPipelineService(engine=Engine(confidence=0.05), resolver=Resolver(src.local_path)).process(
        source=src, document=doc, profile=OcrProfile(min_confidence=0.35, hard_confidence_floor=0.1)
    )
    assert dropped.accepted_ocr_elements == ()
    assert "OCR_LOW_CONFIDENCE_DROPPED" in dropped.warnings


def test_invalid_engine_confidence_is_classified_as_invalid_output(tmp_path):
    src = source(tmp_path)
    doc = document(src)
    result = OcrPipelineService(engine=Engine(confidence=1.5), resolver=Resolver(src.local_path)).process(source=src, document=doc)
    assert result.candidate_results[0].warnings == ("OCR_OUTPUT_INVALID",)
    assert result.document.quality.status == QualityStatus.PARTIAL


def test_render_failure_is_contained_and_classified(tmp_path):
    src = source(tmp_path)
    doc = document(src)
    result = OcrPipelineService(engine=Engine(), resolver=Resolver(src.local_path, fail=True)).process(source=src, document=doc)
    assert result.candidate_results[0].warnings == ("OCR_RENDER_FAILED",)
    assert result.document.quality.status == QualityStatus.PARTIAL


def test_engine_timeout_and_engine_failure_are_distinct(tmp_path):
    src = source(tmp_path)
    doc = document(src)
    timeout = OcrPipelineService(engine=Engine(error=TimeoutError("slow")), resolver=Resolver(src.local_path)).process(source=src, document=doc)
    failure = OcrPipelineService(engine=Engine(error=RuntimeError("boom")), resolver=Resolver(src.local_path)).process(source=src, document=doc)
    assert timeout.candidate_results[0].warnings == ("OCR_TIMEOUT",)
    assert failure.candidate_results[0].warnings == ("OCR_ENGINE_FAILED",)


@pytest.mark.parametrize(
    "profile,expected",
    [
        (OcrProfile(max_pixels_per_page=10), "OCR_PIXEL_LIMIT"),
        (OcrProfile(max_derived_bytes=1), "OCR_DERIVED_BYTES_LIMIT"),
        (OcrProfile(memory_hard_limit_bytes=100), "OCR_MEMORY_LIMIT"),
    ],
)
def test_resource_guards_reject_before_engine(tmp_path, profile, expected):
    src = source(tmp_path)
    doc = document(src)
    engine = Engine()
    result = OcrPipelineService(engine=engine, resolver=Resolver(src.local_path, width=100, height=100)).process(
        source=src, document=doc, profile=profile
    )
    assert result.candidate_results[0].decision == OcrDecision.REJECTED_RESOURCE_LIMIT
    assert expected in result.candidate_results[0].reason_codes
    assert engine.calls == 0


def test_soft_memory_limit_warns_but_allows_processing(tmp_path):
    src = source(tmp_path)
    doc = document(src)
    result = OcrPipelineService(engine=Engine(), resolver=Resolver(src.local_path, width=100, height=100)).process(
        source=src, document=doc, profile=OcrProfile(memory_soft_limit_bytes=100, memory_hard_limit_bytes=100_000)
    )
    assert "OCR_MEMORY_SOFT_LIMIT" in result.warnings
    assert result.accepted_ocr_elements


def test_page_limit_does_not_count_rejected_page_as_processed(tmp_path):
    src = source(tmp_path)
    base = document(src)
    e1 = DocumentElement("img1", ElementType.IMAGE, 0, geometry=SourceGeometry(page_number=1))
    e2 = DocumentElement("img2", ElementType.IMAGE, 1, geometry=SourceGeometry(page_number=2))
    c1 = OcrCandidate("ocr:1", "PAGE", 1, "scanned", "img1", SourceGeometry(page_number=1))
    c2 = OcrCandidate("ocr:2", "PAGE", 2, "scanned", "img2", SourceGeometry(page_number=2))
    doc = ParsedDocument(base.schema_version, base.document_id, base.document_version, base.source_sha256, base.detected_format,
                         base.parser, (e1, e2), (c1, c2), ParseQuality(QualityStatus.OCR_REQUIRED, 0, 2, (), ("SCANNED_IMAGE", "SCANNED_IMAGE")))
    engine = Engine()
    result = OcrPipelineService(engine=engine, resolver=Resolver(src.local_path)).process(
        source=src, document=doc, profile=OcrProfile(max_pages_per_job=1)
    )
    assert result.pages_processed == 1
    assert result.candidate_results[1].reason_codes == ("OCR_PAGE_LIMIT",)
    assert engine.calls == 1


def test_total_pixel_budget_applies_across_candidates(tmp_path):
    src = source(tmp_path)
    base = document(src)
    elements = (DocumentElement("img1", ElementType.IMAGE, 0), DocumentElement("img2", ElementType.IMAGE, 1))
    candidates = (OcrCandidate("ocr:1", "EMBEDDED_IMAGE", None, "image", "img1"),
                  OcrCandidate("ocr:2", "EMBEDDED_IMAGE", None, "image", "img2"))
    doc = with_candidates(base, candidates, elements)
    engine = Engine()
    result = OcrPipelineService(engine=engine, resolver=Resolver(src.local_path, width=100, height=100)).process(
        source=src, document=doc, profile=OcrProfile(max_total_pixels_per_job=15_000)
    )
    assert engine.calls == 1
    assert result.candidate_results[1].decision == OcrDecision.REJECTED_RESOURCE_LIMIT


def test_default_resolver_allocates_unique_scratch_paths(tmp_path):
    src = source(tmp_path)
    doc = document(src)
    candidate = doc.ocr_candidates[0]
    resolver = DefaultOcrInputResolver(tmp_path / "work")
    first = resolver.resolve(source=src, document=doc, candidate=candidate, profile=OcrProfile())
    second = resolver.resolve(source=src, document=doc, candidate=candidate, profile=OcrProfile())
    try:
        assert first.image_path != second.image_path
        assert first.image_path.exists() and second.image_path.exists()
    finally:
        first.image_path.unlink(missing_ok=True)
        second.image_path.unlink(missing_ok=True)


def test_png_region_is_cropped_before_ocr(tmp_path):
    src = source(tmp_path)
    region = SourceGeometry(page_number=1, x0=0.25, y0=0.25, x1=0.75, y1=0.75, coordinate_space="normalized-0-1")
    doc = document(src, scope="REGION", geometry=region)
    resolver = DefaultOcrInputResolver(tmp_path / "work")
    resolved = resolver.resolve(source=src, document=doc, candidate=doc.ocr_candidates[0], profile=OcrProfile())
    try:
        assert (resolved.width, resolved.height) == (50, 50)
    finally:
        resolved.image_path.unlink(missing_ok=True)


def test_invalid_tiff_frame_is_explicit_render_failure(tmp_path):
    path = tmp_path / "multi.tiff"
    Image.new("RGB", (50, 50), "white").save(path, format="TIFF")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    src = AcquiredSource(StorageRef.parse("seaweed://docs/multi.tiff"), path, "multi.tiff", "TIFF", "image/tiff",
                         path.stat().st_size, sha, None, None, "default-v1", (), datetime.now(timezone.utc))
    element = DocumentElement("img", ElementType.IMAGE, 0, geometry=SourceGeometry(page_number=2))
    candidate = OcrCandidate("ocr:2", "PAGE", 2, "scanned", "img", SourceGeometry(page_number=2))
    doc = ParsedDocument("astra-indexator-document-v1", uuid4(), 1, sha, "TIFF", ParserIdentity("fixture", "v1", "default"),
                         (element,), (candidate,), ParseQuality(QualityStatus.OCR_REQUIRED, 0, 1, (), ("SCANNED_IMAGE",)))
    result = OcrPipelineService(engine=Engine(), resolver=DefaultOcrInputResolver(tmp_path / "work")).process(source=src, document=doc)
    assert result.candidate_results[0].warnings == ("OCR_RENDER_FAILED",)


def test_model_bundle_negative_cases_fail_closed(tmp_path):
    with pytest.raises(OcrModelBundleError, match="OCR_MODEL_MANIFEST_MISSING"):
        verify_local_bundle(tmp_path / "missing")

    root = tmp_path / "bundle"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"schemaVersion": "wrong"}), encoding="utf-8")
    with pytest.raises(OcrModelBundleError, match="OCR_MODEL_MANIFEST_UNSUPPORTED"):
        verify_local_bundle(root)


def test_nexus_preloader_rejects_non_approved_origin(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"x")))
    preloader = NexusOcrBundlePreloader(client=client)
    with pytest.raises(OcrModelBundleError, match="OCR_MODEL_DOWNLOAD_ORIGIN_FORBIDDEN"):
        preloader.download_file("https://example.com/model.bin", tmp_path / "model.bin", hashlib.sha256(b"x").hexdigest())
    client.close()
