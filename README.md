# Permission-Aware Multi-Tenant RAG Platform

A student portfolio project: organizations upload documents, members ask questions, and the server retrieves **only** passages the current user is allowed to see. Answers include citations. Isolation is enforced in the API, not by hiding UI.

The backend implements cookie-based authentication with CSRF protection, tenant-scoped document management, ingestion, permission-aware retrieval, question answering, and audit-event recording. The frontend connects login, document upload/listing, and question answering. Development migrations 0001 through 0004 have been applied and verified, and the opt-in PostgreSQL + Qdrant integration test passes against real services.

## What is already decided

- Users belong to **one organization** in the MVP.
- Documents can be **organization-wide** or **restricted** (named users plus the org owner).
- Sessions live in Postgres and are sent as **HttpOnly cookies**.
- Vectors live in **one Qdrant collection**, always filtered by organization and allowed document IDs.
- Tests use a **mock LLM**. Demos can use an OpenAI-compatible API key later.

## Document management API

- `POST /documents?filename=...&visibility=org|restricted` uploads a UTF-8 text or PDF body. The server derives the uploader and organization from the authenticated session, generates the storage path, and enforces the configured size limit.
- `GET /documents` lists only documents the signed-in member may read in their organization. Organization-wide documents are visible to members; restricted documents are visible to their named readers and the organization owner.
- `PUT /documents/{id}/permissions` replaces the reader list for a restricted document. Only the organization owner can manage it, and each reader must be a member of the same organization.
- `DELETE /documents/{id}` returns a non-disclosing 404 for cross-tenant or otherwise unauthorized documents. The uploader or organization owner may delete; a read grant alone does not grant deletion rights.

Unsafe requests continue to require the existing same-origin CSRF checks. Client-supplied organization, uploader, and role values are not authorization inputs.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) or another engine that can run Compose v2
- Git
- Python 3.11+ (the backend declares `requires-python = ">=3.11"`)
- Node.js 20+ and npm

## Local development setup

Commands below are Windows PowerShell. Run them from the repository root unless a step says otherwise. The backend reads the repository-root `.env`; it does not use `backend/.env`.

### 1. Create the environment file

```powershell
Copy-Item .env.example .env
```

`.env` is gitignored. Never commit it and never put real credentials or API keys in `.env.example`.

Keep these values consistent:

- `POSTGRES_PORT`, `DATABASE_URL`, and `TEST_DATABASE_URL` must use the same local host and port. The checked-in example uses `5432`; the current local copy uses `5433`.
- `DATABASE_URL` and `TEST_DATABASE_URL` must be different databases named exactly `permission_aware_rag` and `permission_aware_rag_test`, use the `postgresql+psycopg://` driver, and point at a local host. The migration and test safety checks enforce this.
- `COOKIE_SECURE=false` is required for local HTTP. If you set it to `true`, `CSRF_SECRET` must be a random value of at least 32 characters and must not start with `change-me`.
- Generate `CSRF_SECRET` with a cryptographically secure source; do not reuse the placeholder outside local development.

### 2. Start PostgreSQL and Qdrant

```powershell
docker compose up -d
docker compose ps
```

PostgreSQL is published on `POSTGRES_PORT` (the current local copy uses `5433`; `.env.example` uses `5432`). Qdrant HTTP is published on `QDRANT_HTTP_PORT` and defaults to `6333`. The `document_chunks` collection is created automatically on the first document ingest.

Stop the containers with `docker compose down` when needed. Preserve the existing database and vector-store data; do not remove their volumes or use reset or wipe commands.

### 3. Create and activate the backend virtual environment

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks the activation script, either run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in the current terminal, or call `.\.venv\Scripts\python.exe` directly instead of activating.

### 4. Install backend dependencies

The backend is a setuptools project declared in `backend/pyproject.toml`. Install it editable so the `app` package and all declared dependencies are available:

```powershell
python -m pip install -e .
```

### 5. Apply database migrations

From `backend`, with the virtual environment active:

```powershell
alembic -x target=dev upgrade head
```

Migration target selection is mandatory and fail-closed:

- `-x target=dev` requires `DATABASE_URL` to point at `permission_aware_rag`.
- `-x target=test` requires `TEST_DATABASE_URL` to point at `permission_aware_rag_test`.
- Both URLs must be set, locally hosted, and distinct. Alembic refuses to guess or run without a target.

Do not run migrations against a production database. To inspect the current revision:

```powershell
alembic -x target=dev current
```

### 6. Start the FastAPI backend

From `backend`, with the virtual environment active:

```powershell
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers
```

`--no-proxy-headers` keeps the ASGI scheme tied to the direct local HTTP connection; the Vite proxy preserves the browser's `Host` header for the CSRF same-origin check. Do not enable trusted forwarded headers for this local flow.

The health endpoint is `GET http://127.0.0.1:8000/health` and returns `{"status": "ok"}`.

### 7. Install and start the frontend

In a second terminal:

```powershell
cd frontend
npm install
npm run dev
```

