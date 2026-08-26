from __future__ import annotations

from types import SimpleNamespace

import pytest

from astra_indexator.astravector.batching import (
    DeliveryBatchPlanningError,
    DeterministicBatchPlanner,
)
from astra_indexator.astravector.contracts import LogicalBlock


class _Mapper:
    def logical_block(self, block: LogicalBlock):
        return SimpleNamespace(
            block_id=block.block_id,
            parent_block_id=block.parent_block_id,
            block_type=4,
            text=block.text,
            order_index=block.order_index,
            metadata=block.metadata,
            source_links=(),
            HasField=lambda name: False,
        )


def _block(index: int, *, text: str = "x", metadata: dict[str, str] | None = None) -> LogicalBlock:
    return LogicalBlock(
        block_id=f"b-{index}",
        parent_block_id="root",
        block_type="PARAGRAPH",
        text=text,
        order_index=index,
        metadata=metadata or {},
    )


def _identity(plan):
    return [
        (
            batch.batch_index,
            tuple(block.block_id for block in batch.blocks),
            batch.is_last_batch,
            batch.batch_content_hash,
        )
        for batch in plan
    ]


def test_planning_is_deterministic_for_count_and_byte_boundaries() -> None:
    planner = DeterministicBatchPlanner(
        _Mapper(),  # type: ignore[arg-type]
        max_blocks_per_batch=3,
        max_batch_bytes=18,
    )
    blocks = [_block(0, text="aaaa"), _block(1, text="bbbb"), _block(2, text="cccc")]

    first = planner.plan([blocks[2], blocks[0], blocks[1]])
    second = planner.plan([blocks[1], blocks[2], blocks[0]])

    assert _identity(first) == _identity(second)
    assert [tuple(block.order_index for block in batch.blocks) for batch in first] == [
        (0,),
        (1,),
        (2,),
    ]
    assert [batch.batch_index for batch in first] == [0, 1, 2]
    assert [batch.is_last_batch for batch in first] == [False, False, True]


def test_count_limit_creates_contiguous_zero_based_sequence() -> None:
    planner = DeterministicBatchPlanner(_Mapper(), max_blocks_per_batch=2)  # type: ignore[arg-type]
    plan = planner.plan([_block(4), _block(0), _block(3), _block(1), _block(2)])

    assert [batch.batch_index for batch in plan] == [0, 1, 2]
    assert [tuple(block.order_index for block in batch.blocks) for batch in plan] == [
        (0, 1),
        (2, 3),
        (4,),
    ]
    assert [batch.is_last_batch for batch in plan] == [False, False, True]


def test_byte_limit_uses_pinned_astravector_utf8_accounting() -> None:
    # AstraVector counts UTF-8 bytes of block_id + parent_block_id + text + metadata key/value.
    block = _block(0, text="Қазақ", metadata={"тіл": "қазақ"})
    expected = sum(
        len(value.encode("utf-8"))
        for value in (block.block_id, block.parent_block_id, block.text, "тіл", "қазақ")
    )
    planner = DeterministicBatchPlanner(
        _Mapper(),  # type: ignore[arg-type]
        max_blocks_per_batch=10,
        max_batch_bytes=expected,
    )

    plan = planner.plan([block])

    assert len(plan) == 1


def test_single_block_larger_than_byte_limit_fails_before_transport() -> None:
    planner = DeterministicBatchPlanner(
        _Mapper(),  # type: ignore[arg-type]
        max_blocks_per_batch=10,
        max_batch_bytes=8,
    )

    with pytest.raises(DeliveryBatchPlanningError, match="exceeds max_batch_bytes"):
        planner.plan([_block(0, text="payload")])


def test_duplicate_identity_and_order_remain_rejected() -> None:
    planner = DeterministicBatchPlanner(_Mapper(), max_blocks_per_batch=2)  # type: ignore[arg-type]
    duplicate_id = _block(0)
    duplicate_id_other_order = LogicalBlock(
        block_id=duplicate_id.block_id,
        parent_block_id="root",
        block_type="PARAGRAPH",
        text="other",
        order_index=1,
    )
    with pytest.raises(DeliveryBatchPlanningError, match="block_id values must be unique"):
        planner.plan([duplicate_id, duplicate_id_other_order])

    same_order = LogicalBlock(
        block_id="other",
        parent_block_id="root",
        block_type="PARAGRAPH",
        text="other",
        order_index=0,
    )
    with pytest.raises(DeliveryBatchPlanningError, match="order_index values must be unique"):
        planner.plan([duplicate_id, same_order])


def test_invalid_planner_limits_are_rejected() -> None:
    with pytest.raises(ValueError, match="max_blocks_per_batch must be positive"):
        DeterministicBatchPlanner(_Mapper(), max_blocks_per_batch=0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="max_batch_bytes must be positive"):
        DeterministicBatchPlanner(  # type: ignore[arg-type]
            _Mapper(), max_blocks_per_batch=1, max_batch_bytes=0
        )
