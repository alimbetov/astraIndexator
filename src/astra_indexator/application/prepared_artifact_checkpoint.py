from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from astra_indexator.application.coordinator import LeaseLostError, LeaseToken
from astra_indexator.persistence.models import IndexationJob, JobEvent
from astra_indexator.persistence.prepared_artifacts import PreparedArtifactCheckpoint
from astra_indexator.prepared_artifacts.model import ArtifactManifest, PublishedArtifact


class PreparedArtifactIdentityMismatch(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class InstalledArtifactCheckpoint:
    artifact_id: str
    manifest_uri: str
    manifest_sha256: str
    compatibility_sha256: str


class PreparedArtifactCheckpointService:
    """Installs the authoritative replay pointer under the M2 fencing predicate."""

    def assert_current_lease(self, session: Session, token: LeaseToken) -> None:
        current = session.execute(
            select(IndexationJob.id).where(
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
        published: PublishedArtifact,
        manifest_uri: str | None = None,
    ) -> InstalledArtifactCheckpoint:
        job = session.execute(
            select(IndexationJob)
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
        if job is None:
            raise LeaseLostError("lease is stale or expired")

        manifest = published.manifest
        if (
            manifest.identity.document_id != job.document_id
            or manifest.identity.document_version != job.document_version
            or job.source_content_hash is None
            or manifest.identity.source_sha256 != job.source_content_hash
        ):
            raise PreparedArtifactIdentityMismatch("prepared artifact identity does not match locked indexation job")

        resolved_uri = manifest_uri or published.manifest_key
        checkpoint = session.get(PreparedArtifactCheckpoint, token.job_id)
        values = {
            "artifact_id": manifest.artifact_id,
            "manifest_uri": resolved_uri,
            "manifest_sha256": published.manifest_sha256,
            "source_sha256": manifest.identity.source_sha256,
            "compatibility_sha256": manifest.compatibility_sha256,
            "element_count": manifest.total_element_count,
            "fragment_count": manifest.total_fragment_count,
            "lease_generation": token.lease_generation,
        }
        if checkpoint is None:
            checkpoint = PreparedArtifactCheckpoint(job_id=token.job_id, **values)
            session.add(checkpoint)
        else:
            for name, value in values.items():
                setattr(checkpoint, name, value)
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
                    "manifestUri": resolved_uri,
                    "manifestSha256": published.manifest_sha256,
                    "compatibilitySha256": manifest.compatibility_sha256,
                    "elementCount": manifest.total_element_count,
                    "fragmentCount": manifest.total_fragment_count,
                },
            )
        )
        session.flush()
        return InstalledArtifactCheckpoint(
            artifact_id=manifest.artifact_id,
            manifest_uri=resolved_uri,
            manifest_sha256=published.manifest_sha256,
            compatibility_sha256=manifest.compatibility_sha256,
        )
