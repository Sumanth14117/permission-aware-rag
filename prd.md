# Product requirements

## Product

Permission-Aware RAG is an MVP portfolio project for organization members to ask questions over uploaded documents while receiving answers grounded in content they are authorized to read. Authorization is enforced by the server; the browser UI is not a security boundary.

## Users and access model

- A user belongs to one organization in the MVP.
- The first account created for an organization is its owner; other accounts are members.
- Documents are organization-visible or restricted to named organization members. Owners can read all restricted documents.
- A document's uploader or organization owner may delete it. Read access alone does not permit deletion.

## Implemented capabilities

- Account registration, login, logout, database-backed sessions in HttpOnly cookies, and CSRF protection for unsafe requests.
- Upload UTF-8 text or PDF files, list documents readable by the signed-in user, replace restricted-reader grants as owner, and delete documents with tenant-scoped authorization.
- Synchronous ingestion: extract text, split into overlapping chunks, embed locally, store chunk text and metadata in PostgreSQL, and index vectors in Qdrant.
- Ask questions over ready, authorized documents and return an answer with document/chunk citations. Mock answering is the default; an OpenAI-compatible provider is configurable.
- Best-effort audit events and a golden-set evaluation harness.
- React workspace for registration/sign-in, document upload/list/delete, and question answering.

## Security and reliability requirements

- Derive user, organization, and role from the authenticated session and current database membership. Never treat request-supplied tenant or role values as authorization.
- Restrict vector search by organization and allowed document IDs, then re-check every candidate and load its text from PostgreSQL only after authorization.
- Do not send unauthorized text or vector payload text to the LLM. Remove citations that do not refer to retrieved sources.
- Return non-disclosing not-found behavior for cross-tenant or unauthorized document lookups.
- Fail closed when membership, storage, embeddings, vector search, or configured answering service is unavailable.
- Keep secrets and uploaded tenant files out of source control. Audit records must not contain questions, answers, credentials, or document content.

## Current boundaries and planned work

The current MVP assumes one organization per user, synchronous ingestion, local disk uploads, one Qdrant collection, and local CPU embeddings. Postgres row-level security and asynchronous ingestion are listed as optional M8 hardening, not implemented requirements. Production reverse-proxy/deployment configuration, SSO, object storage, and multi-organization membership are not established by the current repository documentation.

## Acceptance shape

The core user journey is: register or sign in → upload text/PDF with visibility → ask a question → receive an answer and citations drawn only from authorized ready documents. Backend and frontend tests cover the security boundaries; the real PostgreSQL/Qdrant pipeline test is opt-in. No test execution is recorded as part of this documentation change.
