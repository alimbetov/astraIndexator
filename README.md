# AstraIndexator

AstraIndexator is an internal document indexing service responsible for acquiring immutable source documents, parsing and OCR, multilingual normalization and logical segmentation, deterministic `LogicalBlock[]` preparation, durable replay, and lease-fenced delivery to AstraVector for tokenizer-aware vectorization and retrieval.

## Current phase

The TZ-00..TZ-18 architecture/specification baseline is implemented through the M1–M7 foundation, and M8 AstraVector Delivery & Reliability is in completion remediation and final qualification.

Current high-level status:

```text
M1 Persistence Foundation            ✅ qualified / merged
M2 Job Coordinator                   ✅ qualified / merged
M3 SeaweedFS Safe Acquisition        ✅ implemented
M4 Canonical Parser                  ✅ implemented
M5 OCR Pipeline                      ✅ implemented / hardened
M6 Normalization & Logical Splitter  ✅ implemented / hardened
M7 Prepared Artifacts & Replay       ✅ qualified
M8 AstraVector Delivery              🚧 completion remediation / final qualification open
M9+                                  ⬜ planned after M8 qualification
```

The active implementation roadmap is [`docs/IMPLEMENTATION-ROADMAP-2.0.md`](docs/IMPLEMENTATION-ROADMAP-2.0.md).

The current M8 remediation contract is [`docs/M8-COMPLETION-REMEDIATION-SPEC.md`](docs/M8-COMPLETION-REMEDIATION-SPEC.md).

## AccessZone contract

AstraIndexator producer/domain/inventory AccessZone identity is `access_zone_code` only. Producer `accessZoneId/accessZoneIds` are not supported. AstraVector owns code-to-internal-UUID resolution. `delivery_checkpoint.resolved_access_zone_id` is retained only as private downstream recovery/status evidence required by the finalized AstraVector public wire.

See [`docs/ACCESS-ZONE-CODE-CONTRACT-FREEZE.md`](docs/ACCESS-ZONE-CODE-CONTRACT-FREEZE.md).

## Documentation

Canonical architecture and subsystem technical specifications are maintained under [`docs/`](docs/). Implementation changes must preserve those contracts or explicitly supersede the affected historical requirement in the same reviewed change.

## Runtime bootstrap

Canonical production-oriented startup command:

```bash
python -m astra_indexator
```

Prerequisites:

- Python 3.12 or newer;
- PostgreSQL with the Alembic schema migrated to `head`;
- generated AstraVector Python gRPC client from the pinned wire contract before executing M8 delivery;
- syntactically valid AstraVector gRPC endpoint.

Install and prepare a clean checkout:

```bash
python -m pip install --upgrade pip
pip install -e ".[dev]"
python tools/generate_astravector_proto.py
```

Run database migrations explicitly before starting the worker:

```bash
ASTRA_INDEXATOR_DATABASE_URL=postgresql+psycopg://astra_indexator:astra_indexator@localhost:5432/astra_indexator \
  alembic upgrade head
```

Required runtime environment:

```text
ASTRA_INDEXATOR_DATABASE_URL=postgresql+psycopg://user:password@host:5432/database
```

Optional runtime environment:

```text
ASTRA_INDEXATOR_ASTRAVECTOR_GRPC_TARGET=astravector:50051
ASTRA_INDEXATOR_WORKER_ID=<hostname-pid default>
ASTRA_INDEXATOR_LEASE_SECONDS=120
ASTRA_INDEXATOR_POLL_INTERVAL_SECONDS=1.0
ASTRA_INDEXATOR_RPC_TIMEOUT_SECONDS=30.0
ASTRA_INDEXATOR_RPC_SAFETY_MARGIN_SECONDS=5.0
ASTRA_INDEXATOR_LOG_LEVEL=INFO
```

Startup validates PostgreSQL connectivity and the current Alembic head. It does not run
`Base.metadata.create_all()` and does not apply migrations automatically. AstraVector availability is
lazy: the process may start and poll with no queued M8 delivery work when the endpoint is
syntactically valid; downstream gRPC use occurs only when delivery is executed.

The runtime handles `SIGINT` and `SIGTERM` by stopping new job claims, allowing the current bounded
operation to finish through the existing lease-fenced/durable failure path, disposing database
resources, and exiting deterministically.

Real AstraVector end-to-end qualification is not claimed here. For B2/real-service qualification,
the portable AstraVector deployment is maintained at
[`alimbetov/agent-astradeployment-portable-local-1.0`](https://github.com/alimbetov/agent-astradeployment-portable-local-1.0).
