# Backlog

## Must-Fix (Spec Compliance)

1. [x] Align default runtime database with v1 spec
- Current default uses SQLite; spec baseline is PostgreSQL.
- Tasks:
  - Set PostgreSQL as documented default for non-test runtime.
  - Keep SQLite test/dev override explicit.
  - Add environment validation warning when running production mode on SQLite.

2. [x] Implement missing source table indexes from spec
- Missing/partial indexes: `(run_id, accepted)`, `(doi)`, and trigram title index.
- Tasks:
  - Add index declarations/migrations.
  - Add migration notes for SQLite compatibility vs PostgreSQL-only trigram.
  - Verify query plans for list/export/dedup paths.

3. [x] Enforce web source allowlist for Brave/vendor/conference coverage
- Spec requires allowlist-controlled domain scope.
- Tasks:
  - Add allowlist config file loader (`config/domains_allowlist.txt`).
  - Filter Brave results by allowlisted domains before ingestion.
  - Add tests for allow/deny domain behavior.

4. [x] Preserve provenance history during dedup merges
- Current merge fills missing fields but does not persist provenance history.
- Tasks:
  - Add append-only provenance history field/store.
  - Record all discovery methods and parent lineage on merge.
  - Expose provenance in export artifact.

5. [x] Apply citation expansion prioritization before truncation
- Spec requires ranking by abstract/DOI/recency/keyword overlap.
- Tasks:
  - Rank citation candidates per spec before applying `per_direction_limit`.
  - Add deterministic tie-breakers.
  - Add tests to validate ranking and truncation order.

6. [x] Add operational observability baseline
- Spec expects structured logs and metrics.
- Tasks:
  - Add structured JSON logs with `run_id`, `iteration`, `provider`, `latency_ms`.
  - Add counters: fetched/accepted/rejected/dedup/api_errors.
  - Add latency histograms per provider call.

7. [x] Show clear warning when only heuristic filtering is active
- Users must be explicitly informed when AI filtering is not enabled/available.
- Tasks:
  - Add a run-level warning flag/message when `USE_AI_FILTER=false` or AI token missing.
  - Include warning in status response and run logs.
  - Add tests covering: AI enabled, AI disabled, AI requested but token missing.

## Phase 2 Implementation Tasks (Document Acquisition)

Decision lock (approved):
- Access policy: best-effort all links
- Storage: filesystem + DB index
- Formats: PDF first, HTML fallback

1. [x] P0 - Add acquisition API endpoints
- `POST /v1/acquisition/runs`
- `GET /v1/acquisition/runs/{acq_run_id}`
- `GET /v1/acquisition/runs/{acq_run_id}/items`
- `GET /v1/acquisition/artifacts/{artifact_id}`
- `GET /v1/acquisition/runs/{acq_run_id}/manifest`

2. [x] P0 - Add acquisition database schema and migrations
- `acquisition_runs`
- `acquisition_items`
- `artifacts`
- Required indexes for status/source/checksum lookups

3. [x] P0 - Implement URL resolution and download engine
- Candidate URL resolution from DOI/source metadata
- PDF-first strategy with HTML fallback
- File validation (content-type, size limits)
- SHA256 checksuming

4. [x] P0 - Add retry/resume behavior
- Per-download retry policy (`3 attempts`, `1/2/4s`)
- `retry_failed_only` mode
- Skip already-successful items on resume

5. [x] P1 - Add filesystem artifact layout and manifest generation
- `acquisition/{acq_run_id}/{source_id}/source.pdf|source.html`
- Run-level `manifest.json`
- Artifact metadata parity between DB and filesystem

6. [x] P1 - Add tests for acquisition stage
- Unit: URL selection, content classification, checksum, status transitions
- Integration: mixed success/failure run, resume behavior
- API: endpoint semantics, auth/rate-limit/error consistency

7. [x] P1 - Add observability for acquisition stage
- Structured logs (`acq_run_id`, `source_id`, `domain`, `latency_ms`, `status`)
- Metrics counters and latency histogram

Definition of done for Phase 2:
1. Acquisition run can process accepted discovery sources end-to-end.
2. Each successful artifact has checksum, size, MIME, and deterministic path.
3. Failed/partial statuses are queryable via API and resumable with retry mode.
4. Manifest content matches DB artifact records for the same run.
