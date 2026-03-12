# Backlog

Status:
- Updated on 2026-03-11: Phase 4.2 task-first HMI rebuild checklist is complete; next priorities are post-HMI hardening and deployment readiness.

## High Priority

1. [x] P0 - Auto-clean startup state for local dev server runs
- Goal: starting the app should not leave stale runtime state/process conflicts from previous sessions.
- Tasks:
  - Add startup guard to detect/clean stale run locks/temp state from interrupted runs.
  - Add single-instance safety for local `--reload` workflow (prevent duplicate workers from conflicting on same state files).
  - Add optional `CLEAN_ON_STARTUP=true` mode (default for local dev) with clear startup log message of what was cleaned.
  - Ensure cleanup is scoped and safe (no deletion of persisted DB/artifacts unless explicitly configured).
  - Add tests for:
    - stale state present -> startup completes and state is cleaned
    - no stale state -> no-op cleanup
    - cleanup disabled -> startup does not alter state

2. [x] P0 - Remove required app access token for local/internal usage
- Goal: API/HMI should work without mandatory `Authorization: Bearer ...` token.
- Tasks:
  - Make API auth optional via config switch (default: auth disabled).
  - Keep ability to enable auth explicitly for secured deployments.
  - Update HMI auth bar behavior:
    - hide/disable token controls when auth is disabled.
    - keep manual/system token mode only when auth is enabled.
  - Update API dependency wiring so endpoints do not return `401` when auth is disabled.
  - Update docs (`README.md`, `UI_SPEC.md`) with new auth-mode behavior.
  - Add tests for both modes:
    - auth disabled -> no token required.
    - auth enabled -> token required and validated.

3. [x] P0 - Fix AI filtering authentication and runtime reliability
- Goal: when AI filter is enabled, classification should work without repeated unauthorized failures.
- Tasks:
  - Validate OpenAI token load path from system environment and `.env` (single source of truth, no silent fallback to empty token).
  - Add startup/runtime health check for AI provider config (`token present`, `base URL`, `model`) and expose clear status in HMI/API.
  - Prevent repeated `401 Unauthorized` retry storms; fail fast per run with explicit warning and fallback policy.
  - Add structured AI error metrics (`auth_error`, `rate_limited`, `timeout`, `provider_error`) in logs and run summary.
  - Add tests for:
    - valid token -> AI calls succeed
    - missing/invalid token -> deterministic fallback to `needs_review` + operator-visible warning
    - HMI AI toggle + token update applies to newly created runs

4. [x] P0 - Track every HMI user operation (full UI telemetry)
- Goal: persist a clear audit trail of operator actions in HMI (query edits, button clicks, tab switches, form changes, submits).
- Tasks:
  - Define event taxonomy and schema:
    - `event_type` (`click|change|input|submit|navigate`)
    - `control_id`, `control_label`, `page`, `section`, `session_id`
    - context fields (`run_id`, `acq_run_id`, `parse_run_id` when available)
    - safe `value_preview` (truncated/redacted)
  - Add backend ingest endpoint:
    - `POST /v1/hmi/events` with auth/rate-limit parity to existing API mode.
    - Validate payloads and reject oversized/invalid events.
  - Add structured persistent logging:
    - write `hmi_event` records to project log with timestamp and correlation ids.
    - include client fingerprint basics (user agent hash) without storing sensitive data.
  - Add frontend instrumentation in HMI:
    - track button presses (`Run Discovery`, `Accept`, `Reject`, `Retry`, exports, uploads).
    - track search/query changes with debounce to avoid log spam.
    - track filter/select changes and tab/page navigation.
    - send fire-and-forget events so UI is never blocked by telemetry failures.
  - Add privacy/safety guardrails:
    - never log raw API keys/tokens/password-like values.
    - truncate long text input and mark as redacted where needed.
    - add allowlist-based capture for fields safe to log.
  - Add observability and tests:
    - API tests for event ingestion and validation errors.
    - UI tests verifying core actions emit telemetry.
    - smoke check that logs contain expected `hmi_event` lines after manual workflow.

5. [x] P0 - Fix AI run-toggle mismatch (run-level AI must be effective at execution time)
- Goal: if a run is created with AI enabled, relevance decisions must be produced by AI (or explicit AI error fallback), not silent heuristic fallback.
- Problem observed:
  - `run_d8cc48a33dbc`: `run.ai_filter_active=true`, but all rows were `decision_source=fallback_heuristic` and no `ai_filter:evaluate` calls were logged.
