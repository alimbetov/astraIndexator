from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
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


class OcrSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASTRA_OCR_", extra="ignore")

    enabled: bool = True
    profile_id: str = "ocr_cpu_ru_kk_en_v1"
    device: str = "cpu"
    model_bundle_root: Path = Path("/opt/astra/models/ocr/active")
    workspace_root: Path = Path("/work/astra-indexator")
    languages: str = "ru,kk,en"
    min_confidence: float = 0.35
    hard_confidence_floor: float = 0.10
    min_image_width: int = 24
    min_image_height: int = 24
    min_image_pixels: int = 1_024
    max_pages_per_job: int = 500
    max_pixels_per_page: int = 24_000_000
    max_total_pixels_per_job: int = 250_000_000
    max_derived_bytes: int = 512 * 1024 * 1024
    memory_soft_limit_bytes: int = 1024 * 1024 * 1024
    memory_hard_limit_bytes: int = 1536 * 1024 * 1024
    max_concurrent_pages_per_worker: int = 1
    timeout_per_candidate_seconds: float = 60.0
    timeout_per_job_seconds: float = 900.0
    render_dpi: int = 200
    decision_policy_version: str = "ocr-decision-v1"
    preprocessing_version: str = "ocr-preprocess-v1"
    reconciliation_version: str = "ocr-reconcile-v1"

    @property
    def language_tuple(self) -> tuple[str, ...]:
        return tuple(value.strip() for value in self.languages.split(",") if value.strip())

    def to_profile(self):
        from astra_indexator.ocr.model import OcrProfile
        return OcrProfile(
            profile_id=self.profile_id,
            languages=self.language_tuple,
            device=self.device,
            min_confidence=self.min_confidence,
            hard_confidence_floor=self.hard_confidence_floor,
            min_image_width=self.min_image_width,
            min_image_height=self.min_image_height,
            min_image_pixels=self.min_image_pixels,
            max_pages_per_job=self.max_pages_per_job,
            max_pixels_per_page=self.max_pixels_per_page,
            max_total_pixels_per_job=self.max_total_pixels_per_job,
            max_derived_bytes=self.max_derived_bytes,
            memory_soft_limit_bytes=self.memory_soft_limit_bytes,
            memory_hard_limit_bytes=self.memory_hard_limit_bytes,
            max_concurrent_pages_per_worker=self.max_concurrent_pages_per_worker,
            timeout_per_candidate_seconds=self.timeout_per_candidate_seconds,
            timeout_per_job_seconds=self.timeout_per_job_seconds,
            render_dpi=self.render_dpi,
            decision_policy_version=self.decision_policy_version,
            preprocessing_version=self.preprocessing_version,
            reconciliation_version=self.reconciliation_version,
        )

    @model_validator(mode="after")
    def validate_ocr(self) -> "OcrSettings":
        if not (0 <= self.hard_confidence_floor <= self.min_confidence <= 1):
            raise ValueError("OCR confidence thresholds must satisfy 0 <= hard floor <= min <= 1")
        positive = (
            self.min_image_width, self.min_image_height, self.min_image_pixels, self.max_pages_per_job,
            self.max_pixels_per_page, self.max_total_pixels_per_job, self.max_derived_bytes,
            self.memory_soft_limit_bytes, self.memory_hard_limit_bytes, self.max_concurrent_pages_per_worker,
            self.render_dpi,
        )
        if min(positive) <= 0:
            raise ValueError("OCR resource limits must be positive")
        if self.memory_hard_limit_bytes < self.memory_soft_limit_bytes:
            raise ValueError("OCR hard memory limit must be >= soft limit")
        if min(self.timeout_per_candidate_seconds, self.timeout_per_job_seconds) <= 0:
            raise ValueError("OCR timeouts must be positive")
        if self.timeout_per_job_seconds < self.timeout_per_candidate_seconds:
            raise ValueError("OCR job timeout must be >= candidate timeout")
        if not self.language_tuple:
            raise ValueError("at least one OCR language must be configured")
        return self
