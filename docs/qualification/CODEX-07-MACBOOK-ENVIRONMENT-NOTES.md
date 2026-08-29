# CODEX-07 MacBook Environment Notes

Date: 2026-08-29

Purpose: preserve local AstraVector/AstraDeployment environment facts so later Codex runs can resume
B2 qualification without rediscovering the same machine state.

## Host

| Item | Value |
| --- | --- |
| Host architecture | `arm64` |
| Docker engine architecture | `aarch64` |
| Docker Desktop server | `28.3.2` |
| Docker client | `28.3.3`, context `desktop-linux` |
| CODEX-07 architecture gate | Compatible with the validated `linux/arm64` baseline |

## Repositories

| Repository | Local path | Branch | SHA |
| --- | --- | --- | --- |
| AstraIndexator | `/Users/ruslanalimbetov/Documents/AstraIndexator/astraIndexator-verification` | `codex/real-pdf-smoke` | `029fb44` |
| AstraDeployment Portable | `/Users/ruslanalimbetov/Documents/AstraIndexator/agent-astradeployment-portable-local-1.0` | `main` | `cc7dd29256065be1734223fc112813e445109bd0` |
| llm2 | `/Users/ruslanalimbetov/Documents/AstraIndexator/llm2` | `main` | `2142e2bd328c8964dbb3c2eff52caf8a350c5ddf` |

Baseline file hashes from AstraDeployment Portable:

| File | SHA-256 |
| --- | --- |
| `deploy/local/docker-compose.astravector.yml` | `bd4c2f0677fb9d749e81378bc821f4b1446b5fafbdf2ed51ba50a65108073f2d` |
| `deploy/local/.env.example` | `9c4c700ee85858548c975b00b67a1fc26715b8fd87c4544f9aa21a8ed3b1935d` |
| `docs/ASTRADEPLOYMENT_PORTABLE_LOCAL_1_0_SPEC.md` | `92163bead78b1f6cba0951216016870575f97e0247aa413e31132202dda525da` |
| `docs/integration/ASTRAINDEXATOR_INTEGRATION_CONTRACT.md` | `e55148fd544cd33c9800905362975f73cc00c943384a51c88fe3e9ed5d2f7f59` |

## AstraVector Images

Validated image required by CODEX-07 is present locally:

| Repository | Tag | Digest | Image ID prefix |
| --- | --- | --- | --- |
| `registry.astrabase.asia/astravector` | `sha-1cb6065` | `sha256:b0567810b5ea3df752ff8ba559fcf16bc46b245878e798b8888dcf93426ee6ad` | `b0567810b5ea` |

Other local AstraVector images exist and must not be substituted silently:

| Tag | Digest |
| --- | --- |
| `sha-fa9729f` | `sha256:05fefd2f869b0d6cb3d63c9f7ef1dd2d8dd922cc384196206c93c70a85c02a49` |
| `sha-7aed252` | `sha256:060832f8d9763eaf990b4a595db4b6d7cb231c54348f5837747977972ebe6436` |
| `sha-26288b4` / `0.4.1-image-contract` | `sha256:77174cf14b1856b57f95ff96e96ee8c4c04df83034bd9af5127aaba287a6393a` |

## Existing Runtime State Observed

Before CODEX-07 portable startup, this MacBook had an older manually named runtime active:

| Container | Image | Ports | State before cleanup |
| --- | --- | --- | --- |
| `astravector-smoke-runtime` | `registry.astrabase.asia/astravector:sha-1cb6065` | `127.0.0.1:50051`, `127.0.0.1:9090` | Up 7 days |
| `astravector-smoke-qdrant` | `qdrant/qdrant:v1.14.1` | internal only | Up 7 days |
| `astravector-smoke-postgres` | image ID `131dcf7ff6a9` | internal only | Up 7 days |

That runtime did not publish host HTTP `8080`, so it is insufficient for CODEX-07 public retrieval
evidence even though the AstraVector image tag/digest was correct.

An older compose project also existed:

| Compose project | Config file | State observed |
| --- | --- | --- |
| `astravector` | `/Users/ruslanalimbetov/Documents/llm2/astravector/docker-compose.yml` | Running Postgres/Qdrant before cleanup |

It occupied `127.0.0.1:6333`, blocking portable Qdrant startup. It was stopped without deleting
volumes.

## Portable Startup Attempt

Actions performed:

