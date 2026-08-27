from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy.orm import Session

from astra_indexator.application.astravector_delivery_coordinator import AstraVectorDeliveryInput
from astra_indexator.application.coordinator import ClaimedJob
from astra_indexator.application.durable_append_delivery import DurableAppendLeaseFence
from astra_indexator.application.prepared_artifact_replay import PreparedArtifactReplayService
from astra_indexator.application.prepared_artifact_wiring import PreparedArtifactDeliveryInputFactory
from astra_indexator.persistence.models import IndexationJob
from astra_indexator.persistence.prepared_artifacts import PreparedArtifactCheckpoint
from astra_indexator.prepared_artifacts.model import ArtifactCompatibility, ReplayDecision


class PreparedArtifactResumeError(RuntimeError):
    pass


class PreparedArtifactReprocessRequired(PreparedArtifactResumeError):
    """The durable M7 artifact is absent or incompatible and upstream processing is required."""


class PreparedArtifactLineageMismatch(PreparedArtifactResumeError):
    """The durable artifact checkpoint no longer matches immutable job delivery intent."""


@dataclass(slots=True)
class DurablePreparedArtifactResumeService:
    """M8.3.2 recovery boundary from a verified M7 artifact to M8.2 delivery input.

    This service never invokes acquisition, parser, OCR, normalization or splitting. It may only
    reuse a M7 artifact after the current lease is proven, M7 replay validation succeeds and the
    checkpoint's immutable AccessZone/TTL lineage still matches the job.
    """

    session_factory: Callable[[], Session]
    replay_service: PreparedArtifactReplayService
    input_factory: PreparedArtifactDeliveryInputFactory = field(
        default_factory=PreparedArtifactDeliveryInputFactory
    )
    lease_fence: DurableAppendLeaseFence = field(default_factory=DurableAppendLeaseFence)

    def resume(
        self,
        claimed: ClaimedJob,
        *,
        expected: ArtifactCompatibility,
        source_file_name: str = "",
        metadata: dict[str, str] | None = None,
    ) -> AstraVectorDeliveryInput:
        with self.session_factory() as session:
            with session.begin():
                self.lease_fence.assert_owned(session, claimed.token)
                job = session.get(IndexationJob, claimed.token.job_id)
                checkpoint = session.get(PreparedArtifactCheckpoint, claimed.token.job_id)
                if job is None:
                    raise PreparedArtifactResumeError("claimed IndexationJob no longer exists")
                if checkpoint is None:
                    raise PreparedArtifactReprocessRequired(
                        "no durable M7 prepared-artifact checkpoint is available"
                    )
                self._assert_delivery_lineage(job, checkpoint)
                decision, artifact = self.replay_service.replay(
                    session,
                    job_id=job.id,
                    expected=expected,
                )
                if decision is not ReplayDecision.REPLAY or artifact is None:
                    raise PreparedArtifactReprocessRequired(
                        "M7 prepared artifact is incompatible with the expected processing profile"
                    )
                return self.input_factory.build(
                    artifact,
                    document_id=job.document_id,
                    document_version=job.document_version,
                    source_file_name=source_file_name or job.source_file_name or "document",
                    metadata=metadata,
                )

    @staticmethod
    def _assert_delivery_lineage(
        job: IndexationJob,
        checkpoint: PreparedArtifactCheckpoint,
    ) -> None:
        if checkpoint.source_sha256 != job.source_content_hash:
            raise PreparedArtifactLineageMismatch(
                "prepared artifact source hash differs from durable job source hash"
            )
        if checkpoint.requested_access_zone_id != job.requested_access_zone_id:
            raise PreparedArtifactLineageMismatch(
                "prepared artifact accessZoneId differs from durable job delivery intent"
            )
        if checkpoint.requested_access_zone_code != job.requested_access_zone_code:
            raise PreparedArtifactLineageMismatch(
                "prepared artifact accessZoneCode differs from durable job delivery intent"
            )
        if checkpoint.requested_ttl_days != job.requested_ttl_days:
            raise PreparedArtifactLineageMismatch(
                "prepared artifact ttlDays differs from durable job delivery intent"
            )