Vite listens at `http://localhost:5173` (fixed port) and proxies `/auth`, `/documents`, and `/qa` to `http://127.0.0.1:8000`. `CORS_ORIGINS` already includes `http://localhost:5173` in `.env.example`.

### 8. Create a local account

Create an account from the frontend's sign-up page. The page calls `POST /auth/register`, which creates the user, organization, and owner membership and signs the new user in with the same HTTP-only session cookie used by login.

For local development, you can also create the first owner account directly with the existing registration service. From `backend`, with the virtual environment active and migrations applied:

```powershell
python -c "from app.db.session import get_session_factory; from app.registration import register_user; s = get_session_factory()(); register_user(s, email='owner@example.com', password='replace-with-a-strong-password', organization_name='Example Org'); s.close()"
```

This creates a user, organization, and owner membership in the database referenced by `DATABASE_URL`. Sign in at the frontend with those credentials.

### 9. Run the tests

Backend fast suite, from `backend` with the virtual environment active:

```powershell
python -m pytest
```

Database-marked tests are skipped by default. To run the real PostgreSQL + Qdrant test:

1. Create the test database once, from the repository root (replace `rag` if `POSTGRES_USER` differs):

   ```powershell
   docker exec par-postgres psql -U rag -d postgres -c "CREATE DATABASE permission_aware_rag_test;"
   ```

2. Apply the test migrations, from `backend`:

   ```powershell
   alembic -x target=test upgrade head
   ```

3. Run the opt-in integration test, from `backend`:

   ```powershell
   python -m pytest tests/test_real_pipeline_integration.py --run-db
   ```

The test suite overwrites `DATABASE_URL` inside the pytest process with the verified `TEST_DATABASE_URL`; it never uses the development database. Both URLs must be configured and distinct, or the suite refuses to run database tests.

Focused backend examples:

```powershell
python -m pytest tests/test_evaluation.py tests/test_retrieval.py
```

Frontend tests, from `frontend`:

```powershell
npm test
```

Focused frontend examples:

```powershell
npm test -- src/App.test.jsx src/workspaceApi.test.js
```

### 10. LLM configuration

- `LLM_PROVIDER=mock` is the `.env.example` default. `MockLLMProvider` answers by quoting the retrieved chunk that best covers the question, and it abstains when the retrieved documents do not cover the question. It needs no API key and is the safe local and test mode.
- For real generation, set these in `.env`:

  ```dotenv
  LLM_PROVIDER=openai
  OPENAI_API_KEY=<your key>
  OPENAI_BASE_URL=https://api.openai.com/v1
  OPENAI_MODEL=gpt-4o-mini
  ```

  `OPENAI_BASE_URL` may point at any OpenAI-compatible API. The backend calls `{OPENAI_BASE_URL}/chat/completions` with `temperature: 0`, instructs the model to cite the supplied source markers, and strips those markers from the returned answer. If `LLM_PROVIDER=openai` and `OPENAI_API_KEY` is empty, `/qa` returns `503`.
- Retrieval always uses the local `EMBEDDING_MODEL_NAME` (default `sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions) on CPU. The model is loaded lazily on the first upload or question and must be available in the local Sentence Transformers cache or downloadable on first use.
- Keep real keys only in the gitignored `.env`.

## Repository layout

```text
backend/     Python backend with SQLAlchemy models and Alembic migration infrastructure
backend/eval Golden question set and evaluation metrics (M7)
frontend/    React + Vite workspace
data/uploads Organization-scoped files (gitignored contents)
docs/        Architecture and design notes
```

## Evaluation workflow (M7)

`backend/eval/` holds the golden question set and the scoring harness:

- `golden_set.json` — questions with the documents each may retrieve, plus the forbidden documents and terms that must never surface.
- `evaluate.py` — pure metrics (Recall@k, citation precision, isolation), plus `evaluate(cases, run_case)` to score any pipeline runner.

The focused test `backend/tests/test_evaluation.py` drives the golden set through the real permission-aware retrieval and answering boundary using a deliberately hostile vector search that also returns cross-tenant candidates:

```bash
cd backend
.venv/Scripts/python.exe -m pytest tests/test_evaluation.py
```

## Planned milestones

| Milestone | Focus |
| --------- | ----- |
| M0–M1 | Repository, schema, authentication, organizations, and sessions (implemented) |
| M2 | Tenant-scoped document upload/list/delete and restricted-reader management (implemented) |
| M3–M5 | Ingestion, permission-aware retrieval, and question answering (implemented; external services required) |
| M6 | React workspace UI (implemented) |
| M7 | Evaluation set and metrics (implemented in `backend/eval/`) |
| M8 | Optional hardening (Postgres RLS, async ingest) |

Read [docs/architecture.md](docs/architecture.md) for how requests and isolation work.

## Security notes for M0

- `.env` is gitignored. Keep real secrets out of git.
- Uploaded documents are gitignored. Do not commit tenant files.
- The example password `changeme` is a **placeholder** for local Docker only.
