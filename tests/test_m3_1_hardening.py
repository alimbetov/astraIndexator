from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from astra_indexator.acquisition.metrics import NoopAcquisitionMetrics
from astra_indexator.acquisition.service import (
    AcquisitionError,
    AcquisitionPolicy,
    SafeAcquisitionService,
)
from astra_indexator.acquisition.workspace import (
    WorkspaceCapacityError,
    WorkspaceManager,
    WorkspacePolicy,
)
from astra_indexator.config import AcquisitionSettings
from astra_indexator.storage.object_storage import ObjectHead, StorageRef


class MemoryStorage:
    def __init__(self, payload: bytes, *, delay: float = 0.0):
        self.payload = payload
        self.delay = delay

    def head(self, ref: StorageRef) -> ObjectHead:
        return ObjectHead(exists=True, size_bytes=len(self.payload))

    def iter_bytes(self, ref: StorageRef, *, chunk_size: int = 1024 * 1024):
        if self.delay:
            time.sleep(self.delay)
        yield self.payload


def _docx(*, embedded: bool = False) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
        if embedded:
            archive.writestr("word/embeddings/nested.docx", b"PK\x03\x04nested")
    return buffer.getvalue()


def test_typed_settings_reject_invalid_limits(monkeypatch) -> None:
    monkeypatch.setenv("ASTRA_ACQUISITION_MAX_SOURCE_BYTES", "0")
    with pytest.raises(ValidationError):
        AcquisitionSettings()


def test_workspace_preflight_rejects_attempt_above_policy(tmp_path: Path) -> None:
    manager = WorkspaceManager(
        WorkspacePolicy(
            tmp_path,
            min_free_bytes=1,
            reserve_bytes=0,
            max_attempt_bytes=10,
            orphan_grace_seconds=1,
        )
    )
    with pytest.raises(WorkspaceCapacityError):
        manager.preflight(expected_bytes=11)


def test_nested_container_rejected_by_default(tmp_path: Path) -> None:
    payload = _docx(embedded=True)
    service = SafeAcquisitionService(
        MemoryStorage(payload), tmp_path, AcquisitionPolicy(max_nested_container_depth=0)
    )
    with pytest.raises(AcquisitionError) as exc:
        service.acquire(
            source_uri="seaweed://documents/source.docx",
            job_id=uuid4(),
            attempt_id=uuid4(),
            original_file_name="source.docx",
        )
    assert exc.value.code == "CONTAINER_NESTING_LIMIT"


def test_total_acquisition_deadline_is_enforced(tmp_path: Path) -> None:
    service = SafeAcquisitionService(
        MemoryStorage(b"hello", delay=0.02),
        tmp_path,
        AcquisitionPolicy(total_deadline_seconds=0.001),
        metrics=NoopAcquisitionMetrics(),
    )
    with pytest.raises(AcquisitionError) as exc:
        service.acquire(
            source_uri="seaweed://documents/source.txt",
            job_id=uuid4(),
            attempt_id=uuid4(),
            original_file_name="source.txt",
        )
    assert exc.value.code == "ACQUISITION_DEADLINE_EXCEEDED"
