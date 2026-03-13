# AGENTS.md

Operating guide for contributors and AI coding agents in this repository.

## 1. Documentation Precedence

When docs conflict, use this order:
1. `UI_SPEC.md` for UI/HMI contract and user-facing workflow
2. `CURRENT_SCOPE.md`
3. `ARCHITECTURE.md`
4. `PIPELINE_RULES.md`
5. `DATA_SCHEMA.md`
6. `BACKLOG.md`

If behavior changes, update affected docs in the same change.

## 2. Scope Guardrails

In scope:
1. Discovery, acquisition, parse/search, and task-first HMI improvements.
2. Reliability, observability, and workflow UX improvements aligned with current docs.

Out of scope unless explicitly requested:
1. Knowledge graph and topic clustering.
2. Productized entity/relationship extraction.
3. Automated narrative report generation.

## 3. Engineering Rules

1. Keep API layer thin; core behavior belongs in service modules.
2. Keep connector code isolated from decision/dedup logic.
3. Preserve deterministic behavior in ranking/dedup/query generation.
4. Preserve provenance and decision-source traceability.
5. Keep naming stable across API/model/UI.

## 4. UI/HMI Rules

1. Enforce single navigation model from `UI_SPEC.md`.
2. Keep task pages free of mandatory manual ID entry.
3. Keep technical controls in `Advanced` or explicit technical drawers.
4. Show status as text + color, never color-only.
5. Prefer `Session` over `Topic` in user-facing text unless a transition note explicitly says otherwise.
6. Treat `Library Export` as the canonical final workflow stage label.

## 5. Pipeline Rules Compliance

1. Follow `PIPELINE_RULES.md` for provider, citation, scoring, AI fallback, dedup, and iteration behavior.
2. Any threshold/policy change must update tests and docs together.

## 6. Testing Baseline

Before handoff:
1. `python3 -m compileall src tests`
2. `.venv/bin/pytest -q` (or `pytest -q` in active venv)

For changes touching policy/UI/workflows, add focused tests for:
1. decision behavior and review routing
2. dedup/merge behavior
3. task-first user flow and accessibility states

## 7. Change Management

1. Prefer small, reviewable commits.
2. Do not leave stale docs after behavior changes.
3. Keep archive docs in `archive/`; do not treat them as active source of truth.

## 8. Local Development Commands

Use repository scripts and documented commands only. Do not invent ad-hoc startup commands when a project script exists.

Canonical commands:
1. Start server: `./run_server.sh`
2. Server status: `./server_status.sh`
3. Restart server: `./restart_server.sh`
4. Health check: `curl -fsS http://127.0.0.1:8000/healthz`

Rules:
1. If `/healthz` succeeds, treat the server as already running and do not restart.
2. If port 8000 is occupied by a non-project process, do not kill it; report it.
3. Prefer repo-local scripts over inline shell chains.
