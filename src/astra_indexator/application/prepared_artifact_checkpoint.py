from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from astra_indexator.application.coordinator import LeaseLostError, LeaseToken
from astra_indexator.persistence.models import IndexationJob, JobEvent
from astra_indexator.persistence.prepared_artifacts import PreparedArtifactCheckpoint
from astra_indexator.prepared_artifacts.model import ArtifactManifest


@dataclass(frozen=True, slots=True)
class InstalledArtifactCheckpoint:
    artifact_id: str
    manifest_uri: str
    compatibility_sha256: str


class PreparedArtifactCheckpointService:
    """Installs the authoritative replay pointer under the M2 fencing predicate."""

    def assert_current_lease(self, session: Session, token: LeaseToken) -> None:
        current = session.execute(
            select(IndexationJob.id)
            .where(
                IndexationJob.id == token.job_id,
                IndexationJob.worker_id == token.worker_id,
                IndexationJob.lease_generation == token.lease_generation,
                IndexationJob.status == "PROCESSING",
                IndexationJob.lease_until.is_not(None),
                IndexationJob.lease_until >= func.now(),
            )
        ).scalar_one_or_none()
        if current is None:
            raise LeaseLostError("lease is stale or expired")

    def install(
        self,
        session: Session,
        token: LeaseToken,
        *,
        manifest: ArtifactManifest,
        manifest_uri: str,
    ) -> InstalledArtifactCheckpoint:
        # Lock the job row so the lease cannot be reclaimed between validation
        # and checkpoint installation inside this transaction.
        job_id = session.execute(
            select(IndexationJob.id)
            .where(
                IndexationJob.id == token.job_id,
                IndexationJob.worker_id == token.worker_id,
                IndexationJob.lease_generation == token.lease_generation,
                IndexationJob.status == "PROCESSING",
                IndexationJob.lease_until.is_not(None),
                IndexationJob.lease_until >= func.now(),
            )
            .with_for_update()
        ).scalar_one_or_none()
        if job_id is None:
            raise LeaseLostError("lease is stale or expired")

        checkpoint = session.get(PreparedArtifactCheckpoint, token.job_id)
        if checkpoint is None:
            checkpoint = PreparedArtifactCheckpoint(
                job_id=token.job_id,
                artifact_id=manifest.artifact_id,
                manifest_uri=manifest_uri,
                source_sha256=manifest.identity.source_sha256,
                compatibility_sha256=manifest.compatibility_sha256,
                element_count=manifest.total_element_count,
                fragment_count=manifest.total_fragment_count,
                lease_generation=token.lease_generation,
            )
            session.add(checkpoint)
        else:
            checkpoint.artifact_id = manifest.artifact_id
            checkpoint.manifest_uri = manifest_uri
            checkpoint.source_sha256 = manifest.identity.source_sha256
            checkpoint.compatibility_sha256 = manifest.compatibility_sha256
            checkpoint.element_count = manifest.total_element_count
            checkpoint.fragment_count = manifest.total_fragment_count
            checkpoint.lease_generation = token.lease_generation
            checkpoint.updated_at = func.now()

        session.add(
            JobEvent(
                job_id=token.job_id,
                attempt_id=token.attempt_id,
                event_type="PREPARED_ARTIFACT_COMMITTED",
                processing_stage="PREPARED",
                lease_generation=token.lease_generation,
                details={
                    "artifactId": manifest.artifact_id,
                    "manifestUri": manifest_uri,
                    "compatibilitySha256": manifest.compatibility_sha256,
                    "elementCount": manifest.total_element_count,
                    "fragmentCount": manifest.total_fragment_count,
                },
            )
        )
        session.flush()
        return InstalledArtifactCheckpoint(
            artifact_id=manifest.artifact_id,
            manifest_uri=manifest_uri,
            compatibility_sha256=manifest.compatibility_sha256,
        )
