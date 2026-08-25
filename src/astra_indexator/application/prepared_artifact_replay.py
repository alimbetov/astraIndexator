from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from astra_indexator.persistence.prepared_artifacts import PreparedArtifactCheckpoint
from astra_indexator.prepared_artifacts import (
    ArtifactCompatibility,
    ArtifactCorruptionError,
    PreparedArtifact,
    PreparedArtifactReader,
    ReplayDecision,
)


class PreparedArtifactReplayService:
    """Restart-safe checkpoint → manifest → verified parts replay."""

    def __init__(self, reader: PreparedArtifactReader) -> None:
        self._reader = reader

    def replay(
        self,
        session: Session,
        *,
        job_id: UUID,
        expected: ArtifactCompatibility,
    ) -> tuple[ReplayDecision, PreparedArtifact | None]:
        checkpoint = session.get(PreparedArtifactCheckpoint, job_id)
        if checkpoint is None:
            return ReplayDecision.REPROCESS, None
        manifest = self._reader.load_manifest(
            self._manifest_key(checkpoint.manifest_uri),
            expected_sha256=checkpoint.manifest_sha256,
        )
        if manifest.artifact_id != checkpoint.artifact_id:
            raise ArtifactCorruptionError("checkpoint artifactId does not match manifest")
        if manifest.identity.source_sha256 != checkpoint.source_sha256:
            raise ArtifactCorruptionError("checkpoint sourceSha256 does not match manifest")
        if manifest.compatibility_sha256 != checkpoint.compatibility_sha256:
            raise ArtifactCorruptionError("checkpoint compatibility digest does not match manifest")
        if self._reader.replay_decision(manifest, expected) is ReplayDecision.REPROCESS:
            return ReplayDecision.REPROCESS, None
        return ReplayDecision.REPLAY, self._reader.load(manifest)

    @staticmethod
    def _manifest_key(uri: str) -> str:
        if uri.startswith("seaweed://"):
            remainder = uri[len("seaweed://") :]
            _, separator, key = remainder.partition("/")
            if not separator or not key:
                raise ArtifactCorruptionError("invalid Seaweed prepared artifact URI")
            return key
        return uri