- Tasks:
  - Instantiate `AIRelevanceFilter` in discovery execution with run-scoped config (`enabled=run.ai_filter_active` + effective key/model/base_url), not global-only defaults.
  - Align run creation and run execution logic so AI mode is consistent across lifecycle.
  - Add explicit runtime warning when run AI is enabled but effective AI credentials/config are missing.
  - Ensure `provider_call` observability always records AI evaluation attempts/failures for AI-enabled runs.
  - Expose run diagnostics in API response:
    - `ai_filter_effective_enabled`
    - `ai_filter_config_source` (`run|global`)
  - Add tests:
    - run AI enabled + valid key -> at least some rows with `decision_source=ai`
    - run AI enabled + missing/invalid key -> deterministic `needs_review` + visible warning + AI error metrics
    - run AI disabled + global AI enabled -> no AI calls, policy remains manual/heuristic fallback

6. [x] P0 - Fix runtime DB mismatch causing `no such table: runs`
- Goal: prevent server from attaching to an uninitialized SQLite file and breaking review/acquisition flows after restart.
- Problem observed:
  - `2026-03-11 23:55:17` in `logs/knowledge_miner.log`: `sqlite3.OperationalError: no such table: runs`
  - HMI then showed `run_not_found`/`404` and actions failed.
  - Recheck on `2026-03-12`: same run can return `200` then `404` after reload; review API intermittently returns `source_not_found` for visible rows.
- Tasks:
  - Standardize dev SQLite path to an absolute project path (remove CWD-dependent ambiguity from `sqlite:///./knowledge_miner.db`).
  - Persist and enforce one DB target for all reload worker processes (same path in parent/reloader/subprocess).
  - Add startup DB readiness check for required tables (`runs`, `sources`, `acquisition_runs`, `parse_runs`).
  - Log effective DB target at startup (`database_url`, resolved sqlite file path, cwd, process id) for diagnostics.
  - Add explicit startup error/warning when schema is missing and surface it in `/v1/system/status`.
  - Add optional auto-migrate-on-start flag for local development only.
  - Improve API error mapping so schema-missing state is returned as a clear operator-facing error (not generic 500/404 cascade).
  - Add guard for review endpoint:
    - when source is not found, include run/context hint in error detail to distinguish stale UI context vs true missing source.
  - Add tests:
    - missing schema DB -> deterministic not-ready status + clear message
    - migrated DB -> healthy status and run lookup works
    - restart keeps same DB target path
    - after reload, same run/source IDs remain queryable and review actions do not flap between `200` and `404`

7. [x] P0 - Add focused diagnostics for intermittent `run_not_found/source_not_found` flapping
- Goal: collect unambiguous runtime evidence to isolate and close the `200 -> 404` run/source disappearance bug.
- Progress:
  - startup DB context logging added
  - request trace logging for discovery/review paths added
  - `/v1/system/status` DB diagnostics fields added
  - optional `/v1/debug/db-context` endpoint added (guarded by env flag)
  - `scripts/capture_db_flap_diagnostics.sh` added for timeline capture
- Scope:
  - discovery/read/review endpoints
  - startup/reload lifecycle
  - DB target resolution
- Required diagnostics:
  - Startup log block (single structured line):
    - `pid`, `ppid`, `cwd`, `database_url`, resolved sqlite absolute file path, file inode, file mtime, process role (`reloader|worker|single`).
  - Per-request trace fields for key endpoints:
    - `request_id`, `pid`, `method`, `path`, `run_id`, `source_id`, `db_file`, `db_inode`.
  - On `run_not_found` / `source_not_found`:
    - include diagnostic context in logs:
      - queried id
      - row count in `runs` and `sources` tables
      - latest 5 run ids visible in current DB session
      - whether corresponding source exists in any run
  - Add `/v1/system/status` diagnostic fields (read-only):
    - `db_target_url`
    - `db_target_resolved_path` (when sqlite)
    - `db_schema_ready`
    - `db_run_count`
    - `process_pid`
  - Optional debug endpoint (guarded by env flag, off by default):
    - `GET /v1/debug/db-context?run_id=...&source_id=...`
    - returns same diagnostics payload used in logs.
- Test/verification tasks:
  - integration test: create run, poll run/sources repeatedly across reload cycle, assert no `200 -> 404` flapping.
  - test: `source_review` for DOI IDs remains stable before/after reload.
  - manual reproduction script committed under `scripts/` to capture timeline + diagnostics automatically.
- Exit criteria:
  - one captured trace proving root cause, linked in backlog note.
  - fix PR references this diagnostics output and removes need for temporary debug endpoint/log verbosity.

