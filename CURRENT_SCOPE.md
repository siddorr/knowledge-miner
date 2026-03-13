# Current Scope

Status date: 2026-03-13

## Product State (Now)

Knowledge Miner is an end-to-end UPW literature workflow for semiconductor manufacturing:
1. Discovery run execution across external providers.
2. Citation expansion (forward/backward) and deduplicated corpus growth.
3. AI-first relevance decisions with human review override.
4. Document acquisition (PDF-first, HTML fallback) with legal-source resolution and manual recovery.
5. Full-text parsing/chunking and searchable corpus.
6. HMI-driven workflow covering `Discover`, `Review`, `Documents`, `Library`, and `Advanced`.
7. Session persistence with Save/Load, history, and auto-restore support.
8. Event-driven refresh model with SSE plus bounded fallback refresh.
9. Advanced diagnostics, logs, and technical controls isolated in `Advanced`.

## Approved Target UI Contract

The approved target design is now the rewritten [`UI_SPEC.md`](/home/garik/Documents/git/knowledge-miner/UI_SPEC.md), aligned to GUI Design Specification v1.1.

Target HMI direction:
1. Research workstation shell.
2. Separate header/status row, controls row, navigation row, workspace, and footer.
3. Primary operator-facing concept: `Session`.
4. Canonical workflow: `Discover -> Review -> Documents -> Library Export`.
5. Rayyan-style review layout.
6. Technical complexity isolated in `Advanced`.

## Current vs Target UI

Current implementation still reflects parts of the older design:
1. Some shell structure and layout contracts still follow the previous HMI model.
2. `Library` is still present in implementation where target docs now require `Library Export`.
3. Some user-facing wording and controls remain transitional.

Target implementation must migrate toward:
1. Separate controls row above navigation.
2. Consistent `Session` wording in primary UX.
3. Rayyan-style Review.
4. `Library Export` as the final stage label.
5. `Advanced` as diagnostics-only.

Current implementation is not the design source of truth; the rewritten `UI_SPEC.md` is.

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
- `needs_review` queue and human `accept/reject/later` override
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
- HMI shell and task pages aligned to the active UI spec
- advanced diagnostics/settings
- structured logging and run-level observability
- global and per-action busy/progress indicators
- batch manual-upload recovery with auto DOI/title matching

## Out of Scope

1. Knowledge graph construction.
2. Topic clustering as a productized UX concept.
3. Entity/relationship extraction as a productized feature.
4. Automated narrative report generation.
5. Multi-tenant RBAC/auth redesign.

## Canonical User Workflow

1. Start discovery in `Discover`.
2. Review candidates in `Review`.
3. Process approved sources and resolve retrieval failures in `Documents`.
4. Export curated knowledge packages from `Library Export`.
5. Use `Advanced` only for diagnostics, IDs, and low-level controls.

## MVP Boundary

MVP is complete when a user can:
1. Launch discovery from seed queries.
2. Reach accepted/rejected/review decisions through AI + human review paths.
3. Retrieve document artifacts or recover manually.
4. Parse and search resulting corpus.
5. Export core artifacts (`sources_raw`, acquisition manifest, manual recovery CSV).

This product boundary is implemented, while UI design replacement remains an active implementation stream tracked in `BACKLOG.md`.

## Near-Term Roadmap Summary

1. Replace remaining old-design HMI behavior with the new design contract.
2. Production hardening and deployment reliability.
3. UX polish and operator efficiency improvements.
4. Search quality and retrieval accuracy improvements.
5. Backlog-driven enhancements tracked only in `BACKLOG.md`.
