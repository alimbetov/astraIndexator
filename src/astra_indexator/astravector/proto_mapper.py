from __future__ import annotations

import re
from types import ModuleType
from typing import Any

from .canonical_hash import normalize_sha256_hex
from .contracts import (
    AbortIngestionCommand,
    AppendBlocksCommand,
    FinalizeIngestionCommand,
    LogicalBlock,
    SourceLink,
    SourceLocation,
    StartIngestionCommand,
)
from .wire_contract import require_positive_uint32, require_uint32, require_uint64

_ACCESS_ZONE_CODE = re.compile(r"^[0-9]{4}$")

_BLOCK_TYPE_NAMES = {
    "DOCUMENT": "BLOCK_TYPE_DOCUMENT",
    "SECTION": "BLOCK_TYPE_SECTION",
    "SUBSECTION": "BLOCK_TYPE_SUBSECTION",
    "PARAGRAPH": "BLOCK_TYPE_PARAGRAPH",
    "TABLE": "BLOCK_TYPE_TABLE",
    "TABLE_ROW": "BLOCK_TYPE_TABLE_ROW",
    "LIST": "BLOCK_TYPE_LIST",
    "LIST_ITEM": "BLOCK_TYPE_LIST_ITEM",
    "FAQ_ITEM": "BLOCK_TYPE_FAQ_ITEM",
    "CODE_BLOCK": "BLOCK_TYPE_CODE_BLOCK",
    "CAPTION": "BLOCK_TYPE_CAPTION",
}

_SOURCE_LINK_TYPE_NAMES = {
    "ORIGINAL_DOCUMENT": "SOURCE_LINK_TYPE_ORIGINAL_DOCUMENT",
    "PREVIEW": "SOURCE_LINK_TYPE_PREVIEW",
    "DOWNLOAD": "SOURCE_LINK_TYPE_DOWNLOAD",
    "PAGE": "SOURCE_LINK_TYPE_PAGE",
    "SECTION": "SOURCE_LINK_TYPE_SECTION",
    "CHUNK": "SOURCE_LINK_TYPE_CHUNK",
    "EXTERNAL_SYSTEM": "SOURCE_LINK_TYPE_EXTERNAL_SYSTEM",
}


class ProtoMappingError(ValueError):
    """Application DTO cannot be represented safely by the pinned protobuf contract."""


