# CODEX-03 B1 Integration Result

## 34.1 Baseline

repository: https://github.com/alimbetov/astraIndexator  
target branch: spec/m8-completion-remediation  
target starting SHA: 4121e452bbe6dfcfc81c7ab43aa3c5ebf6e72f23 (`origin/spec/m8-completion-remediation` was not present after fetch; local target branch was created from `origin/main`)  
CR-06 branch: codex/runtime-bootstrap-cr06  
CR-06 source SHA: 0b5f6891ded8de92c4a1a7910595aeeba49ebee2  
CR-06 commit SHA: 0b5f6891ded8de92c4a1a7910595aeeba49ebee2  
merge-base: 4121e452bbe6dfcfc81c7ab43aa3c5ebf6e72f23  
integration ending SHA: 68a6a498848dedbe9b5ee1e287209e1f7068ee79  
verification environment: macOS/Darwin, Python 3.12.7, pip 26.2.1, Docker 28.3.3, Docker Compose v2.38.2-desktop.1  
verification date: 2026-08-28

## 34.2 Integration summary

integration method: `git fetch --all --prune`, local target branch creation from `origin/main`, then `git cherry-pick 0b5f6891ded8de92c4a1a7910595aeeba49ebee2`.  
conflicts encountered: none.  
conflict resolution: not applicable.  

production files changed:

- `.gitignore`: excludes reproducibly generated AstraVector gRPC client output.
- `src/astra_indexator/__main__.py`: thin canonical module entry point for `python -m astra_indexator`; startup validation failures return code 2 with a concise diagnostic.
- `src/astra_indexator/runtime/config.py`: explicit runtime configuration validation for PostgreSQL DSN, AstraVector endpoint, worker identity, lease, poll interval, RPC timeout/safety margin and log level.
- `src/astra_indexator/runtime/db.py`: validates PostgreSQL connectivity and current Alembic head without automatic migration or ORM `create_all()`.
- `src/astra_indexator/runtime/composition.py`: production composition root for engine/session factory, `JobCoordinator`, `DurableFailureHandler`, lazy AstraVector delivery executor and worker loop.
- `src/astra_indexator/runtime/worker.py`: polling worker loop, shutdown controller and durable failure path for unsupported runtime payload wiring.
- `src/astra_indexator/runtime/__init__.py`: runtime package exports.

tests changed:

- `tests/test_runtime_bootstrap.py`: adds focused runtime coverage for configuration validation, unavailable database validation, composition construction, no-job polling, shutdown request behavior, and durable failure handler use for unsupported runtime path.
- Existing M8 tests were retained. Formatting/import ordering was repaired in M8 test files without weakening assertions.

documentation changed:

- `README.md`: documents prerequisites, migration command, canonical startup command, environment variables, shutdown behavior, lazy AstraVector dependency semantics and the portable AstraVector deployment reference for later B2.
- `docs/M8-COMPLETION-REMEDIATION-SPEC.md`: documents CR-06 as part of B1 while preserving the boundary that formal B1 PASS and M8 qualification require GitHub CI, merge and post-merge evidence.

## 34.3 CR matrix

| CR | Implementation | Tests | Result |
| --- | --- | --- | --- |
| CR-01 | present | present | PASS |
| CR-02 | present | present | PASS |
| CR-03 | present | present | PASS |
| CR-04 | present | present | PASS locally; real AstraVector convergence remains B2 |
| CR-05 | present | present | PASS |
| CR-06 | present | present | PASS |

Evidence:

