from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Protocol, Sequence
from uuid import UUID


class IngestionSessionState(str, Enum):
    ACTIVE = "ACTIVE"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"
    EXPIRED = "EXPIRED"
    UNKNOWN = "UNKNOWN"


class DeliveryCompletionLevel(str, Enum):
    SESSION_ACCEPTED = "SESSION_ACCEPTED"
    BLOCKS_STAGED = "BLOCKS_STAGED"
    FINALIZED = "FINALIZED"
    VECTOR_READY = "VECTOR_READY"
    SEARCHABLE = "SEARCHABLE"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    page_start: int = 0
    page_end: int = 0
    char_start: int = 0
    char_end: int = 0
    section_path: str = ""
    heading: str = ""
    table_id: str = ""
    row_index: int = 0
    column_index: int = 0


@dataclass(frozen=True, slots=True)
class SourceLink:
    type: str
    url: str
    label: str = ""
    mime_type: str = ""
    requires_auth: bool = False
    expires_at: str = ""
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LogicalBlock:
    block_id: str
    parent_block_id: str
    block_type: str
    text: str
    order_index: int
    source_location: SourceLocation | None = None
    source_links: Sequence[SourceLink] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StartIngestionCommand:
    access_zone_id: UUID | None
    access_zone_code: str | None
    document_id: UUID
    document_version: int
    source_uri: str
    file_name: str
    content_hash: str
    idempotency_key: str
    total_bytes_estimate: int
    total_blocks_estimate: int
    total_pages_estimate: int
    metadata: Mapping[str, str]
    ttl_days: int


@dataclass(frozen=True, slots=True)
class StartIngestionResult:
    ingestion_session_id: UUID
    raw_status: str
    state: IngestionSessionState
    expires_at: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AppendBlocksCommand:
    ingestion_session_id: UUID
    blocks: Sequence[LogicalBlock]
    batch_index: int
    is_last_batch: bool
    batch_content_hash: str


@dataclass(frozen=True, slots=True)
class AppendBlocksResult:
    ingestion_session_id: UUID
    raw_status: str
    state: IngestionSessionState
    accepted_blocks: int
    accepted_batch_index: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FinalizeIngestionCommand:
    ingestion_session_id: UUID
    final_content_hash: str


@dataclass(frozen=True, slots=True)
class AbortIngestionCommand:
    ingestion_session_id: UUID
    reason: str


@dataclass(frozen=True, slots=True)
class IngestionStatus:
    ingestion_session_id: UUID
    raw_status: str
    state: IngestionSessionState
    received_batches: int
    received_blocks: int
    received_bytes: int
    expires_at: str
    error_code: str = ""
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class DocumentVectorStatus:
    raw_state: str
    progress_percent: float
    searchable: bool
    ready_to_activate: bool
    message: str = ""


def map_session_state(raw_status: str) -> IngestionSessionState:
    normalized = raw_status.strip().upper()
    try:
        return IngestionSessionState(normalized)
    except ValueError:
        return IngestionSessionState.UNKNOWN


class AstraVectorIngestionPort(Protocol):
    def start(self, command: StartIngestionCommand) -> StartIngestionResult: ...

    def append(self, command: AppendBlocksCommand) -> AppendBlocksResult: ...

    def finalize(self, command: FinalizeIngestionCommand) -> DocumentVectorStatus: ...

    def abort(self, command: AbortIngestionCommand) -> IngestionStatus: ...

    def get_ingestion_status(self, ingestion_session_id: UUID) -> IngestionStatus: ...

    def get_document_vector_status(
        self,
        *,
        access_zone_id: UUID,
        document_id: UUID,
        document_version: int,
    ) -> DocumentVectorStatus: ...