class AstraVectorProtoMapper:
    """Maps AstraIndexator application DTOs to generated AstraVector protobuf messages.

    ``pb`` must be the module generated from the pinned
    ``llm2/proto/astravector_embedding.proto`` revision. The mapper deliberately
    depends on generated message constructors instead of maintaining duplicate wire DTOs.
    """

    def __init__(self, pb: ModuleType | Any) -> None:
        self._pb = pb

    def start_request(self, command: StartIngestionCommand) -> Any:
        access_zone_id, access_zone_code = self._access_zone(
            command.access_zone_id, command.access_zone_code
        )
        document_version = require_positive_uint32(
            command.document_version, field="document_version"
        )
        total_bytes_estimate = require_uint64(
            command.total_bytes_estimate, field="total_bytes_estimate"
        )
        total_blocks_estimate = require_uint32(
            command.total_blocks_estimate, field="total_blocks_estimate"
        )
        total_pages_estimate = require_uint32(
            command.total_pages_estimate, field="total_pages_estimate"
        )
        ttl_days = require_uint32(command.ttl_days, field="ttl_days")
        idempotency_key = self._required_text(command.idempotency_key, "idempotency_key")

        content_hash = command.content_hash.strip()
        if content_hash:
            content_hash = normalize_sha256_hex(content_hash)

        return self._pb.StartLogicalDocumentIngestionRequest(
            access_zone_id=access_zone_id,
            access_zone_code=access_zone_code,
            document_id=str(command.document_id),
            document_version=document_version,
            source_uri=command.source_uri,
            file_name=command.file_name,
            content_hash=content_hash,
            idempotency_key=idempotency_key,
            total_bytes_estimate=total_bytes_estimate,
            total_blocks_estimate=total_blocks_estimate,
            total_pages_estimate=total_pages_estimate,
            metadata=dict(command.metadata),
            ttl_days=ttl_days,
        )

    def append_request(self, command: AppendBlocksCommand) -> Any:
        if not command.blocks:
            raise ProtoMappingError("blocks must not be empty")
        batch_index = require_uint32(command.batch_index, field="batch_index")
        batch_content_hash = normalize_sha256_hex(command.batch_content_hash)
        return self._pb.AppendLogicalDocumentBlocksRequest(
            ingestion_session_id=str(command.ingestion_session_id),
            blocks=[self.logical_block(block) for block in command.blocks],
            batch_index=batch_index,
            is_last_batch=command.is_last_batch,
            batch_content_hash=batch_content_hash,
        )

    def finalize_request(self, command: FinalizeIngestionCommand) -> Any:
        return self._pb.FinalizeLogicalDocumentIngestionRequest(
            ingestion_session_id=str(command.ingestion_session_id),
            final_content_hash=normalize_sha256_hex(command.final_content_hash),
        )

    def abort_request(self, command: AbortIngestionCommand) -> Any:
        return self._pb.AbortLogicalDocumentIngestionRequest(
            ingestion_session_id=str(command.ingestion_session_id),
            reason=command.reason.strip(),
        )

    def ingestion_status_request(self, ingestion_session_id: Any) -> Any:
        return self._pb.GetLogicalDocumentIngestionStatusRequest(
            ingestion_session_id=str(ingestion_session_id)
        )

    def document_vector_status_request(
        self,
        *,
        access_zone_id: Any,
        document_id: Any,
        document_version: int,
        correlation_id: str = "",
        caller_service: str = "astra-indexator",
        include_qdrant: bool = True,
    ) -> Any:
        version = require_uint64(document_version, field="document_version")
        context = self._pb.RequestContext(
            correlation_id=correlation_id,
            caller_service=caller_service,
        )
        document = self._pb.DocumentRef(
            access_zone_id=str(access_zone_id),
            document_id=str(document_id),
            document_version=version,
        )
        return self._pb.GetDocumentVectorStatusRequest(
            context=context,
            document=document,
            include_qdrant=include_qdrant,
        )

    def logical_block(self, block: LogicalBlock) -> Any:
        block_id = self._required_text(block.block_id, "block_id")
        text = self._required_text(block.text, f"block[{block_id}].text")
        block_type = self._enum_value(
            _BLOCK_TYPE_NAMES,
            block.block_type,
            field=f"block[{block_id}].block_type",
        )
        order_index = require_uint32(block.order_index, field=f"block[{block_id}].order_index")

        kwargs: dict[str, Any] = {
            "block_id": block_id,
            "parent_block_id": block.parent_block_id.strip(),
            "block_type": block_type,
            "text": text,
            "order_index": order_index,
            "source_links": [self.source_link(link) for link in block.source_links],
            "metadata": dict(block.metadata),
        }
        if block.source_location is not None:
            kwargs["source_location"] = self.source_location(block.source_location, owner=block_id)
        return self._pb.LogicalBlock(**kwargs)

    def source_location(self, location: SourceLocation, *, owner: str) -> Any:
        page_start = require_uint32(location.page_start, field=f"block[{owner}].page_start")
        page_end = require_uint32(location.page_end, field=f"block[{owner}].page_end")
        char_start = require_uint32(location.char_start, field=f"block[{owner}].char_start")
        char_end = require_uint32(location.char_end, field=f"block[{owner}].char_end")
        row_index = require_uint32(location.row_index, field=f"block[{owner}].row_index")
        column_index = require_uint32(location.column_index, field=f"block[{owner}].column_index")

        if page_start and page_end and page_end < page_start:
            raise ProtoMappingError(f"block[{owner}] page_end must be >= page_start")
        if char_start and char_end and char_end < char_start:
            raise ProtoMappingError(f"block[{owner}] char_end must be >= char_start")

        return self._pb.SourceLocation(
            page_start=page_start,
            page_end=page_end,
            char_start=char_start,
            char_end=char_end,
            section_path=location.section_path,
            heading=location.heading,
            table_id=location.table_id,
            row_index=row_index,
            column_index=column_index,
        )

    def source_link(self, link: SourceLink) -> Any:
        link_type = self._enum_value(_SOURCE_LINK_TYPE_NAMES, link.type, field="source_link.type")
        url = self._required_text(link.url, "source_link.url")
        return self._pb.SourceLink(
            type=link_type,
            url=url,
            label=link.label,
            mime_type=link.mime_type,
            requires_auth=link.requires_auth,
            expires_at=link.expires_at,
            attributes=dict(link.attributes),
        )

    def _access_zone(self, access_zone_id: Any, access_zone_code: str | None) -> tuple[str, str]:
        zone_id = str(access_zone_id) if access_zone_id is not None else ""
        zone_code = access_zone_code.strip() if access_zone_code is not None else ""
        if not zone_id and not zone_code:
            raise ProtoMappingError("one access-zone selector is required")
        if zone_code and _ACCESS_ZONE_CODE.fullmatch(zone_code) is None:
            raise ProtoMappingError("access_zone_code must be exactly four ASCII digits")
        return zone_id, zone_code

    def _enum_value(self, mapping: dict[str, str], value: str, *, field: str) -> int:
        normalized = value.strip().upper()
        constant_name = mapping.get(normalized)
        if constant_name is None:
            raise ProtoMappingError(f"unsupported {field}: {value!r}")
        try:
            return int(getattr(self._pb, constant_name))
        except AttributeError as exc:
            raise ProtoMappingError(
                f"generated protobuf module does not expose {constant_name}; contract revision mismatch"
            ) from exc

    @staticmethod
    def _required_text(value: str, field: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ProtoMappingError(f"{field} must not be blank")
        return normalized
