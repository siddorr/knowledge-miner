# Current Scope

Status date: 2026-03-28

## Product State (Now)

Knowledge Miner is an end-to-end UPW literature workflow for semiconductor manufacturing:
1. Discovery run execution across external providers.
2. Citation expansion (forward/backward) and deduplicated corpus growth.
3. AI-first relevance decisions with human review override.
4. Document acquisition (PDF-first, validated HTML fallback) with legal-source resolution and manual recovery.
5. Full-text parsing/chunking and searchable corpus.
6. HMI-driven workflow covering `Discover`, `Review`, `Documents`, `Library Export`, `Bookmarks`, and `Advanced`.
7. Session persistence with active-session switching, inline new-session creation, and per-session backend profile loading.
8. Event-driven refresh model with SSE plus bounded fallback refresh.
9. Advanced diagnostics, logs, and technical controls isolated in `Advanced`.
10. Global bookmarks with bookmark-based research session branching.
11. Session-scoped paper annotations with:
 - freeform tags
 - approved tags
 - category-based session tag spec
 - grouped candidate-tag review and approval/rejection
 - categorized paper tag assignment from approved session tags
 - structured AI summaries generated from parsed full text
 - per-session structured summary builder and generated prompt preview
 - summary-model visibility for current and last-used model
12. `hmi2` now surfaces document-state badges and Library filters for PDF availability, parse state, bad HTML, and current-summary availability.

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

Current implementation now matches the approved `UI_SPEC.md` across the primary workflow much more closely:
1. shell structure is implemented as `header/status -> controls -> navigation -> workspace -> footer`
2. `Review` is a two-pane screening workspace
3. `Documents` is an acquisition workstation with summary row, row details, upload, and recovery actions
4. `Library Export` is a two-pane export/annotation workspace with summary preview and structured summary display
5. session/file controls are consolidated under `File` plus visible `Save`

Remaining gaps are mainly polish and operability work tracked in `BACKLOG.md`, including:
1. further tagging-result quality/debugging work
2. further `Advanced` isolation cleanup
3. test-harness stabilization
4. additional operator-efficiency polish

`UI_SPEC.md` remains the design source of truth; this document describes shipped product state and scope boundaries.

## Session Context And Citation Expansion

1. Accepted-paper parent selection for citation expansion is session-based and deduplicated by paper identity.
2. Citation-expansion progress is tracked per saved session-context generation, not only per run.
3. In the same saved context generation:
- already-expanded accepted parents are skipped
- newly accepted session papers remain eligible
4. After a saved session-context change:
- citation expansion is renewed for that session
- all accepted session papers become eligible again in the new context generation
5. Discovery dedup remains stricter for human-reviewed papers:
- human-reviewed papers do not re-enter later runs of the same session even if the saved context changes
- citation-parent renewal after context change does not change that discovery-dedup rule

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
- bookmark workspace and bookmark-to-session branching
- per-session library annotations, category-based tag spec/review/assignment, summary generation, and structured summary preview

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
5. Branch new research from `Bookmarks` when needed.
6. Use `Advanced` only for diagnostics, IDs, and low-level controls.

## MVP Boundary

MVP is complete when a user can:
1. Launch discovery from seed queries.
2. Reach accepted/rejected/review decisions through AI + human review paths.
3. Retrieve document artifacts or recover manually.
4. Parse and search resulting corpus.
5. Export core artifacts (`sources_raw`, acquisition manifest, manual recovery CSV).

This product boundary is implemented. Remaining work is primarily product hardening and operator-efficiency improvements tracked in `BACKLOG.md`.

## Near-Term Roadmap Summary

1. Production hardening and deployment reliability.
2. UX polish and operator efficiency improvements.
3. Search quality and retrieval accuracy improvements.
4. Tag-assignment quality, observability, and operator debugging improvements.
5. Test-harness stabilization for API/UI contract coverage.
6. Backlog-driven enhancements tracked only in `BACKLOG.md`.
