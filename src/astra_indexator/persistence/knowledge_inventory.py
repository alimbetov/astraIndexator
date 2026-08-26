from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from astra_indexator.domain.contracts import AccessZoneCode
from astra_indexator.domain.lifecycle import DocumentLifecycleState

from .lifecycle_models import DocumentVersionLifecycle
from .models import DeliveryBatch, DeliveryCheckpoint, IndexationJob
from .prepared_artifacts import PreparedArtifactCheckpoint


class KnowledgeInventoryProjectionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class KnowledgeInventoryProjection:
    document_id: UUID
    document_version: int
    job_id: UUID
    lifecycle_state: DocumentLifecycleState
    is_current: bool
    knowledge_type: str | None
    requested_access_zone_code: str | None
    requested_access_zone_id: UUID | None
    resolved_access_zone_id: UUID | None
    requested_ttl_days: int | None
    source_file_name: str | None
    storage_object_id: UUID | None
    storage_object_name: str | None
    source_uri: str | None
    source_content_hash: str | None
    processing_fingerprint: str | None
    logical_fragment_count: int | None
    logical_block_count: int | None
    ingestion_session_id: UUID | None
    vector_state: str | None
    searchable: bool
    ready_to_activate: bool
    expected_bindings: int | None
    synced_bindings: int | None
    ttl_state: str | None
    effective_expires_at: datetime | None
    activated_at: datetime | None
    superseded_at: datetime | None
    cancelled_at: datetime | None
    deleted_at: datetime | None
    failed_at: datetime | None
    last_verified_at: datetime | None

    def __post_init__(self) -> None:
        if self.document_version <= 0:
            raise ValueError("document_version must be positive")
        if self.requested_access_zone_code is not None:
            AccessZoneCode(self.requested_access_zone_code)
        if self.requested_access_zone_code is None and self.requested_access_zone_id is None:
            raise ValueError("requested AccessZone selector is required")
        if self.requested_ttl_days is not None and self.requested_ttl_days < 0:
            raise ValueError("requested_ttl_days must be non-negative")
        if bool(self.storage_object_id) != bool(self.storage_object_name):
            raise ValueError(
                "storage_object_id and storage_object_name must either both be set or both be unset"
            )
        if self.storage_object_name is not None and not self.storage_object_name.strip():
            raise ValueError("storage_object_name must not be blank")
        if self.source_file_name is not None and not self.source_file_name.strip():
            raise ValueError("source_file_name must not be blank")
        for field_name, value in (
            ("logical_fragment_count", self.logical_fragment_count),
            ("logical_block_count", self.logical_block_count),
            ("expected_bindings", self.expected_bindings),
            ("synced_bindings", self.synced_bindings),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if self.lifecycle_state is DocumentLifecycleState.ACTIVE and not self.is_current:
            raise ValueError("ACTIVE inventory projection must be current")
        if self.lifecycle_state is not DocumentLifecycleState.ACTIVE and self.is_current:
            raise ValueError("only ACTIVE inventory projection may be current")
        if self.searchable and self.vector_state is None:
            raise ValueError("searchable projection requires vector_state evidence")


class KnowledgeInventoryRepository:
    """Rebuildable operational projection over durable AstraIndexator authorities.

    This repository never reads AstraVector-owned PostgreSQL tables. Downstream
    evidence comes only from M8-owned DeliveryCheckpoint fields populated through
    the public AstraVector gRPC contract. Public source provenance and internal
    storage identity remain separate according to the M9 source-provenance freeze.
    """

    def rebuild(
        self,
        session: Session,
        *,
        document_id: UUID,
        document_version: int,
    ) -> KnowledgeInventoryProjection:
        lifecycle = session.get(
            DocumentVersionLifecycle,
            {"document_id": document_id, "document_version": document_version},
        )
        if lifecycle is None:
            raise KnowledgeInventoryProjectionError("document lifecycle row does not exist")

        job = session.get(IndexationJob, lifecycle.job_id)
        if job is None:
            raise KnowledgeInventoryProjectionError("indexation job does not exist")

        checkpoint = session.get(DeliveryCheckpoint, lifecycle.job_id)
        prepared = session.get(PreparedArtifactCheckpoint, lifecycle.job_id)
        block_count = session.execute(
            select(func.coalesce(func.sum(DeliveryBatch.block_count), 0)).where(
                DeliveryBatch.job_id == lifecycle.job_id
            )
        ).scalar_one()

        raw_vector_state = checkpoint.vector_state_raw if checkpoint is not None else None
        ready_to_activate = raw_vector_state in {
            "READY_TO_ACTIVATE",
            "OPERATION_STATE_READY_TO_ACTIVATE",
        }

        projection = KnowledgeInventoryProjection(
            document_id=lifecycle.document_id,
            document_version=lifecycle.document_version,
            job_id=lifecycle.job_id,
            lifecycle_state=DocumentLifecycleState(lifecycle.state),
            is_current=lifecycle.is_current,
            knowledge_type=job.knowledge_type,
            requested_access_zone_code=lifecycle.requested_access_zone_code,
            requested_access_zone_id=lifecycle.requested_access_zone_id,
            resolved_access_zone_id=(
                checkpoint.resolved_access_zone_id
                if checkpoint is not None
                else lifecycle.resolved_access_zone_id
            ),
            requested_ttl_days=lifecycle.requested_ttl_days,
            source_file_name=job.source_file_name,
            storage_object_id=job.storage_object_id,
            storage_object_name=job.storage_object_name,
            source_uri=job.source_uri,
            source_content_hash=job.source_content_hash,
            processing_fingerprint=job.processing_fingerprint,
            logical_fragment_count=prepared.fragment_count if prepared is not None else None,
            logical_block_count=int(block_count) if block_count is not None else None,
            ingestion_session_id=(checkpoint.ingestion_session_id if checkpoint is not None else None),
            vector_state=raw_vector_state,
            searchable=bool(checkpoint.searchable) if checkpoint is not None else False,
            ready_to_activate=ready_to_activate,
            expected_bindings=(checkpoint.expected_bindings if checkpoint is not None else None),
            synced_bindings=(checkpoint.synced_bindings if checkpoint is not None else None),
            ttl_state=None,
            effective_expires_at=None,
            activated_at=lifecycle.activated_at,
            superseded_at=lifecycle.superseded_at,
            cancelled_at=lifecycle.cancelled_at,
            deleted_at=lifecycle.deleted_at,
            failed_at=lifecycle.failed_at,
            last_verified_at=(checkpoint.last_reconciled_at if checkpoint is not None else None),
        )
        self.upsert(session, projection)
        return projection

    def upsert(self, session: Session, projection: KnowledgeInventoryProjection) -> None:
        params = {
            "document_id": projection.document_id,
            "document_version": projection.document_version,
            "job_id": projection.job_id,
            "knowledge_type": projection.knowledge_type,
            "access_zone_code": projection.requested_access_zone_code,
            "access_zone_id": projection.resolved_access_zone_id,
            "requested_access_zone_code": projection.requested_access_zone_code,
            "requested_access_zone_id": projection.requested_access_zone_id,
            "resolved_access_zone_id": projection.resolved_access_zone_id,
            "requested_ttl_days": projection.requested_ttl_days,
            "source_file_name": projection.source_file_name,
            "storage_object_id": projection.storage_object_id,
            "storage_object_name": projection.storage_object_name,
            "source_uri": projection.source_uri,
            "source_content_hash": projection.source_content_hash,
            "processing_fingerprint": projection.processing_fingerprint,
            "logical_fragment_count": projection.logical_fragment_count,
            "logical_block_count": projection.logical_block_count,
            "lifecycle_state": projection.lifecycle_state.value,
            "is_current": projection.is_current,
            "ingestion_session_id": projection.ingestion_session_id,
            "vector_state": projection.vector_state,
            "searchable": projection.searchable,
            "ready_to_activate": projection.ready_to_activate,
            "expected_bindings": projection.expected_bindings,
            "synced_bindings": projection.synced_bindings,
            "ttl_state": projection.ttl_state,
            "effective_expires_at": projection.effective_expires_at,
            "activated_at": projection.activated_at,
            "superseded_at": projection.superseded_at,
            "cancelled_at": projection.cancelled_at,
            "deleted_at": projection.deleted_at,
            "failed_at": projection.failed_at,
            "last_verified_at": projection.last_verified_at,
        }
        session.execute(
            text(
                """
                INSERT INTO astra_indexator.knowledge_inventory (
                    document_id, document_version, job_id, knowledge_type,
                    access_zone_code, access_zone_id,
                    requested_access_zone_code, requested_access_zone_id,
                    resolved_access_zone_id, requested_ttl_days,
                    source_file_name, storage_object_id, storage_object_name, source_uri,
                    source_content_hash, processing_fingerprint,
                    logical_fragment_count, logical_block_count,
                    lifecycle_state, is_current, ingestion_session_id,
                    vector_state, searchable, ready_to_activate,
                    expected_bindings, synced_bindings,
                    ttl_state, effective_expires_at,
                    activated_at, superseded_at, cancelled_at, deleted_at, failed_at,
                    last_verified_at
                ) VALUES (
                    :document_id, :document_version, :job_id, :knowledge_type,
                    :access_zone_code, :access_zone_id,
                    :requested_access_zone_code, :requested_access_zone_id,
                    :resolved_access_zone_id, :requested_ttl_days,
                    :source_file_name, :storage_object_id, :storage_object_name, :source_uri,
                    :source_content_hash, :processing_fingerprint,
                    :logical_fragment_count, :logical_block_count,
                    :lifecycle_state, :is_current, :ingestion_session_id,
                    :vector_state, :searchable, :ready_to_activate,
                    :expected_bindings, :synced_bindings,
                    :ttl_state, :effective_expires_at,
                    :activated_at, :superseded_at, :cancelled_at, :deleted_at, :failed_at,
                    :last_verified_at
                )
                ON CONFLICT (document_id, document_version) DO UPDATE SET
                    job_id = EXCLUDED.job_id,
                    knowledge_type = EXCLUDED.knowledge_type,
                    access_zone_code = EXCLUDED.access_zone_code,
                    access_zone_id = EXCLUDED.access_zone_id,
                    requested_access_zone_code = EXCLUDED.requested_access_zone_code,
                    requested_access_zone_id = EXCLUDED.requested_access_zone_id,
                    resolved_access_zone_id = EXCLUDED.resolved_access_zone_id,
                    requested_ttl_days = EXCLUDED.requested_ttl_days,
                    source_file_name = EXCLUDED.source_file_name,
                    storage_object_id = EXCLUDED.storage_object_id,
                    storage_object_name = EXCLUDED.storage_object_name,
                    source_uri = EXCLUDED.source_uri,
                    source_content_hash = EXCLUDED.source_content_hash,
                    processing_fingerprint = EXCLUDED.processing_fingerprint,
                    logical_fragment_count = EXCLUDED.logical_fragment_count,
                    logical_block_count = EXCLUDED.logical_block_count,
                    lifecycle_state = EXCLUDED.lifecycle_state,
                    is_current = EXCLUDED.is_current,
                    ingestion_session_id = EXCLUDED.ingestion_session_id,
                    vector_state = EXCLUDED.vector_state,
                    searchable = EXCLUDED.searchable,
                    ready_to_activate = EXCLUDED.ready_to_activate,
                    expected_bindings = EXCLUDED.expected_bindings,
                    synced_bindings = EXCLUDED.synced_bindings,
                    ttl_state = COALESCE(EXCLUDED.ttl_state, astra_indexator.knowledge_inventory.ttl_state),
                    effective_expires_at = COALESCE(
                        EXCLUDED.effective_expires_at,
                        astra_indexator.knowledge_inventory.effective_expires_at
                    ),
                    activated_at = EXCLUDED.activated_at,
                    superseded_at = EXCLUDED.superseded_at,
                    cancelled_at = EXCLUDED.cancelled_at,
                    deleted_at = EXCLUDED.deleted_at,
                    failed_at = EXCLUDED.failed_at,
                    last_verified_at = EXCLUDED.last_verified_at
                """
            ),
            params,
        )

    def get(
        self,
        session: Session,
        *,
        document_id: UUID,
        document_version: int,
    ) -> dict[str, object] | None:
        row = session.execute(
            text(
                "SELECT * FROM astra_indexator.knowledge_inventory "
                "WHERE document_id = :document_id AND document_version = :document_version"
            ),
            {"document_id": document_id, "document_version": document_version},
        ).mappings().one_or_none()
        return dict(row) if row is not None else None
