# Development rules

These are repository-level working rules derived from the current implementation and its documented security contracts.

## Authorization and tenant isolation

- Treat the session-authenticated user ID as the identity input. Re-derive organization membership and role from PostgreSQL for each operation.
- Never accept client-provided organization ID, uploader ID, or role as authority.
- Scope document, permission, and chunk queries by the authoritative organization.
- Keep Qdrant filters for both organization ID and allowed document IDs mandatory. Validate candidates and re-check permission, readiness, and chunk identity in PostgreSQL before using chunk text.
- Never authorize from vector payloads or put document text in vector payloads.
- Keep unauthorized and cross-tenant lookup responses non-disclosing; read grants do not imply delete rights.

## Authentication, CSRF, and secrets

- Preserve the HttpOnly session-cookie and same-origin CSRF protections for every unsafe method; do not add route exemptions casually.
- Hash passwords with the existing Argon2 implementation and store only hashes of opaque session tokens.
- Do not log or audit passwords, tokens, emails, document contents, questions, answers, or retrieved text. Keep audit events best-effort and independent from request transactions.
- Keep credentials in the gitignored root `.env`; never add real secrets to `.env.example` or source control.

## Documents and ingestion

- Build file paths from server-generated organization/document identifiers, never the submitted filename.
- Preserve configured upload/extracted-text bounds and supported text/PDF validation.
- Keep ingestion status transitions and compensating cleanup behavior. Retrieval must require a ready PostgreSQL document even if Qdrant returns a candidate.
- Treat PostgreSQL as the stored chunk-text source; vectors carry identity metadata only.

## Changes and documentation

- Keep the MVP assumptions explicit: one organization per user, synchronous ingestion, local disk, one Qdrant collection, and local embeddings.
- Mark proposed work as planned until implemented and verified; do not document roadmap items as shipped.
- Preserve existing files and data when changing repository documentation or implementation.
- Update relevant documentation when endpoints, authorization rules, model/schema, ingestion, or deployment behavior changes.
