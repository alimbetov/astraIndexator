from __future__ import annotations

import httpx
import pytest

from astra_indexator.prepared_artifacts import SeaweedPreparedArtifactStore


def test_seaweed_adapter_put_get_head_and_stream() -> None:
    objects: dict[str, bytes] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "HEAD":
            return httpx.Response(200 if path in objects else 404)
        if request.method == "PUT":
            objects[path] = request.content
            return httpx.Response(201)
        if request.method == "GET":
            if path not in objects:
                return httpx.Response(404)
            return httpx.Response(200, content=objects[path])
        return httpx.Response(405)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://seaweed")
    store = SeaweedPreparedArtifactStore("http://seaweed", bucket="prepared", client=client)
    key = "prepared/v1/doc/manifest.json"
    assert not store.exists(key)
    assert store.put_if_absent(key, b"manifest", content_type="application/json")
    assert store.exists(key)
    assert not store.put_if_absent(key, b"other", content_type="application/json")
    assert store.get(key) == b"manifest"
    assert b"".join(store.iter_bytes(key, chunk_size=3)) == b"manifest"


def test_seaweed_adapter_missing_object_and_path_traversal_fail_closed() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    store = SeaweedPreparedArtifactStore("http://seaweed", bucket="prepared", client=client)
    with pytest.raises(FileNotFoundError):
        store.get("missing")
    with pytest.raises(ValueError):
        store.get("../escape")