8. [x] P0 - Clear indication of work in progress in HMI
- Goal: operator must always see when background work is still running and when actions are blocked/waiting.
- Tasks:
  - Add a global in-progress banner/spinner with current active phase (`discovery|acquisition|parse`) and last update time.
  - Add per-section busy states for long actions (`Run Discovery`, `Acquire Pending`, `Retry Failed`, `Parse`, `Search`) with disabled buttons during request in-flight.
  - Show run status transitions inline (`queued -> running -> completed/failed`) with explicit text, not color-only indication.
  - Add “still processing” hints when list endpoints return empty while run is active (avoid “No records found” ambiguity).
  - Persist in-progress state across polling refresh and page hash navigation.
  - Emit telemetry events for busy-state enter/exit and action-complete/fail.
  - Add tests:
  - UI test: spinner/banner appears while run is active and clears on terminal state.
  - API/UI integration test: empty intermediate state during running shows “in progress” message, not final empty-result message.
  - accessibility check: status text available to screen readers (`aria-live`).

9. [x] P0 - Apply design review findings (2026-03-12)
- Goal: close critical UX/behavior mismatches identified in full design review.
- Tasks:
  - Fix `Send Accepted to Documents` semantics:
    - either process only selected accepted `source_id`s via a new API contract, or rename UX text to explicitly say “Send all accepted in run”.
  - Implement persistent `Manual Complete` action:
    - replace UI-only message with API-backed state update and durable status.
  - Persist `Later` review state:
    - move from in-memory-only set to backend-stored review queue status.
  - Correct dashboard KPI semantics:
    - `Accepted waiting docs` must be computed from accepted-without-successful-acquisition, not generic acquisition queue count.
  - Remove flow inconsistencies:
    - align HMI header/nav/docs on one canonical flow (`Build/Review/Documents/Library/Advanced`).
    - remove hidden `#discover` dependency from default user flow or reintroduce it explicitly in nav/spec.
  - Improve stale-run recovery UX:
    - when `run_not_found/source_not_found` appears, auto-suggest/select latest valid run and show actionable reload guidance.
- Tests:
  - integration test for selected-only vs all-accepted acquisition behavior (according to finalized contract).
  - UI test that `Manual Complete` and `Later` survive reload and are reflected in API data.
  - UI regression test for status-strip KPI correctness.

10. [x] P0 - Auto-reset stale HMI run context when DB is empty or run is invalid
- Goal: prevent infinite polling/review loops on non-existent run IDs after restart/cleanup.
- Problem observed:
  - HMI keeps polling old run IDs (for example `run_1dcfa040bb46`) while DB has `runs=0`, producing repeated `run_not_found/source_not_found`.
- Tasks:
  - On HMI load and every poll cycle:
    - if `/v1/system/status` shows `db_run_count == 0`, clear `state.latest.discovery|acquisition|parse` and related run-id input fields.
    - if discovery run lookup returns `run_not_found`, auto-clear stale run context (and selected review rows) instead of retrying forever.
  - Add UX feedback:
    - show explicit message: `No active runs found. Start from Build -> Run Discovery.`
    - suppress repeating error toasts for same stale run id.
  - Add optional recovery aid:
    - query latest available run IDs (when any) and offer one-click `Use Latest`.
  - Ensure telemetry emits a single `stale_context_reset` event per reset action.
- Tests:
  - UI test: with empty DB, polling does not keep requesting old run id.
  - UI test: stale run id is auto-cleared after first `run_not_found`.
  - integration test: `db_run_count=0` leads to clean idle state without repeated 404 spam.

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

## Phase 2.1 Implementation Tasks (Manual Download Recovery)

Goal:
- Show documents that failed/partial/skipped in acquisition so end users can download them manually.

1. [x] P0 - Add manual-recovery API endpoint
- `GET /v1/acquisition/runs/{acq_run_id}/manual-downloads`
- Return only items with status in `failed|partial|skipped`.

2. [x] P0 - Define manual download response schema
- Per item include:
  - `item_id`, `source_id`, `status`, `attempt_count`, `last_error`
  - `title`, `doi`, `source_url`, `selected_url`
  - `manual_url_candidates[]` (deduped, ordered)

3. [x] P0 - Build URL candidate aggregation logic
- Combine URLs from source metadata:
  - source landing URL
  - DOI URL (`https://doi.org/...`)
  - selected acquisition URL (if any)
- Deduplicate/canonicalize and preserve deterministic ordering.

4. [x] P1 - Add export endpoint for user operations
- `GET /v1/acquisition/runs/{acq_run_id}/manual-downloads.csv`
- CSV columns aligned with manual download schema.

5. [x] P1 - Add manual-upload registration path
- `POST /v1/acquisition/runs/{acq_run_id}/manual-upload`
- Allow user-provided file registration for a `source_id` with checksum/MIME/path validation.

6. [x] P1 - Add tests for manual recovery
- API tests for filtering, pagination, and schema.
- Tests for URL candidate ordering/dedup.
- Tests for CSV export and manual-upload validation.

