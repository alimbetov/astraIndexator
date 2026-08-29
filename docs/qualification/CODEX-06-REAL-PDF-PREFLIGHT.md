# CODEX-06 Real PDF Pipeline Smoke & B2 Preflight Qualification

Date: 2026-08-29

Branch: `codex/real-pdf-smoke`

Baseline `main` SHA: `2157e4ae8bea2fc787e58949952e0c238aa76984`

B1 qualified implementation SHA: `f43ff28a93dc13c3fb6202c09046807142b4b1d7`

Final verdict: **PASS**

B2_PREFLIGHT_READY: **YES**

This qualification used the external local PDF at
`/Users/ruslanalimbetov/Documents/AstraIndexator/data/mycv.pdf`. The file content was treated as
privacy-sensitive: no extracted text, screenshots, copied PDF, binary artifact, or parsed payload was
written to the repository or included in this report.

## Boundary

This is a B2 preflight only. It does not claim AstraVector B2 success and does not fabricate
downstream `Start`, `Append`, `Finalize`, resolved AccessZone UUID, final downstream ACK, or
`SEARCHABLE` evidence.

The highest verified state is local delivery preparation: M1-M7 real pipeline execution, M8 logical
mapping and deterministic batch planning, local delivery compatibility checkpoint preparation, and
fencing checks before downstream mutation.

## Source Evidence

| Check | Result |
| --- | --- |
| PDF exists outside repository | PASS |
| MIME/type | `application/pdf` |
| Size | `254055` bytes |
| SHA-256 | `b40bccabf7bb4058f97b5536e7396ecd9d15801c847cf980af4c31d9e79acb4f` |
| Same acquisition implementation as B1 | PASS |
| Repeat acquisition SHA match | PASS |
| Repository data/PDF scan | PASS; no copied PDF committed |

## Dependency And Packaging Gates

| Gate | Result |
| --- | --- |
| Isolated environment | PASS; `.venv-codex06`, Python 3.12.7 |
| `pip check` | PASS; no broken requirements |
| Ruff lint | PASS; `ruff check src tests tools` |
| Ruff format | PASS; `ruff format --check src tests tools` |
| Exact CI M8 scoped mypy | PASS; `Success: no issues found in 23 source files` |
| Generated AstraVector client | PASS; generated from Git blob `ed1eab5f56dfb73cc48927ad2effb759a2c4e01e` |
| Package build | PASS from clean `git archive HEAD` source |
| Package hygiene | PASS; clean sdist/wheel contain no `.venv`, `mycv`, `CODEX-01`, `data/`, or `astra_indexator/astravector/generated/` entries |
| Focused pytest | PASS; `39 passed, 12 warnings in 9.89s` |

Note: a direct local build from the dirty working tree would include the unrelated untracked
`docs/qualification/CODEX-01-BOOTSTRAP-RUNTIME-VERIFICATION.md` in the sdist. The qualification
therefore repeated package build from clean tracked `HEAD` source to verify release hygiene without
that untracked file.

## Database And Migration

| Check | Result |
| --- | --- |
| Fresh PostgreSQL container | PASS; `postgres:16-alpine` |
| Alembic base -> head | PASS |
| Head revision | `0007_m8_delivery_compatibility` |
| Delivery compatibility column | PASS; `delivery_checkpoint.delivery_compatibility_sha256` persisted |

## Real PDF Pipeline

| Stage | Evidence |
| --- | --- |
| Acquisition | PASS; `PDF`, `application/pdf`, no warnings |
| Parser route | PASS; `pdfplumber-layout`, `m4-v1`, `default-v1`, `reading-order-v1` |
| PDF structure | PASS; 5 pages; page modes `MIXED`, `NATIVE_TEXT`, `NATIVE_TEXT`, `NATIVE_TEXT`, `NATIVE_TEXT` |
| OCR routing | PASS; `OCR_USED=false`; native text-layer route, OCR candidates treated as routing evidence only |
| Parser quality | PASS; `GOOD`, 178 elements, 8835 native text chars, 2 OCR candidates, no warnings |
| Normalization | PASS; 178 normalized elements, 8835 chars before/after |
| Logical split | PASS; 8 fragments; deterministic repeat fingerprint matched |
| M7 assembler/publisher | PASS; real assembler and publisher used |
| M7 persistence | PASS; `prepared_artifact_checkpoint` installed in PostgreSQL |
| Restart/replay | PASS; checkpoint replay decision `REPLAY` without rerunning parse/normalize/split |
| Compatibility fence | PASS; controlled mismatch returned `REPROCESS` and no artifact |

Prepared artifact evidence:

| Field | Value |
| --- | --- |
| Artifact ID | `d2aece79e0669e4b17c3eee7413a58f465032973f3cdc24eaec379971ebc2f5c` |
| Manifest SHA-256 | `b1f713d274f7990d967393a87bed091f97f0c70803825a8a9aafa1e9c6ada747` |
| Compatibility SHA-256 | `c1afa4fb73262735087c855c5258c1e6076f01cf6d2c327319dd81320c172afe` |
| Part count | 2 |
| Element records | 178 |
| Fragment records | 8 |

## M8 Preflight

| Check | Result |
| --- | --- |
| Delivery compatibility fingerprint | PASS; `m8-delivery-compatibility-v1` |
| `delivery_compatibility_sha256` | `14cf6378b7a7dbe45d068a9a098c6c482a000995a1e5a5ba021213827ce68190` |
| PreparedArtifactDeliveryMapper | PASS; 9 logical blocks |
| DeterministicBatchPlanner | PASS; 1 batch, 9 blocks |
| Batch hash repeatability | PASS; `8512aad939eaead817e95afd2b41c657f47d382d3b1a665cbd3f36a1e4d5561d` |
| Final content hash | `299da83c9261dceebd0ea50db33e2e2b18903c0bb4135c130a3b2038821fffd2` |
| Stable Start identity | PASS; stable for same document/version/source SHA |
| Start identity sensitivity | PASS; changes on document version and source SHA changes |
| AccessZone producer identity | PASS; `access_zone_code=0000`, not UUID |
| TTL preflight | PASS; `0` and `30` accepted, `-1` rejected before downstream mutation |
| Delivery checkpoint preflight | PASS; compatibility persisted without session binding or batch ACK |
| Delivery batch rows | `0`; no downstream Start/Append ACK was fabricated |

## Fencing

| Check | Result |
| --- | --- |
| Stale worker complete after lease reclaim | PASS; `LeaseLostError` |
| Stale worker stage mutation after lease reclaim | PASS; `LeaseLostError` |

## PostgreSQL Audit

| Table | Evidence |
| --- | --- |
| `indexation_job` | job remained `PROCESSING` for local preflight; no fake downstream completion |
| `prepared_artifact_checkpoint` | row exists |
| `delivery_checkpoint` | row exists with compatibility SHA |
| `delivery_checkpoint.ingestion_session_id` | `NULL` |
| `delivery_checkpoint.searchable` | `NULL` |
| `delivery_checkpoint.resolved_access_zone_id` | `NULL` |
| `delivery_batch` | 0 rows |

## Runtime Timings

| Stage | Seconds |
| --- | ---: |
| Acquisition | 0.000964 |
| Parse | 0.258156 |
| Normalize | 0.002042 |
| Split | 0.029070 |
| Publish | 0.033566 |

## Final Decision

CODEX-06 is **PASS** for real local PDF pipeline smoke and B2 preflight qualification.

B2_PREFLIGHT_READY is **YES**: the project can proceed to a real AstraVector runtime qualification,
where downstream Start/Append/Finalize/readiness evidence must be produced by AstraVector itself.
