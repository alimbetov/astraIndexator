from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from astra_indexator.domain.lifecycle import DocumentLifecycleState

from .lifecycle_models import DocumentVersionLifecycle
from .models import DeliveryBatch, DeliveryCheckpoint, IndexationJob
from .prepared_artifacts import PreparedArtifactCheckpoint

SCHEMA = "astra_indexator"


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
    source_uri: str
    source_content_hash: str | None
    processing_fingerprint: str | None
    ingestion_session_id: UUID | None
    logical_fragment_count: int | None
    logical_block_count: int | None
    vector_state: str | None
    searchable: bool
    ready_to_activate: bool
    expected_bindings: int | None
    synced_bindings: int | None
    raw_ttl_state: str | None
    effective_expires_at: datetime | None
    ready_at: datetime | None
    cancel_requested_at: datetime | None
    activated_at: datetime | None
    superseded_at: datetime | None
    cancelled_at: datetime | None
    delete_requested_at: datetime | None
    deleted_at: datetime | None
    failed_at: datetime | None
    last_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.document_version <= 0:
            raise ValueError("document_version must be positive")
        if self.requested_access_zone_code is None and self.requested_access_zone_id is None:
            raise ValueError("one requested access-zone selector is required")
        if self.requested_access_zone_code is not None:
            code = self.requested_access_zone_code
            if len(code) != 4 or not code.isascii() or not code.isdigit():
                raise ValueError("requested_access_zone_code must match ^[0-9]{4}$")
        if self.requested_ttl_days is not None and self.requested_ttl_days < 0:
            raise ValueError("requested_ttl_days must be non-negative")
        if bool(self.storage_object_id) != bool(self.storage_object_name):
            raise ValueError(
                "storage_object_id and storage_object_name must both be set or both be unset"
            )
        if self.storage_object_name is not None and not self.storage_object_name.strip():
            raise ValueError("storage_object_name must not be blank")
        if self.source_file_name is not None and not self.source_file_name.strip():
            raise ValueError("source_file_name must not be blank")
        if not self.source_uri.strip():
            raise ValueError("source_uri must not be blank")
        if self.is_current != (self.lifecycle_state is DocumentLifecycleState.ACTIVE):
            raise ValueError("is_current is true only for ACTIVE lifecycle state")
        for field_name, value in (
            ("logical_fragment_count", self.logical_fragment_count),
            ("logical_block_count", self.logical_block_count),
            ("expected_bindings", self.expected_bindings),
            ("synced_bindings", self.synced_bindings),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative")


class KnowledgeInventoryRepository:
    """Rebuildable M9 projection over AstraIndexator-owned durable authorities.

    The repository never reads AstraVector-owned PostgreSQL tables. Downstream identity,
    vector state and searchability come only from M8 DeliveryCheckpoint evidence populated
    through the public AstraVector gRPC facade.
    """

    def rebuild(
        self,
        session: Session,
        *,
        document_id: UUID,
        document_version: int,
    ) -> KnowledgeInventoryProjection:
        if document_version <= 0:
            raise ValueError("document_version must be positive")

        job = session.execute(
            select(IndexationJob).where(
                IndexationJob.document_id == document_id,
                IndexationJob.document_version == document_version,
            )
        ).scalar_one_or_none()
        if job is None:
            raise KnowledgeInventoryProjectionError("IndexationJob does not exist")

        lifecycle = session.execute(
            select(DocumentVersionLifecycle).where(
                DocumentVersionLifecycle.document_id == document_id,
                DocumentVersionLifecycle.document_version == document_version,
            )
        ).scalar_one_or_none()
        if lifecycle is None:
            raise KnowledgeInventoryProjectionError("DocumentVersionLifecycle does not exist")
        if lifecycle.job_id != job.id:
            raise KnowledgeInventoryProjectionError(
                "lifecycle job_id does not match canonical IndexationJob"
            )

        prepared = session.get(PreparedArtifactCheckpoint, job.id)
        delivery = session.get(DeliveryCheckpoint, job.id)
        block_count = int(
            session.execute(
                select(func.coalesce(func.sum(DeliveryBatch.block_count), 0)).where(
                    DeliveryBatch.job_id == job.id,
                    DeliveryBatch.status == "ACCEPTED",
                )
            ).scalar_one()
        )

        if (
            lifecycle.requested_access_zone_code != job.requested_access_zone_code
            or lifecycle.requested_access_zone_id != job.requested_access_zone_id
            or lifecycle.requested_ttl_days != job.requested_ttl_days
        ):
            raise KnowledgeInventoryProjectionError(
                "immutable producer intent differs between lifecycle and IndexationJob"
            )
        if prepared is not None and (
            prepared.requested_access_zone_code != job.requested_access_zone_code
            or prepared.requested_access_zone_id != job.requested_access_zone_id
            or prepared.requested_ttl_days != job.requested_ttl_days
        ):
            raise KnowledgeInventoryProjectionError(
                "prepared artifact producer intent differs from IndexationJob"
            )

        resolved_zone_id = lifecycle.resolved_access_zone_id
        if delivery is not None and delivery.resolved_access_zone_id is not None:
            if (
                resolved_zone_id is not None
                and resolved_zone_id != delivery.resolved_access_zone_id
            ):
                raise KnowledgeInventoryProjectionError(
                    "resolved accessZoneId differs between lifecycle and delivery evidence"
                )
            resolved_zone_id = delivery.resolved_access_zone_id

        now = session.execute(select(func.now())).scalar_one()
        projection = KnowledgeInventoryProjection(
            document_id=document_id,
            document_version=document_version,
            job_id=job.id,
            lifecycle_state=DocumentLifecycleState(lifecycle.state),
            is_current=bool(lifecycle.is_current),
            knowledge_type=job.knowledge_type,
            requested_access_zone_code=job.requested_access_zone_code,
            requested_access_zone_id=job.requested_access_zone_id,
            resolved_access_zone_id=resolved_zone_id,
            requested_ttl_days=job.requested_ttl_days,
            source_file_name=job.source_file_name,
            storage_object_id=job.storage_object_id,
            storage_object_name=job.storage_object_name,
            source_uri=job.source_uri,
            source_content_hash=job.source_content_hash,
            processing_fingerprint=job.processing_fingerprint,
            ingestion_session_id=(
                delivery.ingestion_session_id if delivery is not None else None
            ),
            logical_fragment_count=None,
            logical_block_count=block_count,
            vector_state=delivery.vector_state_raw if delivery is not None else None,
            searchable=(
                bool(delivery.searchable)
                if delivery is not None and delivery.searchable is not None
                else False
            ),
            ready_to_activate=(
                delivery.vector_state_raw == "READY_TO_ACTIVATE"
                if delivery is not None
                else False
            ),
            expected_bindings=delivery.expected_bindings if delivery is not None else None,
            synced_bindings=delivery.synced_bindings if delivery is not None else None,
            raw_ttl_state=None,
            effective_expires_at=None,
            ready_at=lifecycle.ready_at,
            cancel_requested_at=lifecycle.cancel_requested_at,
            activated_at=lifecycle.activated_at,
            superseded_at=lifecycle.superseded_at,
            cancelled_at=lifecycle.cancelled_at,
            delete_requested_at=lifecycle.delete_requested_at,
            deleted_at=lifecycle.deleted_at,
            failed_at=lifecycle.failed_at,
            last_verified_at=(
                delivery.last_reconciled_at if delivery is not None else None
            ),
            created_at=lifecycle.created_at,
            updated_at=now,
        )
        self.upsert(session, projection)
        return projection

    def upsert(
        self,
        session: Session,
        projection: KnowledgeInventoryProjection,
    ) -> None:
        session.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.knowledge_inventory (
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
                    cancel_requested_at, activated_at, superseded_at, cancelled_at,
                    delete_requested_at, deleted_at, failed_at, last_verified_at,
                    created_at, updated_at
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
                    :raw_ttl_state, :effective_expires_at,
                    :cancel_requested_at, :activated_at, :superseded_at, :cancelled_at,
                    :delete_requested_at, :deleted_at, :failed_at, :last_verified_at,
                    :created_at, :updated_at
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
                    ttl_state = EXCLUDED.ttl_state,
                    effective_expires_at = EXCLUDED.effective_expires_at,
                    cancel_requested_at = EXCLUDED.cancel_requested_at,
                    activated_at = EXCLUDED.activated_at,
                    superseded_at = EXCLUDED.superseded_at,
                    cancelled_at = EXCLUDED.cancelled_at,
                    delete_requested_at = EXCLUDED.delete_requested_at,
                    deleted_at = EXCLUDED.deleted_at,
                    failed_at = EXCLUDED.failed_at,
                    last_verified_at = EXCLUDED.last_verified_at,
                    updated_at = EXCLUDED.updated_at
                """
            ),
            {
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
                "raw_ttl_state": projection.raw_ttl_state,
                "effective_expires_at": projection.effective_expires_at,
                "cancel_requested_at": projection.cancel_requested_at,
                "activated_at": projection.activated_at,
                "superseded_at": projection.superseded_at,
                "cancelled_at": projection.cancelled_at,
                "delete_requested_at": projection.delete_requested_at,
                "deleted_at": projection.deleted_at,
                "failed_at": projection.failed_at,
                "last_verified_at": projection.last_verified_at,
                "created_at": projection.created_at,
                "updated_at": projection.updated_at,
            },
        )