Definition of done for Phase 2.1:
1. End user can retrieve a clear list of non-downloaded documents with direct/manual URL options.
2. User can export the list for offline/manual processing.
3. Manually downloaded files can be registered back into acquisition artifacts with traceable provenance.

## Phase 2.2 Implementation Tasks (Legal Full-Text Coverage Expansion)

Goal:
- Increase automatic full-text success using legal/open sources before manual recovery.

1. [x] P0 - Add legal source resolution chain to acquisition
- Resolution order per source:
  - DOI landing (`https://doi.org/<doi>`)
  - OpenAlex OA location lookup
  - Unpaywall OA URL lookup
  - trusted repository checks (PubMed Central, arXiv when identifiers match)
  - existing publisher/source URL fallback
- Persist resolution attempts and selected URL source (`doi|openalex|unpaywall|pmc|arxiv|publisher`).

2. [x] P0 - Implement OA-first download policy
- Prefer OA PDF URLs over publisher paywalled pages.
- If PDF unavailable but legal HTML available, store as `partial` with extracted text path.
- Avoid retry storms on known paywall/forbidden responses (`403/401/429`) with bounded retry policy.

3. [x] P1 - Extend manual recovery payload for operator action
- Include per-item legal candidate links with provenance labels:
  - `candidate_url`
  - `candidate_source`
  - `candidate_rank`
- Include actionable reason codes:
  - `paywalled`
  - `no_oa_found`
  - `rate_limited`
  - `robots_blocked`
  - `source_error`

4. [x] P1 - Add observability and reporting for legal coverage
- Add counters in acquisition summary:
  - `resolved_via_openalex`
  - `resolved_via_unpaywall`
  - `resolved_via_repository`
  - `paywalled`
  - `manual_recovery_required`
- Add run-level coverage report artifact (`acquisition_coverage_report.json`).

5. [x] P1 - Add tests for resolution and fallback behavior
- Unit:
  - source resolver ordering and deterministic URL ranking
  - reason-code mapping from HTTP/provider outcomes
- Integration:
  - mixed OA/paywalled dataset -> expected `downloaded|partial|failed` distribution
  - manual recovery list includes labeled legal candidates

Definition of done for Phase 2.2:
1. Acquisition tries legal OA/repository sources before final failure.
2. More documents end in `downloaded`/`partial` without manual intervention.
3. Manual recovery list shows ranked legal links with clear failure reasons.
4. Coverage metrics show where full text came from and where manual work is still needed.

## Phase 3 Implementation Tasks (Document Intelligence)

Decision lock (proposed baseline):
- Parse scope: PDF text extraction first, HTML readability fallback, OCR out-of-scope for v1.
- Retrieval: PostgreSQL full-text search (tsvector/BM25-style) as required baseline.
- AI use: optional; system must work in heuristic-only mode with explicit warning.

1. [x] P0 - Add parsing and chunking API endpoints
- `POST /v1/parse/runs` (input: `acq_run_id`, `retry_failed_only`)
- `GET /v1/parse/runs/{parse_run_id}`
- `GET /v1/parse/runs/{parse_run_id}/documents`
- `GET /v1/parse/runs/{parse_run_id}/chunks`
- `GET /v1/parse/documents/{document_id}`
- `GET /v1/parse/documents/{document_id}/text`
- `POST /v1/search` (query over parsed/chunked corpus)

2. [x] P0 - Add Phase 3 database schema and migrations
- `parse_runs`
- `parsed_documents`
- `document_chunks`
- `document_findings`
- Required indexes:
  - run/status foreign-key indexes
  - `document_chunks(parsed_document_id, chunk_index)`
  - full-text index on chunk text (`tsvector`) for PostgreSQL
  - checksum/content-hash indexes for dedup/reparse skip

3. [x] P0 - Implement parser engine for acquired artifacts
- Input source: `artifacts` from completed acquisition runs.
- PDF extraction pipeline with deterministic parser order and fallback.
- HTML extraction pipeline using readability-style main-content extraction.
- Normalize outputs to a single document schema:
  - title, authors, publication_year, abstract_if_found, body_text, language
  - parse quality metrics (char_count, section_count, parser_used)
- Persist parse errors per document without failing whole run.

4. [x] P0 - Implement deterministic chunking + corpus indexing
- Chunk policy:
  - fixed target size (chars/tokens), overlap, sentence-aware boundary when possible
  - stable `chunk_id` generation from `(document_id, chunk_index, content_hash)`
- Store positional metadata (`start_char`, `end_char`) for citation/evidence mapping.
- Build incremental full-text index updates (new/updated documents only).

