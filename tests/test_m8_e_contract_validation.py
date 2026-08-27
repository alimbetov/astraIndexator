from __future__ import annotations

import pytest

from astra_indexator.astravector.contracts import LogicalBlock, SourceLink
from astra_indexator.astravector.validation import (
    LogicalBlockValidationError,
    SourceLinkSecurityError,
    validate_logical_blocks,
    validate_source_link,
)


def _root(*, order_index: int = 0) -> LogicalBlock:
    return LogicalBlock(
        block_id="root",
        parent_block_id="",
        block_type="DOCUMENT",
        text="Document",
        order_index=order_index,
    )


def _paragraph(
    block_id: str,
    *,
    parent: str = "root",
    order_index: int = 1,
) -> LogicalBlock:
    return LogicalBlock(
        block_id=block_id,
        parent_block_id=parent,
        block_type="PARAGRAPH",
        text=f"Text {block_id}",
        order_index=order_index,
    )


def test_valid_connected_tree_is_accepted() -> None:
    blocks = (
        _root(),
        _paragraph("section", order_index=1),
        _paragraph("child", parent="section", order_index=2),
    )

    assert validate_logical_blocks(blocks) == blocks


def test_exactly_one_document_root_is_required() -> None:
    with pytest.raises(LogicalBlockValidationError, match="exactly one DOCUMENT"):
        validate_logical_blocks((_paragraph("orphan"),))

    second_root = LogicalBlock(
        block_id="root-2",
        parent_block_id="",
        block_type="DOCUMENT",
        text="Other document",
        order_index=1,
    )
    with pytest.raises(LogicalBlockValidationError, match="exactly one DOCUMENT"):
        validate_logical_blocks((_root(), second_root))


def test_duplicate_id_and_order_index_are_rejected() -> None:
    duplicate_id = _paragraph("root", parent="root", order_index=1)
    with pytest.raises(LogicalBlockValidationError, match="duplicate logical block_id"):
        validate_logical_blocks((_root(), duplicate_id))

    with pytest.raises(LogicalBlockValidationError, match="duplicate logical block order_index"):
        validate_logical_blocks((_root(), _paragraph("p", order_index=0)))


def test_missing_parent_and_self_parent_are_rejected() -> None:
    with pytest.raises(LogicalBlockValidationError, match="references missing parent"):
        validate_logical_blocks((_root(), _paragraph("p", parent="missing")))

    with pytest.raises(LogicalBlockValidationError, match="cannot parent itself"):
        validate_logical_blocks((_root(), _paragraph("p", parent="p")))


def test_multi_node_cycle_is_rejected() -> None:
    blocks = (
        _root(),
        _paragraph("a", parent="b", order_index=1),
        _paragraph("b", parent="a", order_index=2),
    )

    with pytest.raises(LogicalBlockValidationError, match="contains a cycle"):
        validate_logical_blocks(blocks)


def test_non_root_without_parent_is_rejected() -> None:
    with pytest.raises(LogicalBlockValidationError, match="must reference a parent"):
        validate_logical_blocks((_root(), _paragraph("p", parent="")))


def test_safe_source_link_is_accepted() -> None:
    validate_source_link(
        SourceLink(
            type="PAGE",
            url="https://docs.example.test/manual?page=7&lang=kk",
            attributes={"section": "installation", "source_id": "manual-1"},
        )
    )


@pytest.mark.parametrize(
    "link",
    [
        SourceLink(type="DOWNLOAD", url="https://user:password@example.test/file.pdf"),
        SourceLink(type="DOWNLOAD", url="https://example.test/file.pdf?access_token=secret-123"),
        SourceLink(
            type="EXTERNAL_SYSTEM",
            url="https://example.test",
            attributes={"api-key": "secret-123"},
        ),
        SourceLink(
            type="EXTERNAL_SYSTEM",
            url="https://example.test",
            attributes={"Authorization": "Bearer secret-123"},
        ),
    ],
)
def test_source_link_credentials_are_rejected_without_secret_echo(link: SourceLink) -> None:
    with pytest.raises(SourceLinkSecurityError) as raised:
        validate_source_link(link)

    assert "secret-123" not in str(raised.value)
    assert "password@example" not in str(raised.value)
