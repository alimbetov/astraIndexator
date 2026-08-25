from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from astra_indexator.astravector import (
    AppendBlocksCommand,
    AstraVectorProtoMapper,
    LogicalBlock,
    ProtoMappingError,
    SourceLink,
    SourceLocation,
    StartIngestionCommand,
    UINT32_MAX,
    WireRangeError,
)


class _Message:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


@pytest.fixture
def pb() -> SimpleNamespace:
    return SimpleNamespace(
        BLOCK_TYPE_DOCUMENT=1,
        BLOCK_TYPE_SECTION=2,
        BLOCK_TYPE_SUBSECTION=3,
        BLOCK_TYPE_PARAGRAPH=4,
        BLOCK_TYPE_TABLE=5,
        BLOCK_TYPE_TABLE_ROW=6,
        BLOCK_TYPE_LIST=7,
        BLOCK_TYPE_LIST_ITEM=8,
        BLOCK_TYPE_FAQ_ITEM=9,
        BLOCK_TYPE_CODE_BLOCK=10,
        BLOCK_TYPE_CAPTION=11,
        SOURCE_LINK_TYPE_ORIGINAL_DOCUMENT=1,
        SOURCE_LINK_TYPE_PREVIEW=2,
        SOURCE_LINK_TYPE_DOWNLOAD=3,
        SOURCE_LINK_TYPE_PAGE=4,
        SOURCE_LINK_TYPE_SECTION=5,
        SOURCE_LINK_TYPE_CHUNK=6,
        SOURCE_LINK_TYPE_EXTERNAL_SYSTEM=7,
        StartLogicalDocumentIngestionRequest=_Message,
        AppendLogicalDocumentBlocksRequest=_Message,
        FinalizeLogicalDocumentIngestionRequest=_Message,
        AbortLogicalDocumentIngestionRequest=_Message,
        GetLogicalDocumentIngestionStatusRequest=_Message,
        GetDocumentVectorStatusRequest=_Message,
        RequestContext=_Message,
        DocumentRef=_Message,
        LogicalBlock=_Message,
        SourceLocation=_Message,
        SourceLink=_Message,
    )


def _start(**overrides: object) -> StartIngestionCommand:
    values: dict[str, object] = {
        "access_zone_id": None,
        "access_zone_code": "1500",
        "document_id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        "document_version": 3,
        "source_uri": "minio://docs/regulation.pdf",
        "file_name": "regulation.pdf",
        "content_hash": "AB" * 32,
        "idempotency_key": "astraindexator:doc:v3",
        "total_bytes_estimate": 123456,
        "total_blocks_estimate": 42,
        "total_pages_estimate": 7,
        "metadata": {"source": "bank"},
        "ttl_days": 0,
    }
    values.update(overrides)
    return StartIngestionCommand(**values)  # type: ignore[arg-type]


def test_start_request_maps_session_wire_fields(pb: SimpleNamespace) -> None:
    request = AstraVectorProtoMapper(pb).start_request(_start())

    assert request.access_zone_id == ""
    assert request.access_zone_code == "1500"
    assert request.document_id == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert request.document_version == 3
    assert request.content_hash == ("ab" * 32)
    assert request.ttl_days == 0
    assert request.total_bytes_estimate == 123456
    assert request.metadata == {"source": "bank"}


def test_start_allows_uuid_selector_without_code(pb: SimpleNamespace) -> None:
    zone_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    request = AstraVectorProtoMapper(pb).start_request(
        _start(access_zone_id=zone_id, access_zone_code=None)
    )
    assert request.access_zone_id == str(zone_id)
    assert request.access_zone_code == ""


def test_start_rejects_missing_or_malformed_access_zone(pb: SimpleNamespace) -> None:
    mapper = AstraVectorProtoMapper(pb)
    with pytest.raises(ProtoMappingError, match="selector"):
        mapper.start_request(_start(access_zone_id=None, access_zone_code=None))
    with pytest.raises(ProtoMappingError, match="four ASCII digits"):
        mapper.start_request(_start(access_zone_code="150"))


def test_start_rejects_uint32_overflow_before_wire_call(pb: SimpleNamespace) -> None:
    with pytest.raises(WireRangeError):
        AstraVectorProtoMapper(pb).start_request(_start(document_version=UINT32_MAX + 1))


def test_append_maps_nested_logical_block_and_provenance(pb: SimpleNamespace) -> None:
    block = LogicalBlock(
        block_id="p-1",
        parent_block_id="doc-1",
        block_type="PARAGRAPH",
        text="  Нормализованный текст  ",
        order_index=5,
        source_location=SourceLocation(
            page_start=2,
            page_end=2,
            char_start=10,
            char_end=34,
            section_path="1/1.2",
            heading="Требования",
        ),
        source_links=(
            SourceLink(
                type="PAGE",
                url="minio://docs/regulation.pdf#page=2",
                label="page 2",
                attributes={"page": "2"},
            ),
        ),
        metadata={"language": "ru"},
    )
    command = AppendBlocksCommand(
        ingestion_session_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        blocks=(block,),
        batch_index=4,
        is_last_batch=True,
        batch_content_hash="cd" * 32,
    )

    request = AstraVectorProtoMapper(pb).append_request(command)

    assert request.ingestion_session_id == "cccccccc-cccc-cccc-cccc-cccccccccccc"
    assert request.batch_index == 4
    assert request.is_last_batch is True
    assert request.batch_content_hash == "cd" * 32
    assert len(request.blocks) == 1
    mapped = request.blocks[0]
    assert mapped.block_type == pb.BLOCK_TYPE_PARAGRAPH
    assert mapped.text == "Нормализованный текст"
    assert mapped.source_location.page_start == 2
    assert mapped.source_links[0].type == pb.SOURCE_LINK_TYPE_PAGE
    assert mapped.source_links[0].attributes == {"page": "2"}


def test_mapper_rejects_unspecified_application_enums(pb: SimpleNamespace) -> None:
    mapper = AstraVectorProtoMapper(pb)
    with pytest.raises(ProtoMappingError, match="block_type"):
        mapper.logical_block(
            LogicalBlock(
                block_id="x",
                parent_block_id="",
                block_type="UNSPECIFIED",
                text="text",
                order_index=0,
            )
        )
    with pytest.raises(ProtoMappingError, match="source_link.type"):
        mapper.source_link(SourceLink(type="UNSPECIFIED", url="https://example.test"))


def test_source_location_range_order_is_validated_locally(pb: SimpleNamespace) -> None:
    mapper = AstraVectorProtoMapper(pb)
    with pytest.raises(ProtoMappingError, match="page_end"):
        mapper.source_location(SourceLocation(page_start=4, page_end=3), owner="b1")
    with pytest.raises(ProtoMappingError, match="char_end"):
        mapper.source_location(SourceLocation(char_start=20, char_end=10), owner="b1")


def test_generated_module_contract_mismatch_fails_fast(pb: SimpleNamespace) -> None:
    del pb.BLOCK_TYPE_PARAGRAPH
    with pytest.raises(ProtoMappingError, match="contract revision mismatch"):
        AstraVectorProtoMapper(pb).logical_block(
            LogicalBlock(
                block_id="p",
                parent_block_id="doc",
                block_type="PARAGRAPH",
                text="text",
                order_index=1,
            )
        )
