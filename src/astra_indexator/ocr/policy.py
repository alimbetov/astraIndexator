from __future__ import annotations

from dataclasses import dataclass

from astra_indexator.parser import OcrCandidate, ParsedDocument, QualityStatus

from .model import OcrDecision, OcrMode, OcrProfile


@dataclass(frozen=True, slots=True)
class OcrDecisionResult:
    decision: OcrDecision
    reason_codes: tuple[str, ...]


class OcrDecisionPolicy:
    def _if_needed(self, *, document: ParsedDocument, candidate: OcrCandidate) -> OcrDecisionResult:
        if candidate.scope not in {"PAGE", "REGION", "EMBEDDED_IMAGE"}:
            return OcrDecisionResult(OcrDecision.UNSUPPORTED, ("UNSUPPORTED_CANDIDATE_SCOPE",))

        page_mode = None
        if candidate.page_number is not None and candidate.page_number <= len(document.quality.page_modes):
            page_mode = document.quality.page_modes[candidate.page_number - 1]
        if candidate.scope == "PAGE":
            if page_mode == "NATIVE_TEXT" and candidate.reason not in {"standalone_image", "low_native_text"}:
                return OcrDecisionResult(OcrDecision.NOT_REQUIRED, ("TRUSTED_NATIVE_PAGE",))
            return OcrDecisionResult(OcrDecision.REQUIRED, (candidate.reason, page_mode or "PAGE_CANDIDATE"))
        if candidate.scope == "REGION":
            return OcrDecisionResult(OcrDecision.REQUIRED, (candidate.reason, "REGION_CANDIDATE"))
        return OcrDecisionResult(OcrDecision.REQUIRED, (candidate.reason, "EMBEDDED_IMAGE_CANDIDATE"))

    def decide(
        self,
        *,
        document: ParsedDocument,
        candidate: OcrCandidate,
        mode: OcrMode,
        profile: OcrProfile,
    ) -> OcrDecisionResult:
        if candidate.scope not in {"PAGE", "REGION", "EMBEDDED_IMAGE"}:
            return OcrDecisionResult(OcrDecision.UNSUPPORTED, ("UNSUPPORTED_CANDIDATE_SCOPE",))
        if mode == OcrMode.FORCE:
            return OcrDecisionResult(OcrDecision.REQUIRED, ("OCR_FORCE", candidate.reason))

        needed = self._if_needed(document=document, candidate=candidate)
        if mode == OcrMode.DISABLED:
            # M4 intentionally allows optional embedded-image candidates in an otherwise
            # healthy native document. Disabling OCR must not downgrade the whole document
            # merely because such an advisory candidate exists.
            if candidate.scope == "EMBEDDED_IMAGE" and document.quality.status != QualityStatus.OCR_REQUIRED:
                return OcrDecisionResult(OcrDecision.NOT_REQUIRED, ("OCR_DISABLED_OPTIONAL_IMAGE", candidate.reason))
            if needed.decision == OcrDecision.REQUIRED:
                return OcrDecisionResult(OcrDecision.REQUIRED_BUT_DISABLED, ("OCR_DISABLED", *needed.reason_codes))
            return needed
        return needed