- CR-01 stable Start identity: `start_idempotency_key()` derives `astra-indexator:{document_id}:{document_version}:{source_sha256}` and `tests/test_m8_completion_remediation.py` remains present.
- CR-02 verified source identity: `resolve_verified_source_sha256()` requires durable lowercase SHA-256 and rejects missing/malformed/conflicting payload evidence; M8 coordinator tests remain present.
- CR-03 durable runtime failure execution: `AstraVectorDeliveryExecutor` and `DurableFailureHandler` remain the failure path; runtime worker tests prove unsupported runtime path does not bypass the durable handler.
- CR-04 ambiguous Finalize: local fail-closed reconciliation behavior remains tested; no local claim is made about real AstraVector public-contract convergence.
- CR-05 compatibility fingerprint: migration `0007_m8_delivery_compatibility` remains in the Alembic chain and the clean DB schema contains `delivery_checkpoint.delivery_compatibility_sha256`.
- CR-06 runtime bootstrap: `python -m astra_indexator` starts, validates PostgreSQL, enters worker loop, remains alive on empty queue and exits cleanly on SIGTERM.

## 35. Verification matrix

| Gate | Required | Result | Evidence |
| --- | --- | --- | --- |
| Ruff lint | PASS | PASS | `.venv/bin/ruff check src tests tools` -> `All checks passed!` |
| Ruff format | PASS | PASS | `.venv/bin/ruff format --check src tests tools` -> `143 files already formatted` |
| M8 scoped mypy | PASS | PASS | CI-scoped mypy command -> `Success: no issues found in 23 source files` |
| Package build | PASS | PASS | `.venv/bin/python -m build` -> `Successfully built astra_indexator-0.1.0.tar.gz and astra_indexator-0.1.0-py3-none-any.whl` |
| Dependency consistency | PASS | PASS | `.venv/bin/python -m pip check` -> `No broken requirements found.` |
| Full pytest | PASS | PASS | `.venv/bin/pytest` -> `418 passed, 31 warnings in 34.80s` |
| PostgreSQL integration | PASS | PASS | Full pytest executed Testcontainers PostgreSQL modules and passed. |
| Clean Alembic bootstrap | PASS | PASS | Fresh `postgres:16-alpine` on port 55435 upgraded from base to head. |
| Migration 0007 | PASS | PASS | `public.alembic_version.version_num = 0007_m8_delivery_compatibility`; column and check constraint exist. |
| Canonical startup | PASS | PASS | `python -m astra_indexator` with `ASTRA_INDEXATOR_DATABASE_URL` logged DB validation and runtime startup. |
| Worker polling | PASS | PASS | Runtime smoke logged `worker loop started`; empty queue did not crash. |
| Negative startup | PASS | PASS | Unit tests cover missing DSN, malformed DSN, invalid worker/lease config, unavailable DB, stale Alembic head and invalid AstraVector endpoint; CLI missing DSN returns exit code 2 with diagnostic. |
| SIGTERM shutdown | PASS | PASS | SIGTERM to smoke PID logged `AstraIndexator runtime shutdown complete` and exited code 0. |
| AccessZone contract scan | PASS | PASS | Remaining UUID terms are downstream/public-wire evidence, historical docs, or tests proving UUID producer rejection; no CR-06 producer/domain UUID path added. |
| Regression/coverage review | PASS | PASS | Test count increased from CR-06 baseline 416 to 418 due to negative startup coverage; B1/M8 test files remain present. |

Full pytest summary:

```text
collected: 418
passed: 418
failed: 0
skipped: 0
duration: 34.80s
```

Clean database evidence:

```text
version_num = 0007_m8_delivery_compatibility
astra_indexator tables = delivery_batch, delivery_checkpoint, indexation_job, job_event, knowledge_inventory, prepared_artifact_checkpoint, processing_attempt
delivery_checkpoint.delivery_compatibility_sha256 = character varying
constraint = ck_delivery_checkpoint_delivery_compatibility_sha256_format
```

Runtime smoke command:

```bash
ASTRA_INDEXATOR_DATABASE_URL=postgresql+psycopg://astra_indexator:astra_indexator@localhost:55435/astra_indexator \
ASTRA_INDEXATOR_WORKER_ID=codex03-smoke \
ASTRA_INDEXATOR_POLL_INTERVAL_SECONDS=0.2 \
ASTRA_INDEXATOR_LOG_LEVEL=INFO \
.venv/bin/python -m astra_indexator
```

