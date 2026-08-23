from __future__ import annotations

import hashlib
import re
import time
from dataclasses import replace
from typing import Protocol

from astra_indexator.acquisition import AcquiredSource
from astra_indexator.parser import DocumentElement, ElementType, ParsedDocument, ParseQuality, QualityStatus

from .engine import OcrEngine
from .model import (
    OcrCandidateResult,
    OcrDecision,
    OcrMode,
    OcrObservation,
    OcrPipelineResult,
    OcrProfile,
    OcrRequest,
    ReconciliationAction,
)
from .policy import OcrDecisionPolicy
from .resolver import OcrInputResolver


class OcrMetrics(Protocol):
    def candidate(self, decision: str, scope: str) -> None: ...
    def recognition(self, result: str, seconds: float) -> None: ...
    def reconciliation(self, action: str) -> None: ...
    def resource(self, reason: str) -> None: ...


class NoopOcrMetrics:
    def candidate(self, decision: str, scope: str) -> None: return None
    def recognition(self, result: str, seconds: float) -> None: return None
    def reconciliation(self, action: str) -> None: return None
    def resource(self, reason: str) -> None: return None


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE)).strip()


def _similarity(left: str, right: str) -> float:
    a, b = _normalize_for_match(left), _normalize_for_match(right)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    at, bt = set(a.split()), set(b.split())
    return len(at & bt) / max(1, len(at | bt))


