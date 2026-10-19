# Architecture

This document describes the implemented system and its security contracts.

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

## Data model (M1–M2)

- `users`, `organizations`, `memberships` (one org per user in the MVP; role `owner` or `member`)
- `sessions` (hashed token, expiry, revocation)
- `documents` (`org_id`, visibility `org` | `restricted`, processing status)
- `document_permissions` (restricted readers)
- `chunks` (`org_id` denormalized, text, Qdrant point id)
- `audit_events`

## M4 retrieval security contract

PostgreSQL derives the caller's current membership and role, then builds `allowed_document_ids` from ready organization-wide documents, restricted documents granted to the member, and all restricted documents for the organization owner. If that set is empty, retrieval stops before embedding or Qdrant search.

Qdrant search always filters by both the authoritative organization ID and the allowed document IDs. Qdrant payloads provide candidate point/document/chunk identities only; they never authorize access or supply text. Each candidate is checked against current membership, document tenant, readiness, visibility, and restricted-reader grants in PostgreSQL. The stored chunk's deterministic point ID must match the candidate before its text is returned.

Malformed, stale, duplicate, cross-tenant, or unauthorized candidates are discarded. Only current authorized chunk text leaves the retrieval service. The authenticated user ID comes from the session-backed tenant context; organization and role fields in that context are not trusted by retrieval and are re-derived from PostgreSQL. Checks repeat after vector search so revoked grants or changed document status do not pass based on an earlier scope snapshot.

## Document management (M2)

The authenticated API exposes `POST /documents` for streamed text/PDF uploads, `GET /documents` for the caller's readable documents, `PUT /documents/{id}/permissions` to replace restricted-reader grants, and `DELETE /documents/{id}`. Unsafe methods retain the application's same-origin CSRF checks.

The session cookie identifies the user; the database supplies that user's single organization membership and role. Request fields never select an organization, uploader, or role. Listing and mutation queries scope documents to that organization. Cross-tenant and unauthorized deletion lookups return the same 404 response.

Members can read organization-wide documents and restricted documents explicitly granted to them. Organization owners can read all restricted documents and manage their reader grants; each granted reader must be a member of the same organization. Deletion is separate from read access: only the uploader or organization owner can delete a document.

Uploads accept UTF-8 text and PDF, enforce the configured byte limit, and store content under server-generated organization and document UUID paths. The original filename is retained only as display metadata.

## Audit events

`audit_events` is an append-only table with no foreign keys, so audit history survives deletion of the users, organizations, and documents it describes. Application code only inserts rows; there is no update, delete, or read API in this version.

Events use the `domain.action_outcome` naming convention (for example `auth.login_succeeded`, `document.delete_denied`, `qa.query_failed`) and record an outcome of `success`, `failure`, or `denied`. When available, events carry the authenticated actor, organization, and role, plus the affected resource. Failed and denied events use bounded reason codes; cross-tenant and missing document lookups share `not_found_or_forbidden` so the log never confirms that another organization's document exists. QA events store only the question length and citation count.

Recording is best-effort. Each event is written on a separate database session and transaction bound to the request session's engine, and audit code never commits or rolls back the request session. If an audit write fails, the failure is logged as a safe warning and the main response is unchanged; no event is guaranteed in that case.

## Tenant isolation

**PostgreSQL:** every tenant row has `org_id`. Queries always include the org from the session. Cross-tenant URLs return `404` so we do not confirm that another org's document exists.

**Qdrant:** one collection. Payload indexes on `org_id` and `document_id`. A single search helper will refuse to run if the org filter is missing (fail closed).

**Files:** paths use `data/uploads/{organization_id}/{document_id}`. User-supplied filenames are never used as filesystem paths.

## Ingestion (M3, small files, synchronous)

Uploads accept UTF-8 text or PDF. PDF text is extracted with `pypdf`; extracted text is size-limited and split into deterministic fixed-character windows with configured overlap. The local CPU Sentence Transformers model must report 384 dimensions, matching `all-MiniLM-L6-v2`. Qdrant point IDs are deterministic UUIDv5 values derived from the document ID and chunk index; payload metadata contains document ID, organization ID, and chunk index, never chunk text.

The persisted document supplies both tenant identifiers and media type. Ingestion transitions `pending` → `processing` → `ready`, stores chunk rows, and writes vectors. Failures transition to `failed`; partial vector writes and chunk rows are compensated where possible, and error messages omit document contents. Qdrant receives vectors and identity metadata only; document text is embedded locally and stored in Postgres.

Upload → save file → extract text → fixed-size overlapping chunks → embed → upsert Qdrant → store chunks in Postgres → mark document `ready`. On failure, mark `failed` and do not leave searchable orphan vectors.

## Testing and evaluation (M7)

- Pytest covers login, forbidden actions, and **cross-tenant isolation** using unique secret phrases per organization. Real PostgreSQL + Qdrant verification is opt-in with `--run-db`.
- Frontend uses Vitest + React Testing Library.
- `backend/eval/` holds the golden question set (`golden_set.json`) and pure metrics (Recall@k, citation precision, isolation). The focused test drives the golden set through the retrieval and answering boundary with a hostile vector search that also returns cross-tenant candidates, so a passing run proves isolation holds even when the vector layer leaks.

## Non-goals for the MVP

No Kubernetes, no microservices, no SSO, no S3, no collection-per-org, no Postgres RLS until M8.
