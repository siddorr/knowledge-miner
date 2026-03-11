# UPW Literature Discovery Engine

Automated literature discovery for Ultrapure Water (UPW) systems in semiconductor manufacturing.

## v1 Scope

In scope:
1. Search query execution
2. Citation expansion (forward and backward)
3. Abstract retrieval and relevance scoring
4. Deduplication
5. Iterative query generation
6. Corpus storage and export

Implemented extension beyond v1 baseline:
1. Phase 2 document acquisition (PDF-first, HTML fallback)
2. Acquisition API and manifest artifact generation

Still out of scope:
1. Full-text parsing
2. Knowledge graph and clustering
3. UI for manual report generation

## v1 Targets

1. 2000+ accepted unique sources within 6 iterations
2. Precision@50 >= 0.80 (human spot check)
3. Accepted duplicate rate <= 2%

## Source of Truth

Primary implementation contract: `V1_SPEC.md`.

Supporting docs:
1. `ARCHITECTURE.md`
2. `SEARCH_ENGINE.md`
3. `CITATION_EXPANSION.md`
4. `ABSTRACT_FILTER.md`
5. `ITERATION_PROCESS.md`
6. `DATA_SCHEMA.md`
7. `DEVELOPMENT_PLAN.md`
8. `HMI_PLAN.md`

## HMI UX Rebuild Direction

HMI is being rebuilt as an operator-first interface with search-first workflow:
1. Primary landing: `Work Queue` with row-level actions.
2. IDs are hidden by default in normal operations.
3. Global search drives context across Discovery/Acquisition/Parse/Recovery.
4. Technical IDs remain available in advanced diagnostics views.

Decision and implementation details are defined in `HMI_PLAN.md`.

## Quick Start

```bash
cd /home/garik/Documents/git/knowledge-miner
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn knowledge_miner.main:app --reload
```

Auth modes:
1. Default local/internal mode: auth disabled (`AUTH_ENABLED=false`), no header required.
2. Secured mode: set `AUTH_ENABLED=true`, then send `Authorization: Bearer <API_TOKEN>`.

Local runtime safety defaults:
1. `CLEAN_ON_STARTUP=true` (development default) clears stale runtime lock files under `RUNTIME_STATE_DIR` on app startup.
2. Startup acquires a single-instance runtime lock to avoid duplicate background workers in local reload workflows.

## Real Provider Search

To run against real providers instead of mock data:

```bash
cd /home/garik/Documents/git/knowledge-miner
cp .env.example .env
# edit .env and set BRAVE_API_KEY / SEMANTIC_SCHOLAR_API_KEY
set -a
source .env
set +a
```

Required settings:
1. `USE_MOCK_CONNECTORS=false`
2. `BRAVE_API_KEY=<your_key>`
3. `SEMANTIC_SCHOLAR_API_KEY=<your_key>` (optional but recommended)

Note:
1. OpenAlex does not require an API key.
2. Real-provider mode requires host network access (sandboxed runs may not have DNS/internet).

## Persistent Logs

The service writes persistent rotating logs to file (and stdout) using:
1. `LOG_DIR` (default `./logs`)
2. `LOG_FILE` (default `knowledge_miner.log`)
3. `LOG_LEVEL` (default `INFO`)
4. `LOG_MAX_BYTES` (default `10485760`)
5. `LOG_BACKUP_COUNT` (default `5`)

Example:
1. `tail -f /home/garik/Documents/git/knowledge-miner/logs/knowledge_miner.log`

## Discovery Visibility

For operations dashboard and review workflows, discovery visibility is being extended as follows:

1. `GET /v1/discovery/runs/{run_id}` will include:
- `seed_queries` (original search words used to start the run)

2. `GET /v1/discovery/runs/{run_id}/sources` will support:
- `status=accepted|rejected|needs_review|all`
- Backward-compatible default remains accepted-only when `status` is omitted

3. Dashboard default source view:
- `accepted + needs_review`
- with quick toggles for `accepted/rejected/needs_review/all`

## AI-First Filtering Policy

1. AI is the primary source of automatic relevance decisions.
2. Heuristic scoring is always computed, but used as recommendation metadata.
3. If AI call fails for a candidate, final decision is `needs_review` (not auto-accept/reject).
4. If AI is unavailable at run start (`USE_AI_FILTER=false` or missing `AI_API_KEY`), run is still allowed and candidates default to `needs_review` with heuristic recommendations.
5. Human review remains the final authority (`POST /v1/sources/{source_id}/review`).

## Runtime AI Filter Control

Operators can control AI filter mode at runtime from HMI (no shell/env edit required).

API endpoints:
1. `GET /v1/settings/ai-filter`
2. `POST /v1/settings/ai-filter`

Notes:
1. Settings apply to newly created discovery runs.
2. Existing/running runs keep their already-saved `ai_filter_active` mode.
3. API key is never returned in full; response includes only masked/boolean key state.

## Operator-First HMI APIs

New operator workflow APIs:
1. `GET /v1/work-queue` for actionable cross-phase queue (`needs_review`, `failed`, `partial`).
2. `GET /v1/search/global` for global typed search across runs/sources/acquisition/parse/chunks.
3. `GET /v1/system/status` for auth mode, AI readiness, and provider readiness summary.