5. [x] P1 - Add relevance classification on full text
- Run heuristic scoring on document and chunk levels.
- Optional AI classifier for override/rerank when enabled.
- Persist per-document and per-chunk:
  - decision (`accept/review/reject`)
  - confidence
  - reason code and human-readable rationale
- Expose clear warning when AI is disabled or token missing.

6. [x] P1 - Add evidence extraction and output artifacts
- Extract evidence snippets for accepted/reviewed documents with character-span provenance.
- Generate run-level artifacts:
  - `parsed_corpus.json`
  - `search_index_manifest.json`
  - `findings_report.json`
- Ensure artifact entries match DB records and are reproducible.

7. [x] P1 - Add tests and observability for Phase 3
- Unit tests:
  - parser selection/fallback
  - chunk boundary determinism
  - index updates and query ranking behavior
- Integration tests:
  - acquisition -> parse -> search end-to-end
  - mixed success/failure parse runs
- API tests:
  - endpoint contracts, pagination, error model, auth/rate-limit
- Observability:
  - structured logs (`parse_run_id`, `document_id`, `artifact_id`, `latency_ms`, `status`)
  - counters for parsed/chunked/indexed/failed documents
  - latency histograms per parser and indexing stage

Definition of done for Phase 3:
1. Parse run can process completed acquisition artifacts end-to-end with partial-failure tolerance.
2. Every parsed document has normalized metadata, raw text, parse status, and provenance link to source/artifact.
3. Chunking is deterministic and queryable with positional metadata.
4. Search endpoint returns ranked results with document/chunk provenance.
5. Findings and report artifacts are generated and consistent with database state.

## Phase 4 Implementation Tasks (HMI Ops Dashboard)

Reference:
- `UI_SPEC.md`

Decision lock (approved):
- Surface: FastAPI-hosted web UI
- Primary mode: Ops dashboard (mixed audience)
- Live updates: auto-refresh polling
- Actions: retry + export + review + manual upload registration

1. [x] P0 - Add FastAPI-hosted HMI shell
- Serve static assets and base dashboard route.
- Keep one deployable app (API + UI).
- Add basic navigation:
  - Runs
  - Discovery
  - Acquisition
  - Parse
  - Search
  - Manual Recovery

2. [x] P0 - Implement read-only operations screens
- Runs dashboard with phase/status filters and run lookup.
- Discovery detail view:
  - run metrics
  - sources list with statuses
  - AI heuristic warning visibility
- Acquisition detail view:
  - counters and item statuses
  - error context and selected URL
- Parse detail view:
  - counters
  - parsed document list
  - chunks list

3. [x] P0 - Implement polling and runtime UX behaviors
- Auto-refresh every 5s on active tab; 15s in background.
- Stop polling when run status is terminal (`completed`, `failed`).
- Show stale-data indicator on polling failures.
- Preserve user filter/pagination state across refreshes.

4. [x] P0 - Implement core user actions in UI
- Start Discovery run.
- Start Acquisition run (`retry_failed_only` supported).
- Start Parse run (`retry_failed_only` supported).
- Submit source review decision (`accept`/`reject`).
- Trigger existing exports (`sources_raw`, acquisition manifest).

5. [x] P0 - Implement manual recovery APIs (Phase 2.1 dependency)
- `GET /v1/acquisition/runs/{acq_run_id}/manual-downloads`
- `GET /v1/acquisition/runs/{acq_run_id}/manual-downloads.csv`
- `POST /v1/acquisition/runs/{acq_run_id}/manual-upload`
- Ensure UI consumes these endpoints directly with API-parity behavior.

6. [x] P1 - Implement Manual Recovery UI workflow
- Queue view for `failed|partial|skipped` acquisition items.
- Show:
  - `title`, `doi`, `status`, `last_error`, `attempt_count`
  - `source_url`, `selected_url`, `manual_url_candidates[]`
- CSV export action.
- Manual upload registration form and result feedback.

7. [x] P1 - Implement search explorer UI
- Query interface for `POST /v1/search`.
- Results with score/snippet and links to:
  - parsed document details
  - parsed document full text
  - related source context

8. [x] P1 - Add HMI tests and acceptance checks
- UI/unit tests for polling state and error mapping.
- API integration tests for manual recovery contracts and upload validation.
- End-to-end tests:
  - Discovery -> Acquisition -> Parse -> Search
  - failed acquisition -> manual recovery queue -> CSV export -> manual upload registration

9. [x] P0 - Extend discovery APIs for visualization parity
- `GET /v1/discovery/runs/{run_id}`:
  - include `seed_queries` in response
- `GET /v1/discovery/runs/{run_id}/sources`:
  - add `status=accepted|rejected|needs_review|all`
  - keep backward compatible default (`accepted` when omitted)

