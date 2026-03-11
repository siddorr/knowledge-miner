# Backlog

Status:
- In progress: remaining items exist in Phase 2.1, Phase 3, and HMI plan (updated on 2026-03-11).

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
- `HMI_PLAN.md`

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

6. [ ] P1 - Implement Manual Recovery UI workflow
- Queue view for `failed|partial|skipped` acquisition items.
- Show:
  - `title`, `doi`, `status`, `last_error`, `attempt_count`
  - `source_url`, `selected_url`, `manual_url_candidates[]`
- CSV export action.
- Manual upload registration form and result feedback.

7. [ ] P1 - Implement search explorer UI
- Query interface for `POST /v1/search`.
- Results with score/snippet and links to:
  - parsed document details
  - parsed document full text
  - related source context

8. [ ] P1 - Add HMI tests and acceptance checks
- UI/unit tests for polling state and error mapping.
- API integration tests for manual recovery contracts and upload validation.
- End-to-end tests:
  - Discovery -> Acquisition -> Parse -> Search
  - failed acquisition -> manual recovery queue -> CSV export -> manual upload registration

9. [ ] P0 - Extend discovery APIs for visualization parity
- `GET /v1/discovery/runs/{run_id}`:
  - include `seed_queries` in response
- `GET /v1/discovery/runs/{run_id}/sources`:
  - add `status=accepted|rejected|needs_review|all`
  - keep backward compatible default (`accepted` when omitted)

10. [ ] P1 - Set discovery dashboard default source visibility
- Default table view: `accepted + needs_review`
- Add quick toggles:
  - `accepted`
  - `rejected`
  - `needs_review`
  - `all`
- Persist selected filter across polling refresh.

11. [ ] P1 - Add tests for discovery visibility behavior
- API tests for `status` filter:
  - accepted only
  - rejected only
  - needs_review only
  - all
- API test verifies `seed_queries` is returned by run status.
- UI test verifies default view shows `accepted + needs_review`.

12. [ ] P0 - HMI auth via system variable with UI fallback
- Add server-side config for default HMI token (from environment variable).
- On HMI load, prefill API key from server-provided value when configured.
- Keep manual API key input as fallback/override in browser.
- Add clear UI state: `Using system token` vs `Using manual token`.

13. [ ] P0 - Add "Create New Session" button in HMI (Discovery)
- Add HMI action to create a discovery run from seed queries/max iterations.
- Call `POST /v1/discovery/runs` from UI and show request/response feedback.
- On success:
  - surface `run_id` prominently
  - auto-insert created `run_id` into Runs and Discovery panels
  - append row to Runs table.

14. [ ] P0 - Improve run ID discoverability in HMI
- Display created run IDs in success toast/panel after create actions.
- Add "Copy ID" action for run identifiers.
- Keep latest IDs visible in Runs dashboard state for quick reuse.

15. [ ] P1 - Add tests for session creation and token source UX
- HMI/API tests for create-session action and run_id propagation in UI state.
- Test system-token prefill path and manual override path.
- Test ID visibility and copy action presence.

Definition of done for Phase 4:
1. A user can execute the core operational flow from browser without curl.
2. All HMI actions map to API results without hidden client-only state.
3. Manual recovery is operational: queue visibility, CSV export, manual upload registration.
4. Polling/status/error UX is stable and understandable for mixed technical/non-technical users.

## Phase 4.1 Implementation Tasks (AI-First Decision Policy Rollout)

Goal:
- Make AI the primary decision-maker while keeping heuristic as recommendation-only fallback context.

1. [ ] P0 - Switch discovery decision engine to AI-first
- Final auto decision source: AI classifier.
- On per-candidate AI failure/timeout: final decision = `needs_review`.
- If AI unavailable at run start: run allowed, all candidates default to `needs_review`.

2. [ ] P0 - Add decision provenance fields to source model/API
- `final_decision`
- `decision_source` (`ai|fallback_heuristic|policy_no_ai|human_review`)
- `heuristic_recommendation`
- `heuristic_score`
- Preserve backward compatibility fields (`accepted`, `review_status`).

3. [ ] P0 - Update discovery/export contracts
- `/v1/discovery/runs/{run_id}/sources` returns decision-trace fields.
- Export payload includes decision provenance and heuristic recommendation context.

4. [ ] P1 - Add tests for AI-first policy
- AI success -> final decision from AI.
- AI runtime failure -> final `needs_review`, decision source `fallback_heuristic`.
- AI disabled/missing token -> final `needs_review`, decision source `policy_no_ai`.
- Human review override -> decision source `human_review`.

Definition of done for Phase 4.1:
1. Heuristic no longer performs final auto-accept/auto-reject in AI-first mode.
2. AI failures are handled gracefully with `needs_review` fallback.
3. API and exports expose decision provenance clearly.
4. Legacy consumers using `accepted`/`review_status` remain functional.
