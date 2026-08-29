# CODEX-10 AstraIndexator Real AstraVector Debugging & B2 Continuation Result

Date: 2026-08-29

## Verdict

CODEX-10 PASS.

B2 status: PASS for the real AstraIndexator -> AstraVector pipeline exercised here. The remaining caveat is retrieval visibility: the real PDF is retrievable through the public HTTP endpoint with `callerAccessLevel=INTERNAL`; `PUBLIC` requests did not return the target document.

## Baselines

- AstraIndexator branch: `codex/real-pdf-smoke`
- AstraIndexator baseline SHA: `bcc589e7430e1756695fdc8f7d8beff26528c9a5`
- AstraDeployment portable SHA: `cc7dd29256065be1734223fc112813e445109bd0`
- AstraVector image: `registry.astrabase.asia/astravector:sha-f6493fa`
- AstraVector image digest: `sha256:2957a8887443e53914ca07816ddbaab385e02b96a81b7a08b4a1697f94f0ac40`
- AstraVector source SHA: `f6493fa86d8c7c80678989ffcb8858b5f5b684dd`

## Environment

- Runtime host: MacBook local Docker, `linux/arm64`
- Public gRPC endpoint: `127.0.0.1:50051`
- Public HTTP retrieve endpoint: `127.0.0.1:8080/api/v1/retrieve`
- Metrics endpoint: `127.0.0.1:9090`
- Qdrant endpoint exposed by portable runtime: `127.0.0.1:6333`
- AstraIndexator clean migration database: local PostgreSQL container `astra-codex10-indexator-pg`, host port `55447`, database `astra_indexator_codex10`
- Portable `.env` was adjusted locally to use the new AstraVector image and expected digest. It is intentionally ignored and not committed.
- Preserved portable Postgres volume initially had a local password mismatch for `astravector_app`; the local runtime password was synchronized in the container. No credential value is recorded here.

## Real PDF Input

- PDF path: `/Users/ruslanalimbetov/Documents/AstraIndexator/data/mycv.pdf`
- PDF SHA256: `b40bccabf7bb4058f97b5536e7396ecd9d15801c847cf980af4c31d9e79acb4f`
- Size: `254055` bytes
- Parser: `pdfplumber-layout`
- Pages: `5`
- Page modes: `MIXED`, `NATIVE_TEXT`, `NATIVE_TEXT`, `NATIVE_TEXT`, `NATIVE_TEXT`
- Extracted elements: `178`
- Fragments: `8`
- Logical blocks delivered: `9`

## What Was Found

The old suspected blocker did not reproduce on the new image. Real `StartLogicalDocumentIngestion`, `AppendLogicalDocumentBlocks`, and `FinalizeLogicalDocumentIngestion` completed far enough for AstraVector to synchronize all vector bindings.

The actual blocker was in AstraIndexator: after `Finalize`, the new AstraVector runtime reports a synchronized document as `OPERATION_STATE_READY_TO_ACTIVATE`. AstraIndexator had no public activation step and also treated `READY_TO_ACTIVATE + searchable=true` as an integrity error. That left real B2 stuck after vectors were ready.

## Fixes

- Added `ActivateDocumentVersionCommand` and `ActivateDocumentVersionResult` to the AstraVector contract port.
- Added `AstraVectorGrpcAdapter.activate_document_version(...)` using public `AstraVectorV004Control/ActivateDocumentVersion`.
- Added proto mapping for `ActivateDocumentVersionRequest`.
- Added `DocumentVectorStatus.document_status` and mapped public `sync.documentStatus`.
- Added `VectorActivationRunner` with lease fencing before activation and while polling status.
- Added ambiguous activation reconciliation: if activation transport result is uncertain, AstraIndexator reconciles with public `GetDocumentVectorStatus`.
- Updated readiness policy so `READY_TO_ACTIVATE + searchable=true + sync.documentStatus=ACTIVE` is accepted as searchable completion.
- Wired coordinator flow to advance through `ASTRAVECTOR_ACTIVATE` and complete only after activation/searchable evidence.
- Added activation tests for normal activation, ambiguous transport reconciliation, and lease loss before activation.
- Updated existing readiness/coordinator/fencing tests for the new activation-aware flow.

## Real B2 Evidence

Principal real PDF run: `codex10-principal-005`

