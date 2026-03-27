# UPW Knowledge Miner

Release status: `0.11 beta`

Knowledge Miner is an end-to-end literature workflow for Ultrapure Water (UPW) in semiconductor manufacturing.

Current product includes:
1. Discovery across research/web sources with citation expansion and deduplication.
2. AI-first relevance decisioning with human review override.
3. Document acquisition (PDF-first, HTML fallback) with manual recovery tools.
4. Batch PDF upload with auto-match to pending acquisition items.
5. Full-text parse/chunk processing and search.
6. Operator-driven discovery runs with explicit citation expansion and live run progress in `Discover`.
7. Session-based HMI workflow with live updates, active-session switching, inline new-session creation, and per-session context persistence.
8. Direct Review -> Documents transition (no manual "send to documents" step).
9. Global bookmarks workspace with bookmark-to-session branching.
10. Session-scoped paper annotations in `Library Export`:
   - freeform tags
   - approved tags
   - AI suggested tags generated from parsed full text and kept separate until promoted
   - AI paper summaries from parsed full text
11. Run-context controls are kept in Advanced; task pages run on active session context.
12. UI design authority now follows the in-repo `UI_SPEC.md`.
13. Discover requires per-session research context; AI ranking and summaries use this context and store snapshots per session/run where applicable.

## Session And Citation Logic

Discovery and citation expansion follow different reuse rules inside a session:

1. Discovery dedup:
   - Human-reviewed papers (`accept`, `reject`, `later`) are remembered across the whole session, even if the saved session context changes.
   - Non-human-reviewed papers are deduplicated within the same saved session-context generation and may re-enter after a saved context change.
2. Citation expansion parent pool:
   - Parent candidates are selected from accepted papers across the whole session and deduplicated by paper identity.
   - Within one unchanged saved session context, citation expansion is incremental: already-expanded parents in that context are skipped and only newly eligible accepted papers remain.
   - After saving a changed session context, citation expansion is renewed for the new context generation and all accepted session papers become eligible again.
3. Operator expectation:
   - If you do not change the saved session context, repeated citation-expansion runs continue from the remaining unexpanded accepted papers.
   - If you save a changed session context, citation expansion starts fresh against the accepted session paper set for that new context.

## Library Tagging Logic

1. Manual tags remain authoritative:
   - `Freeform Tags` and `Approved Tags` change only through explicit operator actions.
2. AI suggested tags:
   - are generated from parsed full text in `Library`
   - stay separate from manual tags
   - do not auto-edit freeform or approved tags
3. Promotion:
   - suggested tags can be promoted to freeform tags one by one
   - suggested tags can be promoted to approved tags only if the tag already exists in the session approved-tag catalog
4. Session scope:
   - suggested AI tags, manual tags, and summaries are session-scoped annotation state

## Quick Start

```bash
cd /home/garik/Documents/git/knowledge-miner
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
./run_server.sh
```

Open:
1. API docs: `http://127.0.0.1:8000/docs`
2. HMI2: `http://127.0.0.1:8000/hmi2`
3. Legacy `/hmi` route redirects to `/hmi2`

Default bind behavior:
1. `./run_server.sh` binds to `0.0.0.0:8000` so the app is reachable from your LAN by default.
2. Local health checks still use `127.0.0.1:8000`.

Static asset cache strategy:
1. `/hmi2` injects a version query param (`?v=<build stamp>`) for `gui.js` and `gui.css`.
2. After restart/deploy, browsers fetch the updated frontend bundle automatically.

SQLite local repair behavior:
1. Local startup with `DB_AUTO_MIGRATE_ON_START=true` auto-creates missing feature tables such as bookmarks and paper annotations.
2. Startup also backfills known incorrect persisted discovery query counters in SQLite local/dev databases.
3. Automatic SQLite backups are stored under `./db_backups/` hourly for the live app database, keeping the latest 48 automatic snapshots by default.
4. `Advanced -> Database Restore` can create a manual backup on demand, list managed backups plus legacy backup files, snapshot the current DB before restore, and restore a selected backup in place.
5. The running server should use `knowledge_miner.db`; `pytest` must not target that file, and direct `python -c` or heredoc inspection now requires an explicit `DATABASE_URL`.
6. To inspect the currently resolved DB target safely, use:

