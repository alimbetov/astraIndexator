from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from astra_indexator.application.astravector_delivery_coordinator import AstraVectorDeliveryInput
from astra_indexator.application.prepared_artifact_delivery import PreparedArtifactDeliveryMapper
from astra_indexator.prepared_artifacts.model import PreparedArtifact


class PreparedArtifactIdentityMismatch(ValueError):
    """A prepared artifact belongs to a different document/version than the claimed job."""


@dataclass(frozen=True, slots=True)
class PreparedArtifactDeliveryInputFactory:
    """Qualified M7 -> M8.2 coordinator input boundary.

    This component performs no storage reads, retries, checkpoints, leases, or remote calls. The
    caller first obtains a verified M7 ``PreparedArtifact`` through the M7 replay service, then this
    factory asserts document identity and maps canonical fragments to M8.2 ``LogicalBlock`` DTOs.
    """

    mapper: PreparedArtifactDeliveryMapper = field(default_factory=PreparedArtifactDeliveryMapper)

    def build(
        self,
        artifact: PreparedArtifact,
        *,
        document_id: UUID,
        document_version: int,
        source_file_name: str = "",
        metadata: dict[str, str] | None = None,
    ) -> AstraVectorDeliveryInput:
        identity = artifact.manifest.identity
        if identity.document_id != document_id or identity.document_version != document_version:
            raise PreparedArtifactIdentityMismatch(
                "M7 prepared artifact identity does not match the claimed M8.2 delivery job"
            )
        return AstraVectorDeliveryInput(
            logical_blocks=self.mapper.logical_blocks(artifact),
            source_file_name=source_file_name,
            metadata=dict(metadata or {}),
        )
