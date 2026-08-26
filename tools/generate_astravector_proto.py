from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

REPOSITORY = "alimbetov/llm2"
PROTO_BLOB_SHA = "ed1eab5f56dfb73cc48927ad2effb759a2c4e01e"
PROTO_NAME = "astravector_embedding.proto"
ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "src" / "astra_indexator" / "astravector" / "generated"


def _git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()


def _fetch_proto() -> bytes:
    url = f"https://api.github.com/repos/{REPOSITORY}/git/blobs/{PROTO_BLOB_SHA}"
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    content = base64.b64decode(payload["content"])
    actual = _git_blob_sha(content)
    if actual != PROTO_BLOB_SHA:
        raise RuntimeError(
            f"AstraVector proto blob mismatch: expected {PROTO_BLOB_SHA}, got {actual}"
        )
    return content


def main() -> int:
    GENERATED.mkdir(parents=True, exist_ok=True)
    proto_path = GENERATED / PROTO_NAME
    proto_path.write_bytes(_fetch_proto())

    subprocess.run(
        [
            sys.executable,
            "-m",
            "grpc_tools.protoc",
            f"-I{GENERATED}",
            f"--python_out={GENERATED}",
            f"--grpc_python_out={GENERATED}",
            str(proto_path),
        ],
        check=True,
    )

    grpc_file = GENERATED / "astravector_embedding_pb2_grpc.py"
    text = grpc_file.read_text(encoding="utf-8")
    text = text.replace(
        "import astravector_embedding_pb2 as astravector__embedding__pb2",
        "from . import astravector_embedding_pb2 as astravector__embedding__pb2",
    )
    grpc_file.write_text(text, encoding="utf-8")

    (GENERATED / "__init__.py").write_text(
        '"""Generated from pinned alimbetov/llm2 AstraVector protobuf contract."""\n',
        encoding="utf-8",
    )
    proto_path.unlink()
    print(f"Generated AstraVector Python gRPC client from Git blob {PROTO_BLOB_SHA}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
