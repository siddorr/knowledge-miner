# Development Plan

## v1 Delivery Plan (In Scope)

Phase 1 - Core service skeleton
- API scaffolding and auth
- run lifecycle (`queued/running/completed/failed`)
- PostgreSQL schema and migrations

Phase 2 - Search connectors
- OpenAlex connector
- Semantic Scholar connector
- Brave search connector (SerpAPI fallback)
- provider retry/backoff and error handling

Phase 3 - Processing pipeline
- normalization and canonical ID assignment
- citation expansion (forward and backward)
- abstract scoring and decision classification
- deduplication pipeline

Phase 4 - Iteration engine
- keyword extraction from accepted corpus
- guarded query generation
- stop-condition evaluation
- checkpoint/resume support

Phase 5 - Review and export
- review endpoint for `needs_review` records
- `sources_raw.json` export endpoint
- run metrics and basic monitoring counters

Status:
- Completed (implemented and tested)

## Post-v1 Backlog (Out of Scope for v1)

1. Entity and relationship extraction
2. Knowledge graph
3. Topic clustering
4. Manual generation tooling

## Phase 2 - Document Acquisition (Implemented)

Goal:
- Download article files for accepted sources and persist acquisition status/artifacts for later parsing stages.

Policy choices:
- Access policy: best-effort all links
- Storage: local filesystem + DB index
- Formats: PDF + HTML

Implemented API:
1. `POST /v1/acquisition/runs`
- Start acquisition for a completed discovery run.
2. `GET /v1/acquisition/runs/{acq_run_id}`
- Run-level progress and counters.
3. `GET /v1/acquisition/runs/{acq_run_id}/items`
- Source-level acquisition status.
4. `GET /v1/acquisition/artifacts/{artifact_id}`
- Artifact metadata and location.
5. `GET /v1/acquisition/runs/{acq_run_id}/manifest`
- Acquisition manifest export.

Implemented data model:
1. `acquisition_runs`
- run status and counters (`queued|running|completed|failed`).
2. `acquisition_items`
- per-source status (`queued|downloaded|partial|failed|skipped`), attempts, selected URL, last error.
3. `artifacts`
- file kind (`pdf|html`), path, checksum, size, MIME.

Implemented execution rules:
1. Resolve candidate URLs from source URL + DOI/provider hints.
2. Prefer PDF; fallback to HTML snapshot.
3. Retry transient failures (`3 attempts`, `1/2/4s`).
4. Support resume and `retry_failed_only`.

Implemented guarantees:
1. Deterministic artifact paths under `ARTIFACTS_DIR`.
2. Checksums and size for all saved files.
3. Manifest consistency with DB state.
4. Structured acquisition observability logs and counters.

## Phase 4.2 - Task-First HMI (Implemented)

Goal:
- Provide a first-time-user UI where core work is task-based, not pipeline-based.

Task navigation milestone:
1. `Dashboard`
2. `Discover`
3. `Review`
4. `Documents`
5. `Search`
6. `Advanced`

Scope:
1. Dashboard with clear next actions and recent activity.
2. Simplified Discover page for starting runs.
3. Simplified Review page with row-level accept/reject actions.
4. Documents page for retry/upload recovery workflow.
5. Search page with simple query/results UX.
6. Advanced page containing technical IDs, runs, logs, and diagnostics.

Status:
1. Completed (implemented and tested).

Exit criteria:
1. First-time user can complete `Discover -> Review -> Documents -> Search` without reading docs.
2. No manual ID entry required in primary task flow.
3. Technical internals remain available in `Advanced`.
4. Statuses in primary pages follow three-state model (green/yellow/red).
