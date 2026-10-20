# Product and system design

## Experience

The frontend is a single workspace with registration and sign-in states. After authentication, a member can upload a text or PDF file and choose organization-wide or restricted visibility, inspect the accessible document list, delete documents they are allowed to delete, and submit a question. Answers are presented with document and chunk references. Authorization remains entirely server-side.

The current interface does not expose a restricted-reader grant editor, despite the backend permission replacement endpoint. No such UI capability is assumed here.

## API surface

- `GET /auth/csrf`, `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`
- `POST /documents?filename=…&visibility=org|restricted`, `GET /documents`
- `PUT /documents/{id}/permissions`, `DELETE /documents/{id}`
- `POST /qa` with a question; returns an answer and document/chunk citations
- `GET /health` reports process health only; it does not probe dependencies

Unsafe requests use same-origin CSRF validation. Document routes derive tenant and uploader context from the session/database. Full response behavior and limits are defined in backend route/config code and the repository README.

## Data and trust boundaries

PostgreSQL stores identity, current membership and grants, document state, and chunk text. Qdrant stores vectors plus identity metadata for candidate lookup. Local disk stores the uploaded source file. Sentence Transformers generates embeddings locally; the answering provider receives only authorized retrieved chunks and the question.

Trust flow:

```text
Browser session → FastAPI → PostgreSQL permission scope
                           → filtered Qdrant candidate search
                           → PostgreSQL re-authorization + chunk text
                           → mock or configured LLM → answer + citations
```

## Current constraints

The implementation is single-organization per user, uses one Qdrant collection, local file storage, synchronous small-file ingestion, and CPU embeddings. Mock answering is the default. PostgreSQL RLS and asynchronous ingestion are optional future hardening items; no deployment topology or production proxy setup is provided by the current development configuration.
