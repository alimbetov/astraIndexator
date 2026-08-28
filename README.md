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
