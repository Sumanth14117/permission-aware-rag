# Architecture

This is a concise root-level map of the implemented system. The more detailed security and data-flow notes remain in [docs/architecture.md](docs/architecture.md).

## Runtime components

- `frontend/`: React + Vite single-page workspace. In development, Vite proxies API paths to FastAPI.
- `backend/app/`: FastAPI routes and services for authentication, documents, ingestion, retrieval, answering, configuration, and audit events.
- PostgreSQL: authoritative users, organizations, memberships, sessions, documents, permissions, chunks, and audit records.
- Qdrant: vector index with organization/document identity metadata; it is not an authorization source.
- `data/uploads/`: local organization-scoped uploaded files.
- Sentence Transformers: local embedding provider; an optional OpenAI-compatible HTTP provider generates answers. Mock answering is the configured development default.

## Request and data flows

Authentication establishes a session cookie. CSRF bootstrap issues a double-submit token, and unsafe HTTP methods require the allowed origin and matching token. The session resolves the user; PostgreSQL supplies the current single organization membership and role.

Document upload creates a server-scoped pending record and file, then runs synchronous ingestion: text/PDF extraction → deterministic overlapping chunks → local embeddings → Qdrant vector upsert and PostgreSQL chunk persistence → ready status. Failure marks the document failed and attempts cleanup of partial index writes.

Question answering asks PostgreSQL for the caller's readable ready document IDs, embeds the question, and searches Qdrant with organization and allowed-document filters. Candidate identities are validated and each chunk is re-authorized against current PostgreSQL membership, document state, and grants before chunk text reaches the answering provider. The response contains the answer and document/chunk citations.

Audit events are best-effort inserts in an independent transaction. They store bounded event metadata and QA question length/citation count, not question or answer text.

## Key invariants

- PostgreSQL is the permission and chunk-text authority; vector payloads are untrusted locators.
- Every retrieval is tenant-filtered and allowlist-filtered, then checked again before exposing text.
- User-supplied filenames are display metadata, not storage paths. Upload paths are server generated.
- One organization per user, one shared Qdrant collection, local disk, and synchronous ingestion are current MVP constraints.

For endpoint details, schema notes, and the full retrieval security contract, see [docs/architecture.md](docs/architecture.md) and the setup/API notes in [README.md](README.md).
