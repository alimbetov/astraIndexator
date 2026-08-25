from __future__ import annotations

import json
from pathlib import Path

import pytest

from astra_indexator.astravector import (
    CURRENT_WIRE_CONTRACT,
    HashLogicalBlock,
    HashSourceLink,
    HashSourceLocation,
    UINT32_MAX,
    WireRangeError,
    canonical_batch_bytes,
    compute_batch_content_hash,
    compute_final_content_hash,
    normalize_sha256_hex,
    render_final_content_text,
    require_positive_uint32,
    require_uint32,
)

FIXTURE = Path(__file__).parent / "fixtures" / "astravector" / "canonical-hash-v1.json"


def _location(raw: dict[str, object]) -> HashSourceLocation | None:
    if not raw:
        return None
    return HashSourceLocation(
        page_start=int(raw["page_start"]),
        page_end=int(raw["page_end"]),
        char_start=int(raw["char_start"]),
        char_end=int(raw["char_end"]),
        section_path=str(raw["section_path"]),
        heading=str(raw["heading"]),
        table_id=str(raw["table_id"]),
        row_index=int(raw["row_index"]),
        column_index=int(raw["column_index"]),
    )


def _block(raw: dict[str, object]) -> HashLogicalBlock:
    links = [
        HashSourceLink(
            type=int(link["type"]),
            url=str(link["url"]),
            label=str(link["label"]),
            mime_type=str(link["mime_type"]),
            requires_auth=bool(link["requires_auth"]),
            expires_at=str(link["expires_at"]),
            attributes={str(k): str(v) for k, v in dict(link["attributes"]).items()},
        )
        for link in list(raw["source_links"])
    ]
    return HashLogicalBlock(
        block_id=str(raw["block_id"]),
        parent_block_id=str(raw["parent_block_id"]),
        block_type=int(raw["block_type"]),
        text=str(raw["text"]),
        order_index=int(raw["order_index"]),
        metadata={str(k): str(v) for k, v in dict(raw["metadata"]).items()},
        source_location=_location(dict(raw["source_location"])),
        source_links=tuple(links),
    )


def _fixture() -> tuple[dict[str, object], list[HashLogicalBlock]]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    blocks = [_block(raw) for raw in data["blocks"]]
    return data, blocks


def test_wire_contract_revision_is_pinned_to_verified_llm2_blobs() -> None:
    data, _ = _fixture()
    upstream = data["upstream"]
    assert CURRENT_WIRE_CONTRACT.proto_blob_sha == upstream["protoBlobSha"]
    assert CURRENT_WIRE_CONTRACT.grpc_impl_blob_sha == upstream["grpcImplBlobSha"]
    assert CURRENT_WIRE_CONTRACT.cargo_blob_sha == upstream["cargoBlobSha"]
    assert CURRENT_WIRE_CONTRACT.canonical_hash_contract_version == data["contractVersion"]


def test_batch_hash_matches_rust_golden_vector() -> None:
    data, blocks = _fixture()
    assert compute_batch_content_hash(blocks) == data["expected"]["batchContentHash"]


def test_final_hash_matches_rust_render_contract() -> None:
    data, blocks = _fixture()
    assert render_final_content_text(blocks) == data["expected"]["finalRenderedText"]
    assert compute_final_content_hash(blocks) == data["expected"]["finalContentHash"]


def test_batch_json_is_utf8_compact_and_map_order_independent() -> None:
    _, blocks = _fixture()
    canonical = canonical_batch_bytes(blocks)
    assert b": " not in canonical
    assert b", " not in canonical
    assert "ҚАЗАҚ".encode() in canonical

    first = blocks[0]
    reordered = HashLogicalBlock(
        block_id=first.block_id,
        parent_block_id=first.parent_block_id,
        block_type=first.block_type,
        text=first.text,
        order_index=first.order_index,
        metadata={"a": "first", "z": "последний"},
        source_location=first.source_location,
        source_links=(
            HashSourceLink(
                type=first.source_links[0].type,
                url=first.source_links[0].url,
                label=first.source_links[0].label,
                mime_type=first.source_links[0].mime_type,
                requires_auth=first.source_links[0].requires_auth,
                expires_at=first.source_links[0].expires_at,
                attributes={"a": "1", "z": "2"},
            ),
        ),
    )
    assert compute_batch_content_hash([reordered, blocks[1]]) == compute_batch_content_hash(blocks)


def test_block_order_is_part_of_both_hash_contracts() -> None:
    _, blocks = _fixture()
    reversed_blocks = list(reversed(blocks))
    assert compute_batch_content_hash(reversed_blocks) != compute_batch_content_hash(blocks)
    assert compute_final_content_hash(reversed_blocks) != compute_final_content_hash(blocks)


@pytest.mark.parametrize("value", [-1, UINT32_MAX + 1, True, 1.5, "1"])
def test_uint32_guard_rejects_non_wire_values(value: object) -> None:
    with pytest.raises(WireRangeError):
        require_uint32(value, field="batch_index")  # type: ignore[arg-type]


def test_positive_uint32_guard_rejects_zero_document_version() -> None:
    with pytest.raises(WireRangeError):
        require_positive_uint32(0, field="document_version")
    assert require_positive_uint32(UINT32_MAX, field="document_version") == UINT32_MAX


def test_sha256_normalization_matches_server_prefix_semantics() -> None:
    digest = "AB" * 32
    assert normalize_sha256_hex(f" sha256:{digest} ") == digest.lower()
    with pytest.raises(ValueError):
        normalize_sha256_hex("not-a-digest")