- Result: PASS
- Document ID: `76722fe0-f954-5eef-9727-4a21ba8c0dcf`
- Document version: `1`
- Access zone code: `0000`
- Resolved access zone ID: `34095d27-bb9c-522b-8e5e-17565961916b`
- Ingestion session ID: `e14bbe54-ac80-4de6-af50-341433314053`
- Batch count: `1`
- Batch dispositions: `SEND`
- Job status: `COMPLETED`
- Final processing stage: `ASTRAVECTOR_ACTIVATE`
- Prepared checkpoint: present
- Delivery checkpoint: present
- Stored source hash matched the real PDF SHA256.

Public ingestion session status:

- Status: `COMPLETED`
- Received batches: `1`
- Received blocks: `9`
- Received bytes: `30001`

Public document vector status:

- Top-level state: `OPERATION_STATE_READY_TO_ACTIVATE`
- Progress: `100`
- Searchable: `true`
- Ready to activate: `true`
- `sync.documentStatus`: `ACTIVE`
- Expected bindings: `45`
- Synced bindings: `45`
- Dense vectors expected/found: `45/45`
- Sparse vectors expected/found: `45/45`
- Qdrant points expected/found: `45/45`
- Outbox completed: `45`

Public HTTP retrieval after restart:

- Endpoint: `POST /api/v1/retrieve`
- Access zone ID/code: `34095d27-bb9c-522b-8e5e-17565961916b` / `0000`
- Caller access level: `INTERNAL`
- HTTP status: `200`
- Returned contexts: `3`
- Target document contexts: `1`
- Target document first rank: `1`
- Evidence status: `DEGRADED`
- Degradation codes: `VISIBILITY_REJECTED`

The retrieval response was sanitized for reporting; no PDF text is stored in this report.

## Restart And Native Runtime Smoke

After the real PDF run and code fixes:

- `make stop`: PASS, containers stopped and persistent volumes preserved.
- `make start`: PASS, image digest matched `sha256:2957a8887443e53914ca07816ddbaab385e02b96a81b7a08b4a1697f94f0ac40`.
- `make health`: PASS.
- `make smoke`: PASS, including native `IndexLogicalDocument`, `ActivateDocumentVersion`, and HTTP retrieval evidence.
- Post-restart status for the CODEX-10 PDF still reported `sync.documentStatus=ACTIVE`, `searchable=true`, and `45/45` points found.
- Post-restart HTTP retrieval returned the CODEX-10 PDF at rank 1 with `callerAccessLevel=INTERNAL`.

## Additional Matrix

- Clean Alembic bootstrap: PASS, `0007_m8_delivery_compatibility (head)`.
- Native portable smoke on new image: PASS.
- Real Start: PASS.
- Real Append: PASS.
- SourceLocation/source hash preservation: PASS.
- Real Finalize: PASS.
- Old blocker reproduced: NO.
- Session state after Finalize: `COMPLETED`.
- READY_TO_ACTIVATE handling: PASS after activation-aware fix.
- Activation support before fix: absent.
- `ActivateDocumentVersion`: PASS.
- ACTIVE/searchability evidence: PASS via public `sync.documentStatus=ACTIVE` and `searchable=true`.
- Local completion semantics: PASS.
- Restart/resume evidence: PASS.
- Ambiguous Finalize reconciliation: covered by existing focused tests.
- Ambiguous activation reconciliation: PASS in new focused test.
- Ownership/lease loss before activation: PASS in new focused test.
- TTL positive probe on new runtime: PASS.
- Access zone code `0000`: PASS for real PDF.
- Access zone code `0488`: PASS in native portable smoke.
- Failure routing and transport contract: PASS in full test suite.

## Quality Gates

- `ruff check src tests tools`: PASS.
- `ruff format --check src tests tools`: PASS, `145 files already formatted`.
- Scoped `mypy`: PASS.
- `python -m build`: PASS.
- Focused activation/readiness/coordinator tests: PASS, `47 passed`.
- Full `pytest`: PASS, `424 passed, 31 warnings`.

## Notes

- Do not commit portable `.env`, PDF inputs, extracted text, OCR/CV text, generated protobuf artifacts, database dumps, smoke outputs, credentials, or model files.
- `docs/qualification/CODEX-01-BOOTSTRAP-RUNTIME-VERIFICATION.md` remains intentionally untracked and is not part of CODEX-10.
- Public `PUBLIC` retrieval did not return the real PDF. The successful retrieval evidence uses `INTERNAL`, matching the observed visibility of the indexed document.