10. [x] P1 - Set discovery dashboard default source visibility
- Default table view: `accepted + needs_review`
- Add quick toggles:
  - `accepted`
  - `rejected`
  - `needs_review`
  - `all`
- Persist selected filter across polling refresh.

11. [x] P1 - Add tests for discovery visibility behavior
- API tests for `status` filter:
  - accepted only
  - rejected only
  - needs_review only
  - all
- API test verifies `seed_queries` is returned by run status.
- UI test verifies default view shows `accepted + needs_review`.

12. [x] P0 - HMI auth via system variable with UI fallback
- Add server-side config for default HMI token (from environment variable).
- On HMI load, prefill API key from server-provided value when configured.
- Keep manual API key input as fallback/override in browser.
- Add clear UI state: `Using system token` vs `Using manual token`.

13. [x] P0 - Add "Create New Session" button in HMI (Discovery)
- Add HMI action to create a discovery run from seed queries/max iterations.
- Call `POST /v1/discovery/runs` from UI and show request/response feedback.
- On success:
  - surface `run_id` prominently
  - auto-insert created `run_id` into Runs and Discovery panels
  - append row to Runs table.

14. [x] P0 - Improve run ID discoverability in HMI
- Display created run IDs in success toast/panel after create actions.
- Add "Copy ID" action for run identifiers.
- Keep latest IDs visible in Runs dashboard state for quick reuse.

15. [x] P1 - Add tests for session creation and token source UX
- HMI/API tests for create-session action and run_id propagation in UI state.
- Test system-token prefill path and manual override path.
- Test ID visibility and copy action presence.

16. [x] P1 - Improve post-create workflow guidance (HMI)
- After `Create New Session`, auto-focus Discovery run context and show explicit next-step banner:
  - `Run created: <run_id>`
  - `Status filter currently: accepted`
  - CTA: switch to `all` or `needs_review` when accepted count is zero.
- Ensure `Run ID` remains synchronized in both:
  - Runs lookup input
  - Discovery run input
- Add UX test for zero-accepted run path:
  - created run is visible
  - user receives clear guidance why sources table may be empty.

17. [x] P0 - Simplify manual approval workflow for operators
- Discovery review table must support direct operator review without copying IDs manually.
- In `needs_review` / `all` view, show at minimum:
  - `title`
  - `abstract` (truncated with expand/collapse)
  - `source` (provider)
- Add inline one-click actions per row:
  - `Approve` button
  - `Reject` button
- On click:
  - call review API for that row source id
  - update row state immediately in UI
  - show success/error feedback without leaving the table
- Add tests:
  - approve button changes state to accepted
  - reject button changes state to rejected
  - pagination/filter state is preserved after action

18. [x] P0 - Apply operator-first UX principle consistently across all HMI stages
- Principle: operator should act from row/context directly, without manual copy-paste of IDs between forms.
- Discovery:
  - keep inline approve/reject actions in source table (task 17)
  - make row click populate detail context when needed
- Acquisition:
  - add row actions to start manual recovery from selected acquisition run/item context
  - prefill manual recovery/upload fields from selected row
- Parse:
  - add row actions to open parsed document text/detail without re-entering IDs
- Search:
  - preserve one-click doc/text/source actions (already present) and keep context sync with Discovery panel
- Manual recovery:
  - add row action to prefill `source_id` in upload form
  - optional drag-drop upload target per row
- Global:
  - remove duplicate manual ID forms where row-level actions can replace them
  - keep `Latest IDs` as helper, not primary workflow dependency
- Tests:
  - end-to-end operator flow without manual ID copy between sections
  - state synchronization tests for row action -> target form/context population

19. [x] P1 - Add HMI control to enable/disable AI filter
- Goal: operator can toggle AI filter mode from HMI without manual shell/env edits.
- Tasks:
  - Add runtime settings endpoint(s) for AI filter mode:
    - read current state (`use_ai_filter`, `ai_filter_active`, warning)
    - update `use_ai_filter` (and optionally model/base URL) safely
  - Add HMI settings panel:
    - toggle AI filter on/off
    - optional key/model/base URL inputs (masked key entry)
    - clear status feedback (`active`, `disabled`, `token missing`, `error`)
  - Apply settings to newly created runs (and define behavior for already running runs).
  - Add guardrails:
    - never expose full API key back to UI
    - validate allowed model/base URL format
  - Update docs with runtime toggle behavior and security notes.
  - Add tests:
    - settings read/update API
  - HMI toggle flow
  - run status reflects updated AI mode (`ai_filter_active`, `ai_filter_warning`)

