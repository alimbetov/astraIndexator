from __future__ import annotations

import httpx

from astra_indexator.storage import SeaweedFilerStorage, StorageRef


def test_filer_head_and_streaming_get() -> None:
    payload = b"abcdef" * 10

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/documents/original/a%20b.txt"
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(payload)), "etag": '"etag-1"', "content-type": "text/plain"})
        if request.method == "GET":
            return httpx.Response(200, content=payload)
        return httpx.Response(405)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    storage = SeaweedFilerStorage("http://seaweed:8888", client=client)
    ref = StorageRef.parse("seaweed://documents/original/a b.txt")
    head = storage.head(ref)
    assert head.exists is True
    assert head.size_bytes == len(payload)
    assert head.etag == '"etag-1"'
    assert b"".join(storage.iter_bytes(ref, chunk_size=7)) == payload


def test_filer_head_404_becomes_not_found() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    storage = SeaweedFilerStorage("http://seaweed:8888", client=client)
    assert storage.head(StorageRef.parse("seaweed://documents/missing.pdf")).exists is False
