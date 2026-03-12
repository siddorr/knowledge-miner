# UPW Knowledge Miner

Knowledge Miner is an end-to-end literature workflow for Ultrapure Water (UPW) in semiconductor manufacturing.

Current product includes:
1. Discovery across research/web sources with citation expansion and deduplication.
2. AI-first relevance decisioning with human review override.
3. Document acquisition (PDF-first, HTML fallback) with manual recovery tools.
4. Batch PDF upload with auto-match to pending acquisition items.
5. Full-text parse/chunk processing and search.
6. Operator-driven discovery iterations (`Run One Iteration`, explicit citation iteration, keyword search on demand).
7. Task-first HMI workflow for operators with live updates (SSE), visible progress/freshness state, explicit auth badges, conditional pagination controls, and auto-loading Review queue.
8. Direct Review -> Documents transition (no manual "send to documents" step).

## Quick Start

```bash
cd /home/garik/Documents/git/knowledge-miner
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn knowledge_miner.main:app --reload
```

Open:
1. API docs: `http://127.0.0.1:8000/docs`
2. HMI: `http://127.0.0.1:8000/hmi`

## Repository Documentation Layout

Source of truth docs:
1. `README.md` - entry point
2. `CURRENT_SCOPE.md` - what exists now, scope boundaries, near-term direction
3. `ARCHITECTURE.md` - system components, data flow, runtime boundaries
4. `UI_SPEC.md` - canonical user workflow and HMI behavior contract (Build/Review/Documents/Library/Advanced)
5. `PIPELINE_RULES.md` - discovery/decision/iteration rules
6. `DATA_SCHEMA.md` - data model and constraints
7. `BACKLOG.md` - active implementation tasks
8. `AGENTS.md` - contributor/AI operating rules

Archived legacy docs are in `archive/`.
UI navigation/source-of-truth model is defined only in `UI_SPEC.md`; archived UI docs are explicitly deprecated.
Imported UX source (`Downloads/Knowledge_Miner_GUI_Spec.docx`) is reflected in `UI_SPEC.md` and tracked via `BACKLOG.md` Phase 4.3 tasks.

## Runtime Modes

Auth modes:
1. Local/internal default: `AUTH_ENABLED=false` (no token required).
2. Secured mode: `AUTH_ENABLED=true` and `Authorization: Bearer <API_TOKEN>`.

AI filtering:
1. AI-first policy is runtime-configurable via API/HMI settings.
2. If AI is unavailable, candidates route to `needs_review`.

## Real Provider Search

To use real providers instead of mock connectors:

```bash
cp .env.example .env
# set USE_MOCK_CONNECTORS=false and provider keys
set -a
source .env
set +a
```

Typical settings:
1. `USE_MOCK_CONNECTORS=false`
2. `BRAVE_API_KEY=<key>`
3. `SEMANTIC_SCHOLAR_API_KEY=<key>` (optional but recommended)

## Logs and Runtime State

1. Persistent logs default to `./logs/knowledge_miner.log`.
2. Runtime lock files are stored under `./runtime/`.
3. Artifacts are stored under `./artifacts/`.

## Current Priorities

Use `BACKLOG.md` for authoritative task priority and execution status.