def _file_sha256(path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(document: ParsedDocument, profile: OcrProfile, engine: OcrEngine, mode: OcrMode) -> str:
    identity = engine.model_identity
    payload = "\x1f".join([
        document.source_sha256, document.parser.name, document.parser.version, document.parser.profile,
        mode.value, profile.profile_id, profile.decision_policy_version, profile.preprocessing_version,
        profile.reconciliation_version, identity.engine, identity.engine_version, identity.model_id,
        identity.artifact_revision, identity.bundle_sha256, ",".join(profile.languages),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _project_observations(observations: tuple[OcrObservation, ...], candidate) -> tuple[OcrObservation, ...]:
    projected = []
    for observation in observations:
        geometry = observation.geometry
        if geometry is not None:
            geometry = replace(geometry, page_number=candidate.page_number)
        projected.append(replace(observation, candidate_id=candidate.candidate_id,
                                 source_element_id=candidate.element_id, page_number=candidate.page_number,
                                 geometry=geometry))
    return tuple(projected)


class OcrPipelineService:
    def __init__(self, *, engine: OcrEngine, resolver: OcrInputResolver,
                 policy: OcrDecisionPolicy | None = None, metrics: OcrMetrics | None = None):
        self.engine = engine
        self.resolver = resolver
        self.policy = policy or OcrDecisionPolicy()
        self.metrics = metrics or NoopOcrMetrics()

    def process(self, *, source: AcquiredSource, document: ParsedDocument,
                mode: OcrMode = OcrMode.IF_NEEDED, profile: OcrProfile = OcrProfile()) -> OcrPipelineResult:
        started_job = time.monotonic()
        results: list[OcrCandidateResult] = []
        accepted: list[DocumentElement] = []
        pixels = pages = derived_bytes = 0
        warnings: list[str] = []
        candidate_by_id = {candidate.candidate_id: candidate for candidate in document.ocr_candidates}
        image_cache: dict[str, tuple[OcrObservation, ...]] = {}

        for candidate in document.ocr_candidates:
            decision = self.policy.decide(document=document, candidate=candidate, mode=mode, profile=profile)
            self.metrics.candidate(decision.decision.value, candidate.scope)
            if decision.decision != OcrDecision.REQUIRED:
                results.append(OcrCandidateResult(candidate.candidate_id, decision.decision, decision.reason_codes))
                continue
            if time.monotonic() - started_job >= profile.timeout_per_job_seconds:
                self.metrics.resource("OCR_JOB_TIMEOUT")
                results.append(OcrCandidateResult(candidate.candidate_id, OcrDecision.REJECTED_RESOURCE_LIMIT, ("OCR_JOB_TIMEOUT",)))
                warnings.append("OCR_JOB_TIMEOUT")
                continue

            resolved = self.resolver.resolve(source=source, document=document, candidate=candidate, profile=profile)
            try:
                file_bytes = resolved.image_path.stat().st_size
                memory_estimate = resolved.pixels * 4
                if resolved.width < profile.min_image_width or resolved.height < profile.min_image_height or resolved.pixels < profile.min_image_pixels:
                    results.append(OcrCandidateResult(candidate.candidate_id, OcrDecision.NOT_REQUIRED, ("DECORATIVE_IMAGE_TOO_SMALL",)))
                    continue
                if resolved.pixels > profile.max_pixels_per_page or pixels + resolved.pixels > profile.max_total_pixels_per_job:
                    self.metrics.resource("OCR_PIXEL_LIMIT")
                    results.append(OcrCandidateResult(candidate.candidate_id, OcrDecision.REJECTED_RESOURCE_LIMIT, ("OCR_PIXEL_LIMIT",)))
                    continue
                if derived_bytes + file_bytes > profile.max_derived_bytes:
                    self.metrics.resource("OCR_DERIVED_BYTES_LIMIT")
                    results.append(OcrCandidateResult(candidate.candidate_id, OcrDecision.REJECTED_RESOURCE_LIMIT, ("OCR_DERIVED_BYTES_LIMIT",)))
                    continue
                if memory_estimate > profile.memory_hard_limit_bytes:
                    self.metrics.resource("OCR_MEMORY_LIMIT")
                    results.append(OcrCandidateResult(candidate.candidate_id, OcrDecision.REJECTED_RESOURCE_LIMIT, ("OCR_MEMORY_LIMIT",)))
                    continue
                if memory_estimate > profile.memory_soft_limit_bytes:
                    warnings.append("OCR_MEMORY_SOFT_LIMIT")
                if candidate.scope == "PAGE":
                    pages += 1
                    if pages > profile.max_pages_per_job:
                        self.metrics.resource("OCR_PAGE_LIMIT")
                        results.append(OcrCandidateResult(candidate.candidate_id, OcrDecision.REJECTED_RESOURCE_LIMIT, ("OCR_PAGE_LIMIT",)))
                        continue

                image_hash = _file_sha256(resolved.image_path)
                if image_hash in image_cache:
                    observations = _project_observations(image_cache[image_hash], candidate)
                    reason_codes = (*decision.reason_codes, "OCR_RESULT_REUSED_BY_IMAGE_HASH")
                else:
                    request = OcrRequest(candidate.candidate_id, resolved.image_path, candidate.page_number, candidate.element_id, profile)
                    started = time.monotonic()
                    try:
                        observations = self.engine.recognize(request)
                        self.metrics.recognition("success", time.monotonic() - started)
                    except TimeoutError:
                        self.metrics.recognition("timeout", time.monotonic() - started)
                        results.append(OcrCandidateResult(candidate.candidate_id, OcrDecision.REQUIRED, decision.reason_codes,
                                                          warnings=("OCR_TIMEOUT",)))
                        warnings.append("OCR_TIMEOUT")
                        continue
                    except Exception as exc:
                        self.metrics.recognition("failure", time.monotonic() - started)
                        results.append(OcrCandidateResult(candidate.candidate_id, OcrDecision.REQUIRED, decision.reason_codes,
                                                          warnings=("OCR_ENGINE_FAILED",)))
                        warnings.append("OCR_ENGINE_FAILED")
                        continue
                    image_cache[image_hash] = tuple(observations)
                    reason_codes = decision.reason_codes
                pixels += resolved.pixels
                derived_bytes += file_bytes
                retained = tuple(obs for obs in observations if obs.confidence >= profile.hard_confidence_floor)
                if len(retained) < len(observations):
                    warnings.append("OCR_LOW_CONFIDENCE_DROPPED")
                results.append(OcrCandidateResult(candidate.candidate_id, decision.decision, tuple(reason_codes), retained))
            finally:
                if resolved.cleanup:
                    resolved.image_path.unlink(missing_ok=True)

        native_text_by_page: dict[int | None, list[str]] = {}
        for element in document.elements:
            if element.text:
                page = element.geometry.page_number if element.geometry else None
                native_text_by_page.setdefault(page, []).append(element.text)

        accepted_seen: set[tuple[int | None, str]] = set()
        for result in results:
            candidate = candidate_by_id.get(result.candidate_id)
            if candidate is None:
                continue
            for observation in result.observations:
                quality = "LOW_CONFIDENCE" if observation.confidence < profile.min_confidence else "ACCEPTED"
                native = native_text_by_page.get(observation.page_number, [])
                duplicate_native = any(_similarity(observation.text, text) >= 0.86 for text in native)
                content_key = (observation.page_number, _normalize_for_match(observation.text))
                duplicate_ocr = content_key in accepted_seen and candidate.scope != "EMBEDDED_IMAGE"
                if duplicate_native or duplicate_ocr:
                    self.metrics.reconciliation(ReconciliationAction.DROP_DUPLICATE_OCR.value)
                    continue
                accepted_seen.add(content_key)
                self.metrics.reconciliation(ReconciliationAction.KEEP_OCR.value)
                element_id = hashlib.sha256(
                    f"{document.document_id}\x1f{document.document_version}\x1f{result.candidate_id}\x1f{observation.block_order}\x1f"
                    f"{self.engine.model_identity.bundle_sha256}\x1f{observation.text}".encode("utf-8")
                ).hexdigest()
                accepted.append(DocumentElement(
                    element_id=element_id, type=ElementType.PARAGRAPH, order_index=0, text=observation.text,
                    parent_element_id=candidate.element_id, geometry=observation.geometry,
                    source_locator={"ocrCandidateId": result.candidate_id, "sourceElementId": candidate.element_id,
                                    "pageNumber": observation.page_number, "blockOrder": observation.block_order},
                    role="OCR_TEXT",
                    metadata={"ocrConfidence": observation.confidence, "ocrQuality": quality,
                              "reconciliationAction": ReconciliationAction.KEEP_OCR.value,
                              "ocrEngine": observation.model.engine, "ocrEngineVersion": observation.model.engine_version,
                              "ocrModelId": observation.model.model_id, "ocrArtifactRevision": observation.model.artifact_revision,
                              "ocrBundleSha256": observation.model.bundle_sha256, "ocrProfile": profile.profile_id,
                              "ocrPreprocessingVersion": profile.preprocessing_version,
                              "ocrDecisionPolicyVersion": profile.decision_policy_version,
                              "ocrReconciliationVersion": profile.reconciliation_version})
                )

        inserts_by_parent: dict[str | None, list[DocumentElement]] = {}
        for element in accepted:
            inserts_by_parent.setdefault(element.parent_element_id, []).append(element)
        merged: list[DocumentElement] = []
        for element in document.elements:
            merged.append(element)
            merged.extend(inserts_by_parent.get(element.element_id, []))
        merged.extend(inserts_by_parent.get(None, []))
        merged = [replace(element, order_index=index) for index, element in enumerate(merged)]

        unresolved = any(r.decision in {OcrDecision.REQUIRED_BUT_DISABLED, OcrDecision.REJECTED_RESOURCE_LIMIT} or r.warnings for r in results)
        if unresolved:
            status = QualityStatus.PARTIAL
        elif document.quality.status == QualityStatus.OCR_REQUIRED:
            status = QualityStatus.GOOD if accepted else QualityStatus.LOW_SIGNAL
        else:
            status = document.quality.status
        quality = ParseQuality(status, document.quality.native_text_chars, document.quality.ocr_candidate_count,
                               tuple(dict.fromkeys((*document.quality.warnings, *warnings))), document.quality.page_modes)
        enriched = replace(document, elements=tuple(merged), quality=quality)
        return OcrPipelineResult(enriched, tuple(results), tuple(accepted), _fingerprint(document, profile, self.engine, mode),
                                 pages, pixels, tuple(dict.fromkeys(warnings)))
