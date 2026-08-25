from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from astra_indexator.application.coordinator import LeaseLostError, LeaseToken
from astra_indexator.persistence.models import IndexationJob, JobEvent
from astra_indexator.persistence.prepared_artifacts import PreparedArtifactCheckpoint
from astra_indexator.prepared_artifacts.model import PublishedArtifact


class PreparedArtifactIdentityMismatch(RuntimeError):
    pass


class MissingDeliveryIntent(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class InstalledArtifactCheckpoint:
    artifact_id: str
    manifest_uri: str
    manifest_sha256: str
    compatibility_sha256: str
    requested_access_zone_id: UUID | None
    requested_access_zone_code: str | None
    requested_ttl_days: int


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
            raise PreparedArtifactIdentityMismatch(
                "prepared artifact identity does not match locked indexation job"
            )

        requested_zone_id = job.requested_access_zone_id or job.access_zone_id
        requested_zone_code = job.requested_access_zone_code or job.access_zone_code
        if requested_zone_id is None and requested_zone_code is None:
            raise MissingDeliveryIntent("indexation job has no AccessZone producer intent")
        ttl_days = job.requested_ttl_days if job.requested_ttl_days is not None else 0
        if ttl_days < 0:
            raise MissingDeliveryIntent("indexation job has invalid negative TTL intent")

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
            "requested_access_zone_id": requested_zone_id,
            "requested_access_zone_code": requested_zone_code,
            "requested_ttl_days": ttl_days,
        }
        if checkpoint is None:
            checkpoint = PreparedArtifactCheckpoint(job_id=token.job_id, **values)
            session.add(checkpoint)
        else:
            # Producer delivery intent is immutable across artifact replacement/replay.
            if checkpoint.requested_access_zone_id not in (None, requested_zone_id):
                raise PreparedArtifactIdentityMismatch(
                    "AccessZone ID changed across checkpoint replay"
                )
            if checkpoint.requested_access_zone_code not in (None, requested_zone_code):
                raise PreparedArtifactIdentityMismatch(
                    "AccessZone code changed across checkpoint replay"
                )
            if checkpoint.requested_ttl_days not in (None, ttl_days):
                raise PreparedArtifactIdentityMismatch(
                    "TTL intent changed across checkpoint replay"
                )
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
                    "requestedAccessZoneId": str(requested_zone_id) if requested_zone_id else None,
                    "requestedAccessZoneCode": requested_zone_code,
                    "requestedTtlDays": ttl_days,
                },
            )
        )
        session.flush()
        return InstalledArtifactCheckpoint(
            artifact_id=manifest.artifact_id,
            manifest_uri=resolved_uri,
            manifest_sha256=published.manifest_sha256,
            compatibility_sha256=manifest.compatibility_sha256,
            requested_access_zone_id=requested_zone_id,
            requested_access_zone_code=requested_zone_code,
            requested_ttl_days=ttl_days,
        )