20. [x] P0 - Fix review action for source IDs containing "/" (DOI-safe routing)
- Problem: `Approve/Reject` fails with `404` for DOI-like source IDs because current path parameter does not accept slashes.
- Required changes:
  - Update review route to path-safe parameter (accept full source IDs with `/`).
  - Keep HMI approve/reject actions compatible with encoded IDs.
  - Optionally add non-path review endpoint fallback for robust ID handling.
- Tests:
  - API test: review works for source IDs with slash-containing DOI format.
  - HMI/API integration test: inline approve/reject succeeds on DOI-style rows.

Definition of done for Phase 4:
1. A user can execute the core operational flow from browser without curl.
2. All HMI actions map to API results without hidden client-only state.
3. Manual recovery is operational: queue visibility, CSV export, manual upload registration.
4. Polling/status/error UX is stable and understandable for mixed technical/non-technical users.

## Phase 4.1 Implementation Tasks (AI-First Decision Policy Rollout)

Goal:
- Make AI the primary decision-maker while keeping heuristic as recommendation-only fallback context.

1. [x] P0 - Switch discovery decision engine to AI-first
- Final auto decision source: AI classifier.
- On per-candidate AI failure/timeout: final decision = `needs_review`.
- If AI unavailable at run start: run allowed, all candidates default to `needs_review`.

2. [x] P0 - Add decision provenance fields to source model/API
- `final_decision`
- `decision_source` (`ai|fallback_heuristic|policy_no_ai|human_review`)
- `heuristic_recommendation`
- `heuristic_score`
- Preserve backward compatibility fields (`accepted`, `review_status`).

3. [x] P0 - Update discovery/export contracts
- `/v1/discovery/runs/{run_id}/sources` returns decision-trace fields.
- Export payload includes decision provenance and heuristic recommendation context.

4. [x] P1 - Add tests for AI-first policy
- AI success -> final decision from AI.
- AI runtime failure -> final `needs_review`, decision source `fallback_heuristic`.
- AI disabled/missing token -> final `needs_review`, decision source `policy_no_ai`.
- Human review override -> decision source `human_review`.

Definition of done for Phase 4.1:
1. Heuristic no longer performs final auto-accept/auto-reject in AI-first mode.
2. AI failures are handled gracefully with `needs_review` fallback.
3. API and exports expose decision provenance clearly.
4. Legacy consumers using `accepted`/`review_status` remain functional.

## Phase 4.2 Implementation Tasks (HMI UX Rebuild - Operator First)

Reference:
- `UI_SPEC.md` (Task-First UX)

1. [x] P0 - Hard replace HMI with task-first navigation
- Set top-level nav to:
  - `Dashboard`
  - `Discover`
  - `Review`
  - `Documents`
  - `Search`
  - `Advanced`
- Remove stage-first navigation from primary UI paths.

2. [x] P0 - Build Dashboard as default landing page
- Add "Run Discovery" primary CTA.
- Add attention cards:
  - sources to review
  - failed document downloads
  - processing errors
- Add recent activity summary with discovered/accepted/rejected counters.

3. [x] P0 - Implement simplified Discover page
- Query list input + `Run Discovery` action.
- Show last-run summary only (found/accepted/rejected).
- Exclude technical IDs from default view.

4. [x] P0 - Implement simplified Review page
- Primary table columns:
  - title
  - decision controls (`Accept`/`Reject`)
- Add row expand for abstract and optional context (`reason_code`, `reason_text`, confidence).
- Ensure no manual source ID entry in primary workflow.

5. [x] P0 - Implement Documents (Fix Downloads) page
- Show failed/partial download items with:
  - title
  - problem classification
  - actions (`Retry`, `Upload PDF`, `Open source`)
- Ensure row-context actions (no manual ID copy/paste).

6. [x] P0 - Keep technical complexity in Advanced page
- Move run IDs, raw statuses, logs, and JSON diagnostics to `Advanced`.
- Keep task pages free of technical forms and raw ID fields.

7. [x] P0 - Implement ID visibility policy
- Hide `run_id`, `source_id`, `acq_run_id`, `parse_run_id` on task pages by default.
- Provide optional "Technical details" drawer and copy controls.

8. [x] P0 - Implement status mapper for primary UI
- Map backend statuses to 3-state model:
  - Green = completed/ready
  - Yellow = needs attention/review/in progress
  - Red = failed/blocker
- Use text + color together in all task pages.

9. [x] P0 - Align task APIs for Dashboard/Task pages
- `GET /v1/work-queue` for actionable task groups.
- `GET /v1/system/status` for auth/AI/provider readiness.
- `GET /v1/search/global` for cross-entity search.
- Ensure responses include user-facing reason fields where available.

10. [x] P1 - Simplify Search page UX
- Keep single search field + result cards/snippets.
- Hide raw technical metadata in default results.
- Keep deep technical context accessible via Advanced/details.

