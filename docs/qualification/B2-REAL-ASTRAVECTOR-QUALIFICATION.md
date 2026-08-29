# B2 Real AstraVector Qualification

Date: 2026-08-29

Final verdict: **BLOCKED**

Reason: the validated AstraVector image accepts real Start and Append from AstraIndexator, but
`FinalizeLogicalDocumentIngestion` cannot complete through the public session API because the runtime
internally hardcodes `AUTO_WHEN_READY`, while the same validated v007 contract explicitly rejects
`AUTO_WHEN_READY`.

No private AstraVector PostgreSQL or Qdrant state was used as qualification evidence. The real CV/PDF
content was not committed, copied into the repository, or included in this report.

## Baseline

| Item | Value |
| --- | --- |
| ASTRAINDEXATOR_BRANCH | `codex/real-pdf-smoke` |
| ASTRAINDEXATOR_SHA | `a441e22ac2f3366734e1380997c50e6afc704690` |
| ASTRADEPLOYMENT_SHA | `cc7dd29256065be1734223fc112813e445109bd0` |
| LLM2_REFERENCE_SHA | `2142e2bd328c8964dbb3c2eff52caf8a350c5ddf` |
| ASTRAVECTOR_IMAGE | `registry.astrabase.asia/astravector:sha-1cb6065` |
| ASTRAVECTOR_DIGEST | `sha256:b0567810b5ea3df752ff8ba559fcf16bc46b245878e798b8888dcf93426ee6ad` |
| PROTO_REVISION | `astravector-v007-fix4.5.2`; fixture upstream proto blob `ed1eab5f56dfb73cc48927ad2effb759a2c4e01e` |
| LLM2_PROTO_SHA256 | `d635911e10ebcf3e3572918eaf0636095c4c62630266ae120075d6fddb32f935` |
| HOST_ARCH | `arm64`; Docker engine `aarch64` |
| PostgreSQL version | AstraDeployment `pgvector/pgvector:pg16`; AstraIndexator qualification DB `postgres:16-alpine` |
| Qdrant version | `qdrant/qdrant:v1.14.1` |
| PDF_SHA256 | `b40bccabf7bb4058f97b5536e7396ecd9d15801c847cf980af4c31d9e79acb4f` |

Historical note: `docs/qualification/B2-REAL-ASTRAVECTOR-RESULT.md` remains the earlier
precondition-blocked attempt. This report records the later real runtime attempt.

## Environment

| Gate | Result |
| --- | --- |
| B1 | PASS |
| CODEX-06 | PASS; `B2_PREFLIGHT_READY=YES` |
| Environment evidence synchronized | PASS |
| Portable preflight/start/health/smoke | PASS |
| Image identity | PASS |
| Runtime endpoints | PASS; public gRPC `127.0.0.1:50051`, HTTP `127.0.0.1:8080`, metrics `127.0.0.1:9090` |
| Database boundary | PASS; AstraIndexator used a separate PostgreSQL on host port `55446` |
| No private storage coupling | PASS |
| Post-attempt health | PASS |
| Image digest unchanged | PASS |

## Contract Gates

| Gate | Result | Evidence |
| --- | --- | --- |
| Proto/runtime compatibility | PASS | Runtime reflection exposes Start, Append, Finalize, GetLogicalDocumentIngestionStatus, GetDocumentVectorStatus, Abort. |
| Hash canonicalization | PASS after AstraIndexator fix | Real runtime exposed a root-block `source_location` round-trip mismatch; AstraIndexator now sends an explicit default `SourceLocation` on the synthetic root. Focused tests pass. |
| AccessZone code-only | PASS | Principal path used `access_zone_code=0000`; no producer UUID was supplied. |
| TTL semantics | PARTIAL | Principal path used `ttl_days=0`; negative TTL is rejected by AstraIndexator. Positive TTL was not reached after Finalize blocker. |

## Real B2 Principal Attempt

Input document:

| Field | Value |
| --- | --- |
| Source | external local PDF, not committed |
| PDF pages | 5 |
| Parser | `pdfplumber-layout` |
| Elements/fragments | 178 elements, 8 fragments |
| Logical blocks | 9 |
| AccessZone | `0000` |
| TTL | `0` |

Observed principal path:

| Step | Result |
| --- | --- |
| M7 prepared artifact replay | PASS |
| Real Start | PASS |
| Start checkpoint | PASS |
| Real Append | PASS |
| Append checkpoint | PASS; batch `0` accepted, 9 blocks |
| Real Finalize | BLOCKED |
| No premature completion | PASS; local job stayed `PROCESSING`, stage `ASTRAVECTOR_FINALIZE` |
| Session status/readiness/searchable | BLOCKED by Finalize |
| Real retrieval | NOT RUN; searchability was not established |
| Local completion ordering | PASS; AstraIndexator did not complete without searchability |
| Full real E2E | BLOCKED |

