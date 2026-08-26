from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from astra_indexator.astravector.contracts import LogicalBlock, SourceLocation
from astra_indexator.prepared_artifacts.model import PreparedArtifact


class PreparedArtifactDeliveryMappingError(ValueError):
    """M7 prepared artifact cannot be mapped to the qualified M8.2 application contract."""


_FRAGMENT_BLOCK_TYPES = {
    "SECTION": "SECTION",
    "PARAGRAPH": "PARAGRAPH",
    "LIST": "LIST",
    "TABLE": "TABLE",
    "CODE": "CODE_BLOCK",
    "OCR": "PARAGRAPH",
    "OTHER": "PARAGRAPH",
}


@dataclass(frozen=True, slots=True)
class PreparedArtifactDeliveryMapper:
    """Pure M7 -> M8.2 handoff; no persistence, retry, transport, or recovery behavior."""

    def logical_blocks(self, artifact: PreparedArtifact) -> tuple[LogicalBlock, ...]:
        identity = artifact.manifest.identity
        if not artifact.fragments:
            raise PreparedArtifactDeliveryMappingError(
                "prepared artifact contains no logical fragments for AstraVector delivery"
            )

        records = tuple(
            self._validated_fragment(record, identity.document_id, identity.document_version)
            for record in artifact.fragments
        )
        fragment_ids = [str(record["fragment_id"]) for record in records]
        sequences = [int(record["sequence"]) for record in records]
        if len(fragment_ids) != len(set(fragment_ids)):
            raise PreparedArtifactDeliveryMappingError(
                "prepared artifact fragment_id values must be unique"
            )
        if len(sequences) != len(set(sequences)):
            raise PreparedArtifactDeliveryMappingError(
                "prepared artifact fragment sequence values must be unique"
            )

        ordered = tuple(
            sorted(
                records, key=lambda record: (int(record["sequence"]), str(record["fragment_id"]))
            )
        )
        root_id = f"document:{identity.document_id}:v{identity.document_version}"
        root = LogicalBlock(
            block_id=root_id,
            parent_block_id="",
            block_type="DOCUMENT",
            text=f"Document {identity.document_id} v{identity.document_version}",
            order_index=0,
            metadata={
                "astra.synthetic_root": "true",
                "astra.source_sha256": identity.source_sha256,
                "astra.prepared_artifact_id": artifact.manifest.artifact_id,
            },
        )

        blocks = [root]
        for order_index, record in enumerate(ordered, start=1):
            blocks.append(
                self._logical_block(record, parent_block_id=root_id, order_index=order_index)
            )
        return tuple(blocks)

    @staticmethod
    def _validated_fragment(
        record: Mapping[str, Any], document_id: UUID, document_version: int
    ) -> Mapping[str, Any]:
        required = {
            "fragment_id",
            "document_id",
            "document_version",
            "sequence",
            "fragment_type",
            "normalized_text",
            "context_prefix",
            "hierarchy",
            "source",
            "statistics",
            "split",
            "primary_language",
            "languages",
            "mixed_language",
            "metadata",
        }
        missing = required.difference(record)
        if missing:
            raise PreparedArtifactDeliveryMappingError(
                f"prepared fragment is missing canonical M7 fields: {sorted(missing)}"
            )
        try:
            record_document_id = UUID(str(record["document_id"]))
            record_document_version = int(record["document_version"])
            sequence = int(record["sequence"])
        except (TypeError, ValueError) as exc:
            raise PreparedArtifactDeliveryMappingError(
                "prepared fragment contains malformed identity/sequence"
            ) from exc
        if record_document_id != document_id or record_document_version != document_version:
            raise PreparedArtifactDeliveryMappingError(
                "prepared fragment identity does not match prepared artifact manifest"
            )
        if sequence < 0:
            raise PreparedArtifactDeliveryMappingError(
                "prepared fragment sequence must be non-negative"
            )
        if not str(record["fragment_id"]).strip():
            raise PreparedArtifactDeliveryMappingError("prepared fragment_id must not be blank")
        if not str(record["normalized_text"]).strip():
            raise PreparedArtifactDeliveryMappingError("prepared normalized_text must not be blank")
        fragment_type = str(record["fragment_type"]).strip().upper()
        if fragment_type not in _FRAGMENT_BLOCK_TYPES:
            raise PreparedArtifactDeliveryMappingError(
                f"unsupported prepared fragment_type: {record['fragment_type']!r}"
            )
        if not isinstance(record["source"], Mapping):
            raise PreparedArtifactDeliveryMappingError("prepared fragment source must be an object")
        if not isinstance(record["metadata"], Mapping):
            raise PreparedArtifactDeliveryMappingError(
                "prepared fragment metadata must be an object"
            )
        if not isinstance(record["hierarchy"], Sequence) or isinstance(record["hierarchy"], str):
            raise PreparedArtifactDeliveryMappingError(
                "prepared fragment hierarchy must be a sequence"
            )
        return record

    def _logical_block(
        self,
        record: Mapping[str, Any],
        *,
        parent_block_id: str,
        order_index: int,
    ) -> LogicalBlock:
        fragment_type = str(record["fragment_type"]).strip().upper()
        normalized_text = str(record["normalized_text"]).strip()
        context_prefix = str(record["context_prefix"] or "").strip()
        text = f"{context_prefix}\n\n{normalized_text}" if context_prefix else normalized_text
        hierarchy = tuple(str(item) for item in record["hierarchy"] if str(item).strip())
        source = record["source"]
        assert isinstance(source, Mapping)

        metadata = self._metadata(record, source, context_prefix)
        return LogicalBlock(
            block_id=str(record["fragment_id"]).strip(),
            parent_block_id=parent_block_id,
            block_type=_FRAGMENT_BLOCK_TYPES[fragment_type],
            text=text,
            order_index=order_index,
            source_location=SourceLocation(
                page_start=self._non_negative(source.get("page_from")),
                page_end=self._non_negative(source.get("page_to")),
                section_path=" / ".join(hierarchy),
                heading=hierarchy[-1] if hierarchy else "",
                row_index=self._single_row_index(source),
            ),
            metadata=metadata,
        )

    def _metadata(
        self,
        record: Mapping[str, Any],
        source: Mapping[str, Any],
        context_prefix: str,
    ) -> dict[str, str]:
        raw_metadata = record["metadata"]
        assert isinstance(raw_metadata, Mapping)
        metadata = {str(key): self._string_value(value) for key, value in raw_metadata.items()}
        metadata.update(
            {
                "astra.fragment_type": str(record["fragment_type"]),
                "astra.fragment_sequence": str(record["sequence"]),
                "astra.primary_language": str(record["primary_language"]),
                "astra.mixed_language": "true" if bool(record["mixed_language"]) else "false",
                "astra.source_element_ids": self._string_value(source.get("element_ids", [])),
            }
        )
        if context_prefix:
            metadata["astra.context_prefix"] = context_prefix
        if source.get("table_row_from") is not None:
            metadata["astra.table_row_from"] = str(source["table_row_from"])
        if source.get("table_row_to") is not None:
            metadata["astra.table_row_to"] = str(source["table_row_to"])
        return metadata

    @staticmethod
    def _non_negative(value: Any) -> int:
        if value is None:
            return 0
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise PreparedArtifactDeliveryMappingError("source locator must be an integer") from exc
        if result < 0:
            raise PreparedArtifactDeliveryMappingError("source locator must be non-negative")
        return result

    @classmethod
    def _single_row_index(cls, source: Mapping[str, Any]) -> int:
        row_from = source.get("table_row_from")
        row_to = source.get("table_row_to")
        if row_from is None or row_to is None:
            return 0
        start = cls._non_negative(row_from)
        end = cls._non_negative(row_to)
        return start if start == end else 0

    @staticmethod
    def _string_value(value: Any) -> str:
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
