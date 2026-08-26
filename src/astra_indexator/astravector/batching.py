from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .canonical_hash import (
    HashLogicalBlock,
    HashSourceLink,
    HashSourceLocation,
    canonical_batch_bytes,
    compute_batch_content_hash,
    compute_final_content_hash,
)
from .contracts import AppendBlocksCommand, LogicalBlock
from .proto_mapper import AstraVectorProtoMapper


class DeliveryBatchPlanningError(ValueError):
    """Logical blocks cannot be partitioned into a stable delivery sequence."""


@dataclass(frozen=True, slots=True)
class PlannedDeliveryBatch:
    batch_index: int
    blocks: tuple[LogicalBlock, ...]
    is_last_batch: bool
    batch_content_hash: str
    serialized_bytes: int

    def command(self, *, ingestion_session_id: Any) -> AppendBlocksCommand:
        return AppendBlocksCommand(
            ingestion_session_id=ingestion_session_id,
            blocks=self.blocks,
            batch_index=self.batch_index,
            is_last_batch=self.is_last_batch,
            batch_content_hash=self.batch_content_hash,
        )


class DeterministicBatchPlanner:
    """Stable batching plus canonical AstraVector final-content hashing.

    Input order is not trusted. Blocks are canonicalized by ``(order_index, block_id)`` so a
    restarted worker reconstructs identical batch boundaries and final hash from the same logical
    document. Duplicate block IDs or order indices are rejected because replay ordering would be
    ambiguous.
    """

    def __init__(self, mapper: AstraVectorProtoMapper, *, max_blocks_per_batch: int = 128) -> None:
        if max_blocks_per_batch <= 0:
            raise ValueError("max_blocks_per_batch must be positive")
        self._mapper = mapper
        self._max_blocks = max_blocks_per_batch

    def plan(self, blocks: Sequence[LogicalBlock]) -> tuple[PlannedDeliveryBatch, ...]:
        ordered = self._ordered(blocks)
        chunks = [
            ordered[offset : offset + self._max_blocks]
            for offset in range(0, len(ordered), self._max_blocks)
        ]
        planned: list[PlannedDeliveryBatch] = []
        for batch_index, chunk in enumerate(chunks):
            hash_blocks = tuple(self._hash_block(block) for block in chunk)
            canonical = canonical_batch_bytes(hash_blocks)
            planned.append(
                PlannedDeliveryBatch(
                    batch_index=batch_index,
                    blocks=tuple(chunk),
                    is_last_batch=batch_index == len(chunks) - 1,
                    batch_content_hash=compute_batch_content_hash(hash_blocks),
                    serialized_bytes=len(canonical),
                )
            )
        return tuple(planned)

    def final_content_hash(self, blocks: Sequence[LogicalBlock]) -> str:
        ordered = self._ordered(blocks)
        return compute_final_content_hash(tuple(self._hash_block(block) for block in ordered))

    @staticmethod
    def _ordered(blocks: Sequence[LogicalBlock]) -> tuple[LogicalBlock, ...]:
        if not blocks:
            raise DeliveryBatchPlanningError("logical document must contain at least one block")
        ordered = tuple(sorted(blocks, key=lambda block: (block.order_index, block.block_id)))
        block_ids = [block.block_id for block in ordered]
        if len(block_ids) != len(set(block_ids)):
            raise DeliveryBatchPlanningError("logical block_id values must be unique")
        order_indices = [block.order_index for block in ordered]
        if len(order_indices) != len(set(order_indices)):
            raise DeliveryBatchPlanningError("logical block order_index values must be unique")
        return ordered

    def _hash_block(self, block: LogicalBlock) -> HashLogicalBlock:
        wire = self._mapper.logical_block(block)
        location = None
        try:
            has_location = wire.HasField("source_location")
        except (AttributeError, ValueError):
            has_location = block.source_location is not None
        if has_location:
            loc = wire.source_location
            location = HashSourceLocation(
                page_start=int(loc.page_start),
                page_end=int(loc.page_end),
                char_start=int(loc.char_start),
                char_end=int(loc.char_end),
                section_path=str(loc.section_path),
                heading=str(loc.heading),
                table_id=str(loc.table_id),
                row_index=int(loc.row_index),
                column_index=int(loc.column_index),
            )

        links = tuple(
            HashSourceLink(
                type=int(link.type),
                url=str(link.url),
                label=str(link.label),
                mime_type=str(link.mime_type),
                requires_auth=bool(link.requires_auth),
                expires_at=str(link.expires_at),
                attributes=dict(link.attributes),
            )
            for link in wire.source_links
        )
        return HashLogicalBlock(
            block_id=str(wire.block_id),
            parent_block_id=str(wire.parent_block_id),
            block_type=int(wire.block_type),
            text=str(wire.text),
            order_index=int(wire.order_index),
            metadata=dict(wire.metadata),
            source_location=location,
            source_links=links,
        )
