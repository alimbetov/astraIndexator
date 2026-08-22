from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import IndexationJob


@dataclass(frozen=True, slots=True)
class NewIndexationJob:
    producer_request_id: UUID
    document_id: UUID
    document_version: int
    access_zone_code: str
    source_uri: str
    knowledge_type: str | None = None
    external_revision: str | None = None
    requested_ttl_days: int | None = None
    source_file_name: str | None = None
    source_content_hash: str | None = None
    source_size_bytes: int | None = None
    priority: int = 0
    max_attempts: int = 5


class IndexationJobRepository:
    """Persistence-only durable Inbox operations.

    Claim/lease/fencing behavior deliberately belongs to M2.
    """

    def create_or_get(self, session: Session, command: NewIndexationJob) -> IndexationJob:
        job_id = uuid4()
        stmt = (
            insert(IndexationJob)
            .values(
                id=job_id,
                producer_request_id=command.producer_request_id,
                document_id=command.document_id,
                document_version=command.document_version,
                external_revision=command.external_revision,
                knowledge_type=command.knowledge_type,
                access_zone_code=command.access_zone_code,
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