11. [x] P1 - Implement first-time-user flow acceptance tests
- Validate full flow:
  - Dashboard -> Discover -> Review -> Documents -> Search
- Validate no manual ID entry required in primary flow.

12. [x] P1 - Add accessibility and clarity checks
- Keyboard navigation for core task flow.
- Explicit text labels on primary actions.
- Status readability without relying on color only.

## Phase 4.3 Implementation Tasks (GUI Spec Alignment from DOCX)

Reference:
- `Downloads/Knowledge_Miner_GUI_Spec.docx` (imported UX source-of-truth)
- `UI_SPEC.md` (canonical in-repo UI contract to implement against)
- `README.md` (documents UI source-of-truth and doc hierarchy)

Goal:
- Align HMI with the approved Build/Review/Documents/Library/Advanced operating model for first-time and daily operators.

1. [x] P0 - Replace primary navigation with spec tabs
- Required top-level nav:
  - `Build`
  - `Review`
  - `Documents`
  - `Library`
  - `Advanced`
- Remove `Dashboard` from primary navigation (can remain as internal route only if needed).
- Add badge counters on `Review` and `Documents` tabs.

2. [x] P0 - Implement global top status strip on all pages
- Show:
  - project/corpus name
  - active topic
  - pending review count
  - accepted waiting for documents
  - document failures count
  - last run state
  - one next-action button
- Keep strip visible and synchronized across navigation.

3. [x] P0 - Add launch routing policy (no standalone dashboard first)
- On `/hmi` open:
  - no topic -> `Build` in create-topic state
  - review queue exists -> `Review`
  - document failures exist -> `Documents` failed view
  - otherwise -> `Build`
- Add deterministic precedence and tests.

4. [x] P0 - Build screen restructuring (topic workspace only)
- Build page must contain:
  - topic list (`+ New Topic`)
  - tabs: `Add Sources`, `Queries`, `Runs`
  - right details panel for selected topic/run
- Keep review actions out of Build.

5. [x] P0 - Promote manual source addition to first-class Build flow
- Add source input paths:
  - DOI
  - URL
  - citation/free-text
  - bulk paste
- Add duplicate check + assign-to-topic before save.
- Add copy buttons for query/source fields.

6. [x] P0 - Review as dedicated decision workspace
- Queue-focused screen with:
  - filters (`Pending`, `Accepted`, `Rejected`, `Later`)
  - row actions (`Accept`, `Reject`, `Later`)
  - batch actions (`Accept Selected`, `Reject Selected`, `Send Accepted to Documents`)
  - preview panel with title/abstract/DOI/URL/citation + copy buttons
- Keep manual ID entry out of primary review flow.

7. [x] P0 - Documents page as acquisition operations center
- Primary actions:
  - `Acquire Pending`
  - `Retry Failed`
  - `Copy Selected DOI/URL`
- Queue filters:
  - `Awaiting`, `Acquired`, `Failed`, `Manual Recovery`
- Details panel includes error reason, attempts, `Open Source`, `Retry`, `Manual Complete`.

8. [x] P1 - Library page merge (browser + search)
- Single screen behavior:
  - empty query -> corpus browser
  - query present -> ranked search results
- Add filters (topic/year/docs/parsed) and preview pane.
- Keep advanced metadata hidden by default.

9. [x] P1 - Copy button consistency pass across all pages
- One-click copy + lightweight confirmation (`Copied`) for:
  - DOI
  - title
  - URL
  - citation
  - search query/topic query
  - error message
  - selected DOI/URL sets in Documents
- Ensure copied text is value-only (no labels/clutter).

10. [x] P1 - Topic coverage model + per-topic counters
- Topics behave as coverage buckets.
- Per topic show:
  - candidates
  - accepted
  - awaiting documents
  - failed documents
- Ensure counters drive status strip and nav badges.

11. [x] P1 - Advanced page scope enforcement
- Keep technical-only tools in Advanced:
  - run IDs
  - logs
  - raw records
  - pipeline status
  - settings
- Remove raw IDs/storage paths/low-level statuses from task pages unless inside expandable technical drawer.

12. [x] P1 - Archive or mark legacy stage-first UI docs as deprecated
- Mark old conflicting UI docs clearly (`deprecated`/`archive` banner).
- Keep one active UI source-of-truth referenced from README.

Definition of done for Phase 4.3:
1. Primary workflow visible to user is `Build -> Review -> Documents -> Library`.
2. First-time user can complete one full cycle without manual IDs or external docs.
3. Copy actions are available and consistent across all operational screens.
4. Technical internals remain accessible in Advanced but do not dominate task screens.
