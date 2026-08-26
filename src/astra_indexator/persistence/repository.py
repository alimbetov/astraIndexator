from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from astra_indexator.domain.contracts import AccessZoneCode

from .models import IndexationJob


@dataclass(frozen=True, slots=True)
class NewIndexationJob:
    producer_request_id: UUID
    document_id: UUID
    document_version: int
    source_uri: str
    access_zone_code: str | None = None
    access_zone_id: UUID | None = None
    requested_access_zone_code: str | None = None
    requested_access_zone_id: UUID | None = None
    knowledge_type: str | None = None
    external_revision: str | None = None
    requested_ttl_days: int | None = None
    source_file_name: str | None = None
    source_content_hash: str | None = None
    source_size_bytes: int | None = None
    priority: int = 0
    max_attempts: int = 5

    def __post_init__(self) -> None:
        self._validate_code(self.access_zone_code, field="access_zone_code")
        self._validate_code(
            self.requested_access_zone_code,
            field="requested_access_zone_code",
        )
        if (
            self.access_zone_code is not None
            and self.requested_access_zone_code is not None
            and self.access_zone_code != self.requested_access_zone_code
        ):
            raise ValueError(
                "access_zone_code and requested_access_zone_code must be identical when both are set"
            )
        if (
            self.access_zone_id is not None
            and self.requested_access_zone_id is not None
            and self.access_zone_id != self.requested_access_zone_id
        ):
            raise ValueError(
                "access_zone_id and requested_access_zone_id must be identical when both are set"
            )

        effective_id = self.requested_access_zone_id or self.access_zone_id
        effective_code = self.requested_access_zone_code or self.access_zone_code
        if effective_id is None and effective_code is None:
            raise ValueError("AccessZone producer intent is required")
        if self.requested_ttl_days is not None and self.requested_ttl_days < 0:
            raise ValueError("requested_ttl_days must be non-negative")

    @staticmethod
    def _validate_code(value: str | None, *, field: str) -> None:
        if value is None:
            return
        try:
            AccessZoneCode(value)
        except ValueError as exc:
            raise ValueError(f"{field} must match ^[0-9]{{4}}$") from exc


class IndexationJobRepository:
    """Persistence-only durable Inbox operations.

    Claim/lease/fencing behavior deliberately belongs to M2. M8 requested_*
    columns are the authoritative producer delivery intent; legacy access_zone_*
    columns are written only for backward compatibility. AccessZoneCode is always
    preserved byte-for-byte as a four-character string; it is never converted to
    an integer and never replaced by an AstraVector-resolved UUID.
    """

    def create_or_get(self, session: Session, command: NewIndexationJob) -> IndexationJob:
        job_id = uuid4()
        requested_zone_id = command.requested_access_zone_id or command.access_zone_id
        requested_zone_code = command.requested_access_zone_code or command.access_zone_code
        stmt = (
            insert(IndexationJob)
            .values(
                id=job_id,
                producer_request_id=command.producer_request_id,
                document_id=command.document_id,
                document_version=command.document_version,
                external_revision=command.external_revision,
                knowledge_type=command.knowledge_type,
                access_zone_code=command.access_zone_code or requested_zone_code,
                access_zone_id=command.access_zone_id or requested_zone_id,
                requested_access_zone_code=requested_zone_code,
                requested_access_zone_id=requested_zone_id,
                requested_ttl_days=command.requested_ttl_days,
                source_uri=command.source_uri,
                source_file_name=command.source_file_name,
                source_content_hash=command.source_content_hash,
                source_size_bytes=command.source_size_bytes,
                status="PENDING",
                priority=command.priority,
                max_attempts=command.max_attempts,
            )
            .on_conflict_do_nothing(index_elements=[IndexationJob.producer_request_id])
            .returning(IndexationJob.id)
        )
        inserted_id = session.execute(stmt).scalar_one_or_none()

        if inserted_id is not None:
            return session.execute(
                select(IndexationJob).where(IndexationJob.id == inserted_id)
            ).scalar_one()

        return session.execute(
            select(IndexationJob).where(
                IndexationJob.producer_request_id == command.producer_request_id
            )
        ).scalar_one()

    def get(self, session: Session, job_id: UUID) -> IndexationJob | None:
        return session.get(IndexationJob, job_id)
