# Development roadmap

This is a continuation checklist, not a claim that the listed future work has been approved or started. Completed milestones are summarized from the README.

## Implemented

- [x] M0–M1: repository foundation, schema/migrations, authentication, organizations, memberships, and sessions
- [x] M2: tenant-scoped document upload/list/delete and restricted-reader management API
- [x] M3–M5: synchronous ingestion, permission-aware retrieval, and question answering
- [x] M6: React workspace UI for registration/sign-in, upload/list/delete, and QA
- [x] M7: golden evaluation set and metrics

## Planned / optional

- [ ] M8: evaluate PostgreSQL row-level security and asynchronous ingestion as optional hardening
- [ ] Decide whether restricted-reader grant management belongs in the UI; currently only the backend endpoint is documented
- [ ] Define production deployment and reverse-proxy configuration before claiming production readiness

## Continuation notes

- Keep docs aligned with implementation and label roadmap proposals as planned.
- Before adding a feature, trace the route, service, model, and tests that define its security and state transitions.
- Preserve the database, vector store, uploads, `.env`, and existing uncommitted work. Database migrations and destructive data actions require deliberate task-specific authorization.
