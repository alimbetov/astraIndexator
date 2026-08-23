from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from astra_indexator.persistence.models import IndexationJob, ProcessingAttempt

from .metrics import AcquisitionMetrics, NoopAcquisitionMetrics


class WorkspaceCapacityError(RuntimeError):
    code = "WORKSPACE_CAPACITY_EXCEEDED"


@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    root: Path
    min_free_bytes: int
    reserve_bytes: int
    max_attempt_bytes: int
    orphan_grace_seconds: int


class WorkspaceManager:
    def __init__(self, policy: WorkspacePolicy, metrics: AcquisitionMetrics | None = None):
        self.policy = policy
        self.metrics = metrics or NoopAcquisitionMetrics()

    def preflight(self, *, expected_bytes: int | None = None) -> None:
        self.policy.root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.policy.root).free
        self.metrics.workspace_free_bytes(free)
        required = self.policy.min_free_bytes + self.policy.reserve_bytes
        if expected_bytes is not None:
            if expected_bytes > self.policy.max_attempt_bytes:
                raise WorkspaceCapacityError("expected attempt bytes exceed max_attempt_bytes")
            required = max(required, expected_bytes + self.policy.reserve_bytes)
        if free < required:
            raise WorkspaceCapacityError("workspace free capacity below configured threshold")

    def attempt_root(self, job_id: UUID, attempt_id: UUID) -> Path:
        return self.policy.root / str(job_id) / str(attempt_id)

    def enforce_attempt_usage(self, job_id: UUID, attempt_id: UUID) -> None:
        root = self.attempt_root(job_id, attempt_id)
        total = sum(path.stat().st_size for path in root.rglob("*") if path.is_file()) if root.exists() else 0
        if total > self.policy.max_attempt_bytes:
            raise WorkspaceCapacityError("attempt workspace exceeds max_attempt_bytes")

    def cleanup_attempt(self, job_id: UUID, attempt_id: UUID) -> None:
        shutil.rmtree(self.attempt_root(job_id, attempt_id), ignore_errors=True)
        self.metrics.workspace_cleanup(result="deleted")

    def scavenge(self, session: Session, *, now_epoch: float | None = None) -> list[Path]:
        now_epoch = now_epoch or time.time()
        deleted: list[Path] = []
        if not self.policy.root.exists():
            return deleted
        for job_dir in self.policy.root.iterdir():
            if not job_dir.is_dir():
                continue
            try:
                job_id = UUID(job_dir.name)
            except ValueError:
                continue
            for attempt_dir in job_dir.iterdir():
                if not attempt_dir.is_dir():
                    continue
                try:
                    attempt_id = UUID(attempt_dir.name)
                except ValueError:
                    continue
                if now_epoch - attempt_dir.stat().st_mtime < self.policy.orphan_grace_seconds:
                    continue
                live = session.execute(
                    select(ProcessingAttempt.id)
                    .join(IndexationJob, IndexationJob.id == ProcessingAttempt.job_id)
                    .where(
                        ProcessingAttempt.id == attempt_id,
                        ProcessingAttempt.job_id == job_id,
                        ProcessingAttempt.finished_at.is_(None),
                        IndexationJob.status == "PROCESSING",
                        IndexationJob.lease_until.is_not(None),
                        IndexationJob.lease_until >= func.now(),
                        IndexationJob.worker_id == ProcessingAttempt.worker_id,
                        IndexationJob.lease_generation == ProcessingAttempt.lease_generation,
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if live is not None:
                    self.metrics.workspace_cleanup(result="kept_live")
                    continue
                shutil.rmtree(attempt_dir, ignore_errors=True)
                deleted.append(attempt_dir)
                self.metrics.workspace_cleanup(result="deleted_orphan")
            try:
                job_dir.rmdir()
            except OSError:
                pass
        return deleted
