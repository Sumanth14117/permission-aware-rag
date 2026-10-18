# Architecture (M0)

This document describes the system we will build. M0 does not run the API or UI yet. It exists so later code has a contract to follow.

## Goal

Members of an organization can ask questions about documents they are allowed to read. The server finds relevant passages, asks a language model to answer from those passages, and returns **citations**. Documents and chunks from another organization must never appear in retrieval or in the prompt.

## Components

- **Frontend (React + Vite, M6):** login, document list, question box, citations. The browser is not trusted for access control.
- **Backend (Python + FastAPI, from M1):** authentication, permissions, ingest, retrieval, LLM calls, audit log.
- **PostgreSQL:** users, organizations, memberships, sessions, documents, permissions, chunk metadata, audit events. This is the **source of truth** for who may see what.
- **Qdrant:** vector index only. Payloads store `org_id` and `document_id` so search can filter. Qdrant does not decide permissions.
- **Local disk (`data/uploads/`):** files stored under organization-scoped directories.
- **Sentence Transformers:** local embeddings. Model name is configured in `.env`.
- **LLM provider interface:** `mock` in tests; optional OpenAI-compatible HTTP API for demos.

## Ask-question flow (from M4–M5)

1. The browser sends the question. The session cookie identifies the user. The client does not choose an organization id.
2. The server loads the session, user, and **single** membership, then the `org_id`.
3. A permission service builds `allowed_document_ids`:
   - every document in the org with `visibility = org`
   - plus restricted documents granted to this user
   - the org owner can read restricted documents
4. The question is embedded with the same model used at ingest.
5. Qdrant search **must** include filters: this `org_id` and `document_id` in `allowed_document_ids`. Search without those filters is a bug.
6. Retrieved points are checked again in Postgres. Extra points are dropped.
7. The LLM sees only surviving passages. Citations that do not match retrieved ids are stripped.
8. An audit event is recorded.

```text
Browser --cookie--> FastAPI --> permission service --> allowed document ids
                              --> Qdrant (filtered search)
                              --> Postgres (re-check)
                              --> LLM provider
                              --> audit log
```

## Data model (to be created in M1–M2)

- `users`, `organizations`, `memberships` (one org per user in the MVP; role `owner` or `member`)
- `sessions` (hashed token, expiry, revocation)
- `documents` (`org_id`, visibility `org` | `restricted`, processing status)
- `document_permissions` (restricted readers)
- `chunks` (`org_id` denormalized, text, Qdrant point id)
- `audit_events`

## Tenant isolation

**PostgreSQL:** every tenant row has `org_id`. Queries always include the org from the session. Cross-tenant URLs return `404` so we do not confirm that another org’s document exists.

**Qdrant:** one collection. Payload indexes on `org_id` and `document_id`. A single search helper will refuse to run if the org filter is missing (fail closed).

**Files:** later paths look like `data/uploads/{org_id}/{document_id}`. User-supplied filenames are never used as filesystem paths.

## Ingestion (M3, small files, synchronous)

Upload → save file → extract text → fixed-size overlapping chunks → embed → upsert Qdrant → store chunks in Postgres → mark document `ready`. On failure, mark `failed` and do not leave searchable orphan vectors.

## Testing and evaluation (later)

- Pytest: login, forbidden actions, **cross-tenant isolation** (unique secret phrases per org).
- Frontend: Vitest + React Testing Library.
- A small golden question set and metrics (Recall@k, citation precision, isolation score) in M7.

## Non-goals for the MVP

No Kubernetes, no microservices, no SSO, no S3, no collection-per-org, no Postgres RLS until M8.