| Action | Result |
| --- | --- |
| Created ignored `deploy/local/.env` in AstraDeployment clone | Done; not committed |
| Stopped `astravector-smoke-runtime`, `astravector-smoke-qdrant`, `astravector-smoke-postgres` | Done; no volume deletion |
| `make preflight` from `deploy/local` | PASS |
| First `make start` | BLOCKED by existing `6333` listener from old llm2 compose project |
| Stopped old llm2 `astravector` compose project | Done; no volume deletion |
| Second `make start` | Image digest PASS, containers created, then AstraVector restart-loop |
| `make stop` | Done; portable containers stopped, persistent volumes preserved |

`make preflight` observed:

```text
Docker: ready
Architecture: arm64
Free disk: 9 GiB
Model cache: existing
PREFLIGHT_PASS
```

`make start` verified the required image identity before startup:

```text
AstraVector image: ARCH=arm64 OS=linux DIGESTS=["registry.astrabase.asia/astravector@sha256:b0567810b5ea3df752ff8ba559fcf16bc46b245878e798b8888dcf93426ee6ad"]
```

Startup failure classification:

```text
ASTRADEPLOYMENT_START = BLOCKED
CAUSE = reused astradeployment-postgres-data volume had an existing database password that did not match the newly created local .env
NOT_AN_ASTRAINDEXATOR_FAILURE = true
```

Observed AstraVector log line:

```text
Error: unavailable: postgres: error returned from database: password authentication failed for user "astravector_app"
```

## Current State After Cleanup

After `make stop`:

| Item | State |
| --- | --- |
| Portable `astradeployment-*` containers | Stopped |
| Older `astravector-smoke-*` containers | Stopped |
| Older llm2 `astravector-*` compose containers | Stopped |
| Listeners on `50051`, `8080`, `9090`, `6333` | None observed |
| AstraDeployment persistent volumes | Preserved |
| Model cache volume | Preserved |

Several unrelated PostgreSQL testcontainers remain running on host ports `55378-55445`. They do not
conflict with default AstraVector ports, but later runs may choose to clean them separately if disk
or Docker noise becomes a problem.

## Next Run Guidance

To continue CODEX-07, do not rediscover the old runtime first. Start from:

```bash
cd /Users/ruslanalimbetov/Documents/AstraIndexator/agent-astradeployment-portable-local-1.0/deploy/local
```

Then choose one of these paths:

1. If preserving the existing `astradeployment-postgres-data` volume is required, recover the
   original Postgres password and update the ignored `.env` to match it.
2. If a fresh B2 baseline is acceptable, stop containers and recreate only the portable
   `astradeployment-*` volumes intentionally before `make start`; record that as a fresh baseline
   reset.

Do not use the old `astravector-smoke-runtime` as final CODEX-07 evidence unless it is recreated with
the required public HTTP `8080` endpoint and the rest of the portable baseline gates.

Do not commit `.env`, PDF files, extracted CV text, generated protobuf files, or smoke outputs.

## Resolution And Revalidation

Chronological update from CODEX-07B continuation:

| Step | Result |
| --- | --- |
| Initial portable start | `BLOCKED`; preserved PostgreSQL volume password mismatch |
| Root cause | `astradeployment-postgres-data` was reused with credentials that did not match the newly created ignored `.env` |
| Remediation | Synchronized the local `astravector_app` role password with the ignored `deploy/local/.env` |
| Network auth check | PASS from a separate container on `astradeployment-network` |
| `make start` | PASS |
| `make health` | PASS |
| `make smoke` | PASS |
| Current runtime state | `B2_RUNTIME_READY = YES` |

Current public endpoints:

| Endpoint | Address | State |
| --- | --- | --- |
| AstraVector gRPC | `127.0.0.1:50051` | published |
| AstraVector HTTP | `127.0.0.1:8080` | published; `/ready` returned `{"ready":true,"status":"READY"}` |
| AstraVector metrics | `127.0.0.1:9090` | published |
| Qdrant HTTP | `127.0.0.1:6333` | published by portable baseline |

Current running portable containers after remediation:

| Container | Image | State |
| --- | --- | --- |
| `astradeployment-astravector-1` | `registry.astrabase.asia/astravector:sha-1cb6065` | healthy |
| `astradeployment-postgres-1` | `pgvector/pgvector:pg16` | healthy |
| `astradeployment-qdrant-1` | `qdrant/qdrant:v1.14.1` | running |

The validated AstraVector image identity remained:

```text
registry.astrabase.asia/astravector:sha-1cb6065
sha256:b0567810b5ea3df752ff8ba559fcf16bc46b245878e798b8888dcf93426ee6ad
```

Native AstraDeployment smoke completed after remediation. It used the deployment's own smoke
document, not the real CV/PDF used by AstraIndexator qualification.
