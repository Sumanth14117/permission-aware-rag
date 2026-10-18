# Permission-Aware Multi-Tenant RAG Platform

A student portfolio project: organizations upload documents, members ask questions, and the server retrieves **only** passages the current user is allowed to see. Answers include citations. Isolation is enforced in the API, not by hiding UI.

The project has completed M0.6, including the initial schema migration and its read-only verification. M0.7, database models and application integration, is in progress and pending acceptance. Authentication, document upload, search, and chat UI are not yet implemented.

## What is already decided

- Users belong to **one organization** in the MVP.
- Documents can be **organization-wide** or **restricted** (named users plus the org owner).
- Sessions live in Postgres and are sent as **HttpOnly cookies**.
- Vectors live in **one Qdrant collection**, always filtered by organization and allowed document IDs.
- Tests use a **mock LLM**. Demos can use an OpenAI-compatible API key later.

## Prerequisites (M0)

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) or another engine that can run Compose v2
- Git

Python 3.11+ and Node.js 20+ are needed from later milestones, not to start the databases.

## Setup

1. Clone or open this repository.
2. Copy environment placeholders:

   ```bash
   cp .env.example .env
   ```

   On Windows PowerShell:

   ```powershell
   Copy-Item .env.example .env
   ```

3. Start PostgreSQL and Qdrant:

   ```bash
   docker compose up -d
   ```

4. Confirm the stack is healthy:

   ```bash
   docker compose ps
   ```

   Postgres should accept connections on `localhost:5432`. Qdrant HTTP should respond on `localhost:6333`.

5. Stop the containers with `docker compose down` when needed. Preserve the existing database and vector-store data; do not remove their volumes or use reset or wipe commands.

## Repository layout

```text
backend/     Python backend with SQLAlchemy models and Alembic migration infrastructure
frontend/    React + Vite app (placeholder in M0)
data/uploads Organization-scoped files (gitignored contents)
docs/        Architecture and design notes
```

## Planned milestones

| Milestone | Focus |
| --------- | ----- |
| M0 | Repo, Compose, docs (Completed) |
| M0.6 | Baseline schema migration & DDL verification (Completed) |
| M0.7 (in progress; pending acceptance) | Database models and application integration |
| M1 | Users, orgs, sessions |
| M2 | Document upload/list/delete |
| M3 | Chunking, embeddings, Qdrant ingest |
| M4 | Permissioned retrieval and isolation tests |
| M5 | LLM interface (mock + optional OpenAI-compatible) |
| M6 | React UI |
| M7 | Evaluation set and write-up |
| M8 | Optional hardening (Postgres RLS, async ingest) |

Read [docs/architecture.md](docs/architecture.md) for how requests and isolation will work.

## Security notes for M0

- `.env` is gitignored. Keep real secrets out of git.
- Uploaded documents are gitignored. Do not commit tenant files.
- The example password `changeme` is a **placeholder** for local Docker only.
