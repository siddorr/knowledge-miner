# Current Scope

Status date: 2026-03-11

## Product State (Now)

Knowledge Miner is an end-to-end UPW literature workflow for semiconductor manufacturing:
1. Discovery run execution across external providers.
2. Citation expansion (forward/backward) and deduplicated corpus growth.
3. AI-first relevance decisions with human review override.
4. Document acquisition (PDF-first, HTML fallback) with legal-source resolution and manual recovery.
5. Full-text parsing/chunking and searchable corpus.
6. Task-first HMI (`Dashboard -> Discover -> Review -> Documents -> Search`) with `Advanced` technical controls.

## In Scope

1. Discovery pipeline:
- seed query execution
- provider search connectors
- citation expansion
- canonical ID assignment
- deduplication
- iterative query refinement
2. Decisioning and review:
- heuristic scoring as recommendation metadata
- AI-first final auto-decision policy
- `needs_review` queue and human `accept/reject` override
3. Acquisition pipeline:
- URL resolution chain with OA/legal preference
- retries/resume
- artifact indexing + manifests
- manual recovery list and manual upload registration
4. Parse/search pipeline:
- parse run execution
- document/chunk storage
- search APIs and HMI search workflow
5. Operations and UX:
- task-first HMI pages
- advanced diagnostics/settings
- structured logging and run-level observability

## Out of Scope

1. Knowledge graph construction.
2. Topic clustering.
3. Entity/relationship extraction as a productized feature.
4. Automated narrative report generation.
5. Multi-tenant RBAC/auth redesign.

## Canonical User Workflow

1. Start run in `Dashboard`/`Discover`.
2. Review candidates in `Review`.
3. Resolve retrieval failures in `Documents`.
4. Query parsed knowledge in `Search`.
5. Use `Advanced` only for diagnostics, IDs, and low-level controls.

## MVP Boundary

MVP is complete when a user can:
1. Launch discovery from seed queries.
2. Reach accepted/rejected decisions (AI + human review path).
3. Retrieve document artifacts or recover manually.
4. Parse and search resulting corpus.
5. Export core artifacts (`sources_raw`, acquisition manifest, manual recovery CSV).

This boundary is implemented in the current repository.

## Near-Term Roadmap Summary

1. Production hardening and deployment reliability.
2. UX polish and operator efficiency improvements.
3. Search quality and retrieval accuracy improvements.
4. Backlog-driven enhancements tracked only in `BACKLOG.md`.
