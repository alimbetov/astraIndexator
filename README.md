# AstraIndexator

AstraIndexator is an internal document indexing service responsible for acquiring immutable source documents, parsing and OCR, multilingual normalization and logical segmentation, deterministic `LogicalBlock[]` preparation, and delivery to AstraVector for tokenizer-aware vectorization and retrieval.

## Current phase

The TZ-00..TZ-18 architecture/specification baseline is complete and implementation is underway.

Current implementation status:

```text
M1 Persistence Foundation   ✅ merged
M2 Job Coordinator          ✅ merged
M3 SeaweedFS & Acquisition  next
```

The implementation roadmap is maintained in [`docs/IMPLEMENTATION-ROADMAP-1.0.md`](docs/IMPLEMENTATION-ROADMAP-1.0.md).

## Documentation

Canonical architecture and subsystem technical specifications are maintained under [`docs/`](docs/). Implementation changes must preserve those contracts or update the relevant TZ in the same reviewed change.