Runtime smoke logs:

```text
database validation succeeded
AstraIndexator runtime starting
worker loop started
AstraIndexator runtime shutdown complete
```

Dependency consistency evidence:

```text
.venv/bin/python -m pip check
No broken requirements found.
```

Dependency alignment review:

- CI quality job installs `.[dev]`; `dev` includes `astra-indexator[test]`, `build`, `mypy`, `ruff` and `types-grpcio`, so quality gates, package build, generated-client tooling and tests share one supported dependency graph.
- CI test job installs `.[test]`; test dependencies include `pytest`, `testcontainers[postgres]`, `reportlab`, `pypdfium2` and `grpcio-tools`.
- Runtime dependencies needed by CR-06 are already production dependencies: SQLAlchemy, Alembic, psycopg, pydantic settings, grpcio and protobuf.
- Generated AstraVector client files are not source-controlled dependencies. They are reproducibly generated by the documented CI step before checks/tests that import the generated modules.
- No direct dependency on the portable AstraVector deployment repo was added to this B1 branch; that repo remains a B2/real-service qualification input, not a Python package dependency.

Negative startup CLI evidence:

```text
.venv/bin/python -m astra_indexator
exit code: 2
ERROR astra_indexator.runtime AstraIndexator startup failed: ASTRA_INDEXATOR_DATABASE_URL is required
```

## AccessZone contract scan

Production source occurrences are classified as acceptable:

- `src/astra_indexator/domain/delivery_intent.py`: rejects producer `accessZoneId`/`accessZoneIds`; this enforces the code-only contract.
- `src/astra_indexator/application/astravector_delivery_coordinator.py`: uses `access_zone_id=None` on Start and persists/uses `resolved_access_zone_id` only as downstream recovery/status evidence.
- `src/astra_indexator/application/finalize_reconciliation.py`: treats `access_zone_id` as optional downstream `DocumentRef` evidence and fails closed when it cannot be recovered.
- `src/astra_indexator/application/vector_readiness.py`: uses downstream `DocumentRef.access_zone_id` for public AstraVector status checks after AstraVector has returned/resolved it.
- `src/astra_indexator/persistence/models.py`: `DeliveryCheckpoint.resolved_access_zone_id` is the explicitly permitted private downstream evidence field.
- `src/astra_indexator/astravector/*`: generated/public wire mapping and adapter code; not producer/domain identity.

Documentation occurrences in historical TZ/M8 docs are either explicitly superseded by `ACCESS-ZONE-CODE-CONTRACT-FREEZE.md` and `M8-COMPLETION-REMEDIATION-SPEC.md`, or describe downstream AstraVector public wire requirements. Tests include both rejection of producer UUID selectors and allowed downstream UUID evidence.

## Generated AstraVector client policy

`src/astra_indexator/astravector/generated/` is treated as reproducibly generated build output. CI generates it with:

```bash
python tools/generate_astravector_proto.py
```

The directory is ignored by `.gitignore` and was not committed. A clean clone still has a supported preparation path because both CI jobs explicitly run the generator before gates/tests that need the generated protobuf client.

## Defects

No P0/P1 defects remain for local B1 integration qualification.

No new P2/P3 defects were found during CODEX-03.

## Git hygiene

Inspected before final commit:

```text
git status --short --branch
```

Excluded from commit:

```text
.venv
__pycache__
.pytest_cache
.mypy_cache
.ruff_cache
dist
build
src/astra_indexator/astravector/generated/
temporary PostgreSQL containers
```

The historical CODEX-01 report was left untracked because it contains machine-local verification paths and is not required for this integration deliverable.

## Final verdict

PASS

Boundary: CODEX-03 PASS means B1 local integration qualification only. It does not mean formal B1 PASS, M8 QUALIFIED or AstraIndexator 1.0 QUALIFIED. The next required steps remain push B1 branch, GitHub CI, review, merge, post-merge main CI, then B2 real AstraVector qualification.
