from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .models import SCHEMA


class PreparedArtifactCheckpoint(Base):
    __tablename__ = "prepared_artifact_checkpoint"
    __table_args__ = (
        CheckConstraint("lease_generation > 0", name="prepared_artifact_lease_generation_positive"),
        CheckConstraint("element_count >= 0", name="prepared_artifact_element_count_non_negative"),
        CheckConstraint(
            "fragment_count >= 0", name="prepared_artifact_fragment_count_non_negative"
        ),
        {"schema": SCHEMA},
    )

    job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.indexation_job.id", ondelete="CASCADE"),
        primary_key=True,
    )
    artifact_id: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_uri: Mapped[str] = mapped_column(Text, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    compatibility_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    element_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fragment_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
