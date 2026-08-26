from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from types import ModuleType


class GeneratedClientUnavailable(RuntimeError):
    """Pinned AstraVector generated Python client is unavailable in this build/runtime."""


@dataclass(frozen=True, slots=True)
class GeneratedAstraVectorClient:
    pb: ModuleType
    pb_grpc: ModuleType


def load_generated_client() -> GeneratedAstraVectorClient:
    try:
        pb = import_module("astra_indexator.astravector.generated.astravector_embedding_pb2")
        pb_grpc = import_module(
            "astra_indexator.astravector.generated.astravector_embedding_pb2_grpc"
        )
    except ModuleNotFoundError as exc:
        raise GeneratedClientUnavailable(
            "AstraVector generated client is missing. Run "
            "`python tools/generate_astravector_proto.py` during the build from the pinned llm2 proto."
        ) from exc

    required = (
        "StartLogicalDocumentIngestionRequest",
        "LogicalBlock",
        "SourceLocation",
        "SourceLink",
    )
    missing = [name for name in required if not hasattr(pb, name)]
    if missing or not hasattr(pb_grpc, "AstraVectorIngestionFacadeStub"):
        details = ", ".join(missing) if missing else "AstraVectorIngestionFacadeStub"
        raise GeneratedClientUnavailable(
            f"generated AstraVector client does not match pinned contract: missing {details}"
        )
    return GeneratedAstraVectorClient(pb=pb, pb_grpc=pb_grpc)
