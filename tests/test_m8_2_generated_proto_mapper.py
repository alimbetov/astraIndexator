from __future__ import annotations

from uuid import UUID

from astra_indexator.astravector import (
    AppendBlocksCommand,
    AstraVectorProtoMapper,
    LogicalBlock,
    SourceLink,
    SourceLocation,
    StartIngestionCommand,
    load_generated_client,
)


def test_mapper_uses_pinned_generated_messages_and_preserves_wire_values() -> None:
    client = load_generated_client()
    mapper = AstraVectorProtoMapper(client.pb)

    start = mapper.start_request(
        StartIngestionCommand(
            access_zone_id=None,
            access_zone_code="0001",
            document_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
            document_version=7,
            source_uri="s3://docs/regulation.pdf",
            file_name="regulation.pdf",
            content_hash="AB" * 32,
            idempotency_key="astraindexator:doc:v7",
            total_bytes_estimate=123,
            total_blocks_estimate=1,
            total_pages_estimate=2,
            metadata={"source": "qualification"},
            ttl_days=0,
        )
    )

    assert isinstance(start, client.pb.StartLogicalDocumentIngestionRequest)
    assert start.access_zone_id == ""
    assert start.access_zone_code == "0001"
    assert start.document_version == 7
    assert start.content_hash == "ab" * 32
    assert start.ttl_days == 0

    block = LogicalBlock(
        block_id="p-1",
        parent_block_id="doc-1",
        block_type="PARAGRAPH",
        text="  Текст блока  ",
        order_index=0,
        source_location=SourceLocation(
            page_start=1,
            page_end=1,
            char_start=0,
            char_end=10,
            section_path="1",
            heading="Раздел",
            table_id="",
            row_index=0,
            column_index=0,
        ),
        source_links=(
            SourceLink(
                type="PAGE",
                url="s3://docs/regulation.pdf#page=1",
                label="page 1",
                mime_type="application/pdf",
                requires_auth=True,
                expires_at="",
                attributes={"page": "1"},
            ),
        ),
        metadata={"language": "ru"},
    )

    append = mapper.append_request(
        AppendBlocksCommand(
            ingestion_session_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            blocks=(block,),
            batch_index=0,
            is_last_batch=True,
            batch_content_hash="cd" * 32,
        )
    )

    assert isinstance(append, client.pb.AppendLogicalDocumentBlocksRequest)
    assert len(append.blocks) == 1
    mapped = append.blocks[0]
    assert isinstance(mapped, client.pb.LogicalBlock)
    assert mapped.block_id == "p-1"
    assert mapped.parent_block_id == "doc-1"
    assert mapped.block_type == client.pb.BLOCK_TYPE_PARAGRAPH
    assert mapped.text == "Текст блока"
    assert mapped.order_index == 0
    assert mapped.metadata["language"] == "ru"
    assert mapped.HasField("source_location")
    assert mapped.source_location.page_start == 1
    assert mapped.source_location.heading == "Раздел"
    assert len(mapped.source_links) == 1
    assert mapped.source_links[0].type == client.pb.SOURCE_LINK_TYPE_PAGE
    assert mapped.source_links[0].requires_auth is True
    assert mapped.source_links[0].attributes["page"] == "1"
