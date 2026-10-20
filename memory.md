# Project memory

## Project identity

Permission-Aware Multi-Tenant RAG Platform is a student portfolio MVP. Its core promise is that answers use only document passages the authenticated organization member may read, with citations.

## Decisions currently reflected in code/docs

- A user belongs to one organization in the MVP; membership role is owner or member.
- Documents are organization-visible or restricted to named readers; owners can read restricted documents.
- Sessions are database-backed and carried in HttpOnly cookies; unsafe routes use same-origin CSRF checks.
- PostgreSQL is authoritative for membership, grants, document readiness, and chunk text.
- One Qdrant collection is searched with organization and allowed-document filters; candidates are independently checked against PostgreSQL.
- Uploads live on local disk. Ingestion is synchronous and supports UTF-8 text/PDF. Embeddings are generated locally; mock LLM is the default and OpenAI-compatible generation is optional.
- Audit logging is best-effort and excludes sensitive content.

## Current implementation map

- API entry and middleware: `backend/app/main.py`
- Authentication and registration: `backend/app/auth.py`, `backend/app/authentication.py`, `backend/app/registration.py`
- Document API and tenant context: `backend/app/documents.py`
- Ingestion/indexing: `backend/app/ingestion_service.py`, `backend/app/ingestion.py`, `backend/app/indexing.py`
- Permission-aware retrieval and answering: `backend/app/retrieval.py`, `backend/app/answering.py`, `backend/app/qa.py`
- Persistence models and migrations: `backend/app/models/`, `backend/alembic/`
- Frontend workspace: `frontend/src/`
- Existing detailed architecture: `docs/architecture.md`

## Known boundaries and gaps

- Optional M8 work names Postgres RLS and asynchronous ingestion; neither is described as implemented.
- Restricted-reader management exists as an API capability, but the reviewed UI does not expose it.
- The README documents local development and an opt-in real PostgreSQL/Qdrant test. This documentation pass did not execute tests or verify live service state.
- `docs/architecture.md` is the pre-existing detailed architecture source; the root `architecture.md` added for the requested filename is a short navigational summary.
