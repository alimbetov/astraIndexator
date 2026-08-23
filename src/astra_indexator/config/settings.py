from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AcquisitionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASTRA_ACQUISITION_", extra="ignore")

    workspace_root: Path = Path("/work/astra-indexator")
    max_source_bytes: int = 100 * 1024 * 1024
    max_container_entries: int = 10_000
    max_total_uncompressed_bytes: int = 500 * 1024 * 1024
    max_single_entry_uncompressed_bytes: int = 100 * 1024 * 1024
    max_compression_ratio: float = 100.0
    max_nested_container_depth: int = 0
    max_image_width: int = 30_000
    max_image_height: int = 30_000
    max_image_pixels: int = 150_000_000
    max_tiff_pages: int = 1_000

    min_free_bytes: int = 2 * 1024 * 1024 * 1024
    reserve_bytes: int = 512 * 1024 * 1024
    max_attempt_bytes: int = 2 * 1024 * 1024 * 1024
    orphan_grace_seconds: int = 3600

    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 30.0
    total_deadline_seconds: float = 300.0
    validation_profile: str = "default-v1"

    @model_validator(mode="after")
    def validate_limits(self) -> "AcquisitionSettings":
        positive_ints = {
            "max_source_bytes": self.max_source_bytes,
            "max_container_entries": self.max_container_entries,
            "max_total_uncompressed_bytes": self.max_total_uncompressed_bytes,
            "max_single_entry_uncompressed_bytes": self.max_single_entry_uncompressed_bytes,
            "max_image_width": self.max_image_width,
            "max_image_height": self.max_image_height,
            "max_image_pixels": self.max_image_pixels,
            "max_tiff_pages": self.max_tiff_pages,
            "min_free_bytes": self.min_free_bytes,
            "max_attempt_bytes": self.max_attempt_bytes,
            "orphan_grace_seconds": self.orphan_grace_seconds,
        }
        if any(value <= 0 for value in positive_ints.values()):
            raise ValueError("acquisition integer limits must be positive")
        if self.reserve_bytes < 0:
            raise ValueError("reserve_bytes must be non-negative")
        if self.max_compression_ratio <= 1:
            raise ValueError("max_compression_ratio must be > 1")
        if self.max_nested_container_depth < 0:
            raise ValueError("max_nested_container_depth must be >= 0")
        if min(self.connect_timeout_seconds, self.read_timeout_seconds, self.total_deadline_seconds) <= 0:
            raise ValueError("acquisition timeouts must be positive")
        if self.total_deadline_seconds < self.connect_timeout_seconds:
            raise ValueError("total_deadline_seconds must be >= connect_timeout_seconds")
        return self
