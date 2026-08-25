from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .wire_contract import require_uint32


@dataclass(frozen=True, slots=True)
class HashSourceLocation:
    page_start: int = 0
    page_end: int = 0
    char_start: int = 0
    char_end: int = 0
    section_path: str = ""
    heading: str = ""
    table_id: str = ""
    row_index: int = 0
    column_index: int = 0

    def canonical_value(self) -> dict[str, object]:
        return {
            "page_start": require_uint32(self.page_start, field="source_location.page_start"),
            "page_end": require_uint32(self.page_end, field="source_location.page_end"),
            "char_start": require_uint32(self.char_start, field="source_location.char_start"),
            "char_end": require_uint32(self.char_end, field="source_location.char_end"),
            "section_path": self.section_path,
            "heading": self.heading,
            "table_id": self.table_id,
            "row_index": require_uint32(self.row_index, field="source_location.row_index"),
            "column_index": require_uint32(self.column_index, field="source_location.column_index"),
        }


@dataclass(frozen=True, slots=True)
class HashSourceLink:
    type: int
    url: str
    label: str = ""
    mime_type: str = ""
    requires_auth: bool = False
    expires_at: str = ""
    attributes: Mapping[str, str] = field(default_factory=dict)

    def canonical_value(self) -> dict[str, object]:
        return {
            "type": self.type,
            "url": self.url,
            "label": self.label,
            "mime_type": self.mime_type,
            "requires_auth": self.requires_auth,
            "expires_at": self.expires_at,
            "attributes": dict(self.attributes),
        }


@dataclass(frozen=True, slots=True)
class HashLogicalBlock:
    block_id: str
    parent_block_id: str
    block_type: int
    text: str
    order_index: int
    metadata: Mapping[str, str] = field(default_factory=dict)
    source_location: HashSourceLocation | None = None
    source_links: Sequence[HashSourceLink] = ()

    def canonical_value(self) -> dict[str, object]:
        return {
            "block_id": self.block_id,
            "parent_block_id": self.parent_block_id,
            "block_type": self.block_type,
            "text": self.text,
            "order_index": require_uint32(self.order_index, field="logical_block.order_index"),
            "metadata": dict(self.metadata),
            "source_location": (
                self.source_location.canonical_value() if self.source_location is not None else {}
            ),
            "source_links": [link.canonical_value() for link in self.source_links],
        }


def canonical_batch_bytes(blocks: Sequence[HashLogicalBlock]) -> bytes:
    """Mirror AstraVector compute_batch_content_hash input bytes.

    Rust uses serde_json::to_vec over a Vec<Value>. With serde_json's default Map
    implementation (no preserve_order feature in llm2 Cargo.toml), object/map keys
    are serialized in lexical order. UTF-8 characters are emitted directly and JSON
    separators are compact.
    """

    values = [block.canonical_value() for block in blocks]
    payload = json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return payload.encode("utf-8")


def compute_batch_content_hash(blocks: Sequence[HashLogicalBlock]) -> str:
    if not blocks:
        raise ValueError("batch must contain at least one logical block")
    return hashlib.sha256(canonical_batch_bytes(blocks)).hexdigest()


def render_final_content_text(blocks: Sequence[HashLogicalBlock]) -> str:
    """Mirror AstraVector render_logical_blocks_for_chunking for Finalize."""

    out: list[str] = []
    for block in blocks:
        location = block.source_location
        if location is not None and location.heading.strip():
            out.append(location.heading.strip())
            out.append("\n")
        out.append(block.text.strip())
        out.append("\n\n")
    return "".join(out)


def compute_final_content_hash(blocks: Sequence[HashLogicalBlock]) -> str:
    if not blocks:
        raise ValueError("document must contain at least one logical block")
    rendered = render_final_content_text(blocks)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def normalize_sha256_hex(raw: str) -> str:
    candidate = raw.strip()
    if candidate.startswith("sha256:"):
        candidate = candidate.removeprefix("sha256:")
    candidate = candidate.lower()
    if len(candidate) != 64 or any(ch not in "0123456789abcdef" for ch in candidate):
        raise ValueError("value must be a 64-character sha256 hex string")
    return candidate
