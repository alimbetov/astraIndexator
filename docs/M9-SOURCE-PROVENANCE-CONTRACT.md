# M9 Source Provenance Contract Freeze

## Status

**FROZEN CONTRACT — mandatory for M9 and later API/search work.**

AstraIndexator keeps user-visible source identity and internal storage identity as separate values for the complete document lifecycle.

## Canonical fields

```text
source_file_name      # original/public filename uploaded by the user
storage_object_id     # internal immutable UUID identity of the stored object
storage_object_name   # internal storage name, normally UUID plus optional extension
source_uri            # current physical/logical object location
source_content_hash   # immutable content evidence when known
source_size_bytes     # source size evidence when known
```

Example:

```text
source_file_name    = "Технический регламент 2026.pdf"
storage_object_id   = 71984e42-c89e-4c42-9370-f8a2d27d89e5
storage_object_name = "71984e42-c89e-4c42-9370-f8a2d27d89e5.pdf"
source_uri          = "seaweed://documents/71984e42-c89e-4c42-9370-f8a2d27d89e5.pdf"
```

## Invariants

1. `source_file_name` is producer/user-visible provenance. It MUST NOT be replaced by an internal UUID filename after upload.
2. `storage_object_id` is infrastructure identity. It is not a display filename.
3. `storage_object_name` may be derived from the storage object's UUID and extension, but it MUST NOT become the public source name.
4. `source_uri` is location, not identity. Moving the object may change `source_uri` without changing `storage_object_id` or the original `source_file_name`.
5. If both `storage_object_id` and `storage_object_name` are supplied at job creation, both are persisted. New application commands MUST reject a partial storage identity where only one of the pair is supplied.
6. Legacy rows may have the storage identity fields NULL. M9 projection must preserve NULL rather than inventing values from `source_uri`.
7. Knowledge Inventory MUST expose both public provenance and internal storage identity.
8. Search/RAG/UI provenance displays `source_file_name` to the user. Internal UUID names are operational metadata only.
9. AstraVector `StartLogicalDocumentIngestion.file_name` receives the public `source_file_name`; `source_uri` remains a separate wire field. The internal storage UUID/name MUST NOT replace `file_name`.
10. Reindex creates a new immutable document version and may point at a new storage object while preserving the exact public filename supplied for that version.

## Required durable path

```text
upload boundary
   ├── source_file_name (public)
   ├── storage_object_id (UUID)
   ├── storage_object_name (internal)
   └── source_uri
          ↓
IndexationJob
          ↓
processing / restart / replay
          ↓
M8 Start
   ├── file_name  = source_file_name
   └── source_uri = source_uri
          ↓
M9 Knowledge Inventory
   ├── source_file_name
   ├── storage_object_id
   ├── storage_object_name
   └── source_uri
          ↓
search / UI provenance
   └── display source_file_name
```

## Anti-regression qualification

Tests MUST prove that:

- a Unicode/original filename survives PostgreSQL round-trip;
- a UUID-based storage name survives independently;
- the two values may differ and remain different;
- Knowledge Inventory rebuild preserves both values;
- UUID-only AccessZone and code-only AccessZone paths do not alter provenance;
- M8 Start uses the original/public filename, never `storage_object_name`.
