# HMI Plan - Ops Dashboard (FastAPI-hosted)

## Summary

Build a FastAPI-served web HMI focused on operations for mixed users.

Primary intent:
1. Guide users through Discovery -> Acquisition -> Parse -> Search.
2. Expose operational status/errors clearly.
3. Support manual recovery for non-downloaded documents.

Acceptance target:
1. Task completion + API parity (UI actions map 1:1 to API behavior).

## Scope

In scope:
1. FastAPI-hosted web UI (single deployable app).
2. Full navigation:
- Runs dashboard
- Discovery details
- Acquisition details
- Parse details
- Search explorer
- Manual recovery queue
3. Auto-refresh polling.
4. UI actions:
- Start runs
- Retry failed-only runs
- Review decisions
- Export artifacts/lists
- Register manual uploads

Out of scope (v1 HMI):
1. Separate frontend repo/app.
2. WebSocket push updates.
3. New backend login/session system.
4. Admin config editing UI.

## Required APIs

Existing APIs used:
1. `POST /v1/discovery/runs`
2. `GET /v1/discovery/runs/{run_id}` (extended to include `seed_queries`)
3. `GET /v1/discovery/runs/{run_id}/sources` (extended with `status` filter)
4. `POST /v1/sources/{source_id}/review`
5. `GET /v1/exports/sources_raw`
6. `POST /v1/acquisition/runs`
7. `GET /v1/acquisition/runs/{acq_run_id}`
8. `GET /v1/acquisition/runs/{acq_run_id}/items`
9. `GET /v1/acquisition/artifacts/{artifact_id}`
10. `GET /v1/acquisition/runs/{acq_run_id}/manifest`
11. `POST /v1/parse/runs`
12. `GET /v1/parse/runs/{parse_run_id}`
13. `GET /v1/parse/runs/{parse_run_id}/documents`
14. `GET /v1/parse/runs/{parse_run_id}/chunks`
15. `GET /v1/parse/documents/{document_id}`
16. `GET /v1/parse/documents/{document_id}/text`
17. `POST /v1/search`

Phase 2.1 APIs required for manual recovery:
1. `GET /v1/acquisition/runs/{acq_run_id}/manual-downloads`
2. `GET /v1/acquisition/runs/{acq_run_id}/manual-downloads.csv`
3. `POST /v1/acquisition/runs/{acq_run_id}/manual-upload`

Discovery visualization API additions:
1. `GET /v1/discovery/runs/{run_id}` returns:
- `seed_queries: list[str]`
2. `GET /v1/discovery/runs/{run_id}/sources` supports:
- `status=accepted|rejected|needs_review|all`
- Backward compatibility default: accepted-only when omitted

## UI Information Architecture

1. Runs Dashboard
- Unified run list across phases.
- Filters: phase, status, date, keyword/run id.
- Actions: create run, open details, retry failed-only.

2. Discovery Detail
- Run metrics and AI filter warning.
- Sources list with status filters (`accepted/rejected/needs_review/all`) and review actions.
- Per-source decision trace fields:
  - `final_decision`
  - `decision_source`
  - `heuristic_recommendation`
  - `heuristic_score`
- Export `sources_raw.json`.

3. Acquisition Detail
- Progress/counters and item statuses.
- Error visibility and selected URL trace.
- Manifest/artifact links.

4. Parse Detail
- Parse status/counters/error summary.
- Parsed documents list and chunk list.

5. Search Explorer
- Query input, result ranking, snippet view.
- Links back to document/chunk/source context.

6. Manual Recovery
- Failed/partial/skipped queue.
- CSV export.
- Manual upload registration.

## UX/Behavior Defaults

1. No dedicated auth screen in v1; behavior follows API auth mode.
- `AUTH_ENABLED=false`: token controls are hidden and requests run without auth header.
- `AUTH_ENABLED=true`: token controls are shown with system-token prefill + manual override.
2. Polling intervals:
- 5s when page is visible and run is active.
- 15s when hidden.
3. Polling stops for terminal statuses (`completed`, `failed`) unless user refreshes.
4. Error mapping in UI:
- 400 invalid request
- 401/403 auth/config issue
- 404 resource missing
- 409 state conflict
- 429 rate limited
- 5xx retry guidance
5. Discovery table default status view: `accepted + needs_review`.
6. Quick toggles available in UI: `accepted | rejected | needs_review | all`.
7. Fallback/policy decisions (`decision_source=fallback_heuristic|policy_no_ai`) are visually marked as review-required.
8. Runtime AI filter settings are operator-controlled in HMI (`Load AI Settings` / `Save AI Settings`) and apply to new discovery runs.
9. Review actions must support DOI-style source IDs with `/` (path-safe review routing).

## Implementation Order

1. Backend parity first:
- Phase 2.1 manual recovery endpoints + schemas + tests.
2. HMI shell:
- FastAPI static assets + base route.
3. Read-only dashboards:
- Runs and detail pages with polling.
4. Mutating actions:
- Review, retry, exports.
5. Recovery workflows:
- Manual downloads list, CSV export, upload registration.
6. End-to-end tests:
- Discovery -> Acquisition -> Parse -> Search
- Recovery flow with failed downloads.

## Test/Acceptance Matrix

1. Unit tests:
- Polling state transitions.
- Status and error mapping.
2. API integration tests:
- Manual recovery endpoint contracts.
- CSV export semantics.
- Manual upload validation/provenance.
 - Discovery source `status` filter combinations.
 - Run status response includes `seed_queries`.
 - Discovery source response includes decision-trace fields (`final_decision`, `decision_source`, `heuristic_recommendation`, `heuristic_score`).
3. End-to-end scenarios:
- Full happy path run.
- Partial/failure recovery path.

## Assumptions

1. Primary deployment is local/internal at first.
2. Auth is optional by deployment mode (`AUTH_ENABLED`), with UI parity for both modes.
3. API-first behavior is authoritative; UI must not infer unsupported states.
