from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from astra_indexator.acquisition import AcquiredSource
from astra_indexator.persistence.models import JobEvent

from .coordinator import LeaseLostError, LeaseToken


class AcquisitionCheckpoint:
    """Persist M3 acquisition evidence only under a live M2 lease fence."""

    def record(self, session: Session, token: LeaseToken, source: AcquiredSource) -> None:
        result = session.execute(
            text(
                """
                UPDATE astra_indexator.indexation_job
                   SET source_content_hash = :sha256,
                       source_size_bytes = :size_bytes,
                       source_etag = :etag,
                       source_version_id = :version_id,
                       source_detected_format = :detected_format,
                       source_detected_content_type = :detected_content_type,
                       source_validation_profile = :validation_profile,
                       source_acquired_at = :acquired_at,
                       processing_stage = 'ACQUIRED',
                       updated_at = now()
                 WHERE id = :job_id
                   AND worker_id = :worker_id
                   AND lease_generation = :lease_generation
                   AND status = 'PROCESSING'
                   AND lease_until IS NOT NULL
                   AND lease_until >= now()
                """
            ),
            {
                "job_id": token.job_id,
                "worker_id": token.worker_id,
                "lease_generation": token.lease_generation,
                "sha256": source.sha256,
                "size_bytes": source.size_bytes,
                "etag": source.etag,
                "version_id": source.version_id,
                "detected_format": source.detected_format,
                "detected_content_type": source.detected_content_type,
                "validation_profile": source.validation_profile,
                "acquired_at": source.acquired_at,
            },
        )
        if result.rowcount != 1:
            raise LeaseLostError("cannot install acquisition checkpoint with stale or expired lease")

        session.add(
            JobEvent(
                job_id=token.job_id,
                attempt_id=token.attempt_id,
                event_type="SOURCE_ACQUIRED",
                from_status="PROCESSING",
                to_status="PROCESSING",
                processing_stage="ACQUIRED",
                lease_generation=token.lease_generation,
                details={
                    "workerId": token.worker_id,
                    "detectedFormat": source.detected_format,
                    "sizeBytes": source.size_bytes,
                    "sha256": source.sha256,
                    "validationProfile": source.validation_profile,
                    "warnings": list(source.warnings),
                },
            )
        )