Principal blocking response:

```text
AstraVector gRPC INVALID_ARGUMENT:
UNSUPPORTED_ACTIVATION_POLICY_AUTO_WHEN_READY:
AUTO_WHEN_READY requires lifecycle auto-activation worker and is disabled in v007 fix1
```

Public contract evidence:

| Evidence | Result |
| --- | --- |
| Runtime reflection for `StartLogicalDocumentIngestionRequest` | No `activation_policy` field exists. |
| llm2 `FinalizeLogicalDocumentIngestion` implementation | Builds internal `VectorIndexingOptions` with `ActivationPolicy::AutoWhenReady`. |
| llm2 v007 fix1 documentation | States `AUTO_WHEN_READY` is explicitly rejected until lifecycle auto-activation is implemented. |

Therefore AstraIndexator cannot legally switch session Finalize to `MANUAL` through the public
session-ingestion API. Using `IndexLogicalDocument` manually or querying private storage would not
satisfy CODEX-07B's required AstraIndexator Start -> Append -> Finalize path.

## Remediation Applied In AstraIndexator

During the real B2 attempt, the first failure was:

```text
DATA_LOSS: INGESTION_STAGING_CORRUPTED_BATCH_HASH_MISMATCH
```

Root cause: AstraIndexator's synthetic root block omitted `source_location`. The validated runtime's
Append path accepted the batch hash, but Finalize reconstructed stored `{}` source-location JSON as
a default protobuf `SourceLocation`, changing the recomputed batch hash.

Fix: `PreparedArtifactDeliveryMapper` now sends an explicit default `SourceLocation` on the
synthetic root block. This preserves stable protobuf -> JSON -> protobuf hash behavior on the
validated runtime.

Validation:

```text
24 passed in 1.01s
Success: no issues found in 4 source files
```

## Final Matrix

| Gate | Result |
|---|---|
| B1 | PASS |
| CODEX-06 | PASS |
| Environment evidence synchronized | PASS |
| Portable health | PASS |
| Image identity | PASS |
| Database boundary | PASS |
| Proto/runtime compatibility | PASS |
| Hash canonicalization | PASS |
| Real Start | PASS |
| Start checkpoint | PASS |
| Start idempotency | PARTIAL; retry returned same stuck session after first failed attempt |
| Real Append | PASS |
| Append replay | NOT RUN after Finalize blocker |
| Append conflict protection | NOT RUN after Finalize blocker |
| Real Finalize | BLOCKED |
| No premature completion | PASS |
| Session status | BLOCKED |
| SEARCHABLE | BLOCKED |
| Real retrieval | BLOCKED |
| Local completion ordering | PASS |
| Full real E2E | BLOCKED |
| Restart after Start | NOT RUN after Finalize blocker |
| Restart after Append | NOT RUN after Finalize blocker |
| Restart after Finalize | NOT RUN after Finalize blocker |
| Ambiguous Finalize recovery | BLOCKED |
| Public DocumentRef recovery | BLOCKED |
| AccessZone code-only | PASS |
| TTL semantics | PARTIAL |
| Real fencing | NOT RUN after Finalize blocker |
| Failure routing | PARTIAL; ownership/hash paths exercised, Finalize contract blocks completion |
| No private storage coupling | PASS |
| AstraVector restart | NOT RUN; no searchable document existed |
| Post-restart retrieval | BLOCKED |
| Post-B2 native smoke | PASS; portable runtime smoke remained healthy after blocked B2 attempt |
| Image digest unchanged | PASS |

## Defects

| ID | Owner | Severity | Description |
| --- | --- | --- | --- |
| B2-BLOCKER-01 | AstraVector / llm2 public session API | P0 | `FinalizeLogicalDocumentIngestion` hardcodes `AUTO_WHEN_READY`, while v007 fix1 rejects `AUTO_WHEN_READY`; public Start has no activation-policy field, so AstraIndexator cannot select `MANUAL`. |
| B2-FIXED-01 | AstraIndexator | P1 fixed | Synthetic root omitted `source_location`, exposing an AstraVector protobuf/JSON/protobuf hash round-trip mismatch at Finalize. Fixed by emitting explicit default `SourceLocation`. |

## Final Verdict

B2 is **BLOCKED**.

AstraIndexator reached real Start and real Append against the validated AstraVector OCI runtime, but
the public session Finalize contract is not currently satisfiable by a code-only AstraIndexator
producer. The next required work is an AstraVector/llm2 contract remediation: either session
ingestion must allow a supported activation policy such as `MANUAL`, or `AUTO_WHEN_READY` must become
implemented and supported by the validated image.