```bash
cd /home/garik/Documents/git/knowledge-miner
.venv/bin/python scripts/show_db_target.py
```

## LAN Access (Another PC on Same Network)

Default `./run_server.sh` already binds to all interfaces. Equivalent manual start:

```bash
cd /home/garik/Documents/git/knowledge-miner
source .venv/bin/activate
uvicorn knowledge_miner.main:app --host 0.0.0.0 --port 8000 --reload
```

Find Linux host IP:

```bash
ip -4 addr show | rg -n "inet "
# or
hostname -I
```

From remote browser (replace `192.168.1.50` with host IP):
1. `http://192.168.1.50:8000/healthz`
2. `http://192.168.1.50:8000/docs`
3. `http://192.168.1.50:8000/hmi2`

Connectivity tests from remote terminal:

```bash
curl -i http://192.168.1.50:8000/healthz
curl -I http://192.168.1.50:8000/hmi2
```

Port conflict fix (`address already in use`):
1. Use a different port, for example:
```bash
uvicorn knowledge_miner.main:app --host 0.0.0.0 --port 8010 --reload
```
2. Then open `http://192.168.1.50:8010/hmi2`.

Firewall checklist (Linux `ufw`):

```bash
sudo ufw status
sudo ufw allow 8000/tcp
# or if using alternate port:
sudo ufw allow 8010/tcp
```

Security note:
1. Binding to `0.0.0.0` exposes API/HMI to your LAN.
2. If LAN is not fully trusted, set `AUTH_ENABLED=true` and use a strong API token before exposing it.

## Repository Documentation Layout

Source of truth docs:
1. `README.md` - entry point
2. `CURRENT_SCOPE.md` - what exists now, scope boundaries, near-term direction
3. `ARCHITECTURE.md` - system components, data flow, runtime boundaries
4. `UI_SPEC.md` - canonical user workflow and HMI behavior contract (`Discover`/`Review`/`Documents`/`Library Export`/`Bookmarks`/`Advanced`)
5. `PIPELINE_RULES.md` - discovery/decision/iteration rules
6. `DATA_SCHEMA.md` - data model and constraints
7. `BACKLOG.md` - active implementation tasks
8. `AGENTS.md` - contributor/AI operating rules
9. `MANUAL_LIVE_LOGIC_TEST.md` - live logic-test procedure and artifact expectations

Archived legacy docs are in `archive/`.
UI navigation/source-of-truth model is defined only in `UI_SPEC.md`; archived UI docs are explicitly deprecated.
Imported UX sources, including GUI Design Specification v1.1, are reflected in `UI_SPEC.md` and tracked via `BACKLOG.md`.

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
2. `USE_SEMANTIC_SCHOLAR=false` (set `true` to enable Semantic Scholar connector)
3. `BRAVE_API_KEY=<key>`
4. `SEMANTIC_SCHOLAR_API_KEY=<key>` (optional; used only when `USE_SEMANTIC_SCHOLAR=true`)

## Logs and Runtime State

1. Persistent logs default to `./logs/knowledge_miner.log`.
2. Runtime lock files are stored under `./runtime/`.
3. Artifacts are stored under `./artifacts/` and reused across sessions by DOI first, then source URL when available.

## Maintainability Guardrails

Run file-size checks:

```bash
python scripts/check_file_sizes.py
```

Guardrail policy:
1. JS/TS warning/fail thresholds and Python warning/fail thresholds are defined in `config/file_size_guardrails.json`.
2. Temporary exceptions must be listed in the same config with an explicit reason.
3. Exceptions are for active refactors only and should be removed once split work is complete.

## Current Priorities

Use `BACKLOG.md` for authoritative task priority and execution status.
