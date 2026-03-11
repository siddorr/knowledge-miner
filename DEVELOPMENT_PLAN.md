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

1. Full-text parsing
2. Entity and relationship extraction
3. Knowledge graph
4. Topic clustering
5. Manual generation tooling

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
