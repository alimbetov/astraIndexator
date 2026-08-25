from __future__ import annotations

from dataclasses import dataclass

UINT32_MAX = 4_294_967_295
UINT64_MAX = 18_446_744_073_709_551_615

ASTRAVECTOR_PROTO_REPOSITORY = "alimbetov/llm2"
ASTRAVECTOR_PROTO_PATH = "proto/astravector_embedding.proto"
ASTRAVECTOR_PROTO_BLOB_SHA = "ed1eab5f56dfb73cc48927ad2effb759a2c4e01e"
ASTRAVECTOR_GRPC_IMPL_PATH = "src/grpc/mod.rs"
ASTRAVECTOR_GRPC_IMPL_BLOB_SHA = "07afff94c4f3825ec953df55cd407ed7c653151a"
ASTRAVECTOR_CARGO_BLOB_SHA = "c93a6e7e4f20995297e62f8be45503b107e55fa5"
CANONICAL_HASH_CONTRACT_VERSION = "astravector-v007-fix4.5.2"


class WireRangeError(ValueError):
    """Raised when a domain integer cannot be represented by the protobuf wire field."""


def require_uint32(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WireRangeError(f"{field} must be an integer")
    if value < 0 or value > UINT32_MAX:
        raise WireRangeError(f"{field} must be in uint32 range 0..{UINT32_MAX}")
    return value


def require_positive_uint32(value: int, *, field: str) -> int:
    value = require_uint32(value, field=field)
    if value == 0:
        raise WireRangeError(f"{field} must be > 0")
    return value


def require_uint64(value: int, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WireRangeError(f"{field} must be an integer")
    if value < 0 or value > UINT64_MAX:
        raise WireRangeError(f"{field} must be in uint64 range 0..{UINT64_MAX}")
    return value


@dataclass(frozen=True, slots=True)
class WireContractRevision:
    repository: str = ASTRAVECTOR_PROTO_REPOSITORY
    proto_path: str = ASTRAVECTOR_PROTO_PATH
    proto_blob_sha: str = ASTRAVECTOR_PROTO_BLOB_SHA
    grpc_impl_path: str = ASTRAVECTOR_GRPC_IMPL_PATH
    grpc_impl_blob_sha: str = ASTRAVECTOR_GRPC_IMPL_BLOB_SHA
    cargo_blob_sha: str = ASTRAVECTOR_CARGO_BLOB_SHA
    canonical_hash_contract_version: str = CANONICAL_HASH_CONTRACT_VERSION


CURRENT_WIRE_CONTRACT = WireContractRevision()
