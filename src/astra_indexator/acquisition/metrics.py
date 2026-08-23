from __future__ import annotations

from typing import Protocol


class AcquisitionMetrics(Protocol):
    def acquisition_completed(self, *, detected_format: str, size_bytes: int, duration_seconds: float) -> None: ...
    def acquisition_failed(self, *, error_code: str, duration_seconds: float) -> None: ...
    def storage_request(self, *, operation: str, result: str, duration_seconds: float) -> None: ...
    def workspace_free_bytes(self, value: int) -> None: ...
    def workspace_cleanup(self, *, result: str) -> None: ...


class NoopAcquisitionMetrics:
    def acquisition_completed(self, *, detected_format: str, size_bytes: int, duration_seconds: float) -> None:
        pass

    def acquisition_failed(self, *, error_code: str, duration_seconds: float) -> None:
        pass

    def storage_request(self, *, operation: str, result: str, duration_seconds: float) -> None:
        pass

    def workspace_free_bytes(self, value: int) -> None:
        pass

    def workspace_cleanup(self, *, result: str) -> None:
        pass
