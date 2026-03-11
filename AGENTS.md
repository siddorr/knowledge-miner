# AGENTS.md

This file is the operating guide for AI coding agents working in this repository.

## 1. Start Here (Mandatory)

Before making changes:
1. Read `V1_SPEC.md` first.
2. Confirm task scope against implemented phases:
   - Discovery pipeline (v1 baseline)
   - Acquisition pipeline (Phase 2 extension)
   - Parse/search pipeline (Phase 3 extension)
   - Task-first HMI (Phase 4.2)
3. Write a short task plan and execute in small, testable steps.
4. Prefer editing existing files over adding new abstractions.

Do not work from memory when repo docs define behavior.

## 2. Source of Truth

When docs conflict, use this precedence:
1. `V1_SPEC.md`
2. `DATA_SCHEMA.md`
3. `ARCHITECTURE.md`
4. module docs (`SEARCH_ENGINE.md`, `CITATION_EXPANSION.md`, `ABSTRACT_FILTER.md`, `ITERATION_PROCESS.md`)

If conflicts are found, update docs in the same change.

## 3. v1 Scope Guardrails

In scope:
- Search connectors
- Citation expansion
- Abstract scoring/filtering
- Deduplication
- Iterative query refinement
- Corpus export
- Acquisition run execution (PDF-first with HTML fallback, manifest, retries/resume)

Out of scope:
- Knowledge graph
- Topic clustering
- Automated report generation

Do not introduce out-of-scope features unless explicitly requested.

UI/HMI features are in scope when they align with:
1. `HMI_PLAN.md` (navigation/UX behavior)
2. `UI_UX_DETAILED_SPEC.md` (detailed UI contract)
3. existing API contracts and tests

## 4. Architecture Rules

1. Keep deterministic behavior in core pipeline logic.
2. Keep connectors isolated from scoring/dedup logic.
3. Keep API layer thin; business logic belongs in service modules.
4. Persist checkpoints per iteration.
5. Use canonical IDs exactly per `V1_SPEC.md`.

## 5. Data and Schema Discipline

1. Treat schema changes as high-impact.
2. Keep required/optional field behavior explicit.
3. Avoid silent data coercion.
4. Preserve provenance fields whenever records are merged.
5. Add schema versioning to exported artifacts.

## 6. Code Quality Standards

1. Keep code ASCII unless file already uses Unicode.
2. Avoid non-informative comments.
3. Prefer small pure functions for scoring, dedup, and query generation.
4. Handle failures explicitly with typed errors or clear HTTP error codes.
5. Keep naming stable and predictable across modules.

## 7. Testing Requirements

For logic changes, add/adjust tests for:
1. scoring thresholds and review classification
2. dedup precedence and merge behavior
3. iteration stop-condition calculation
4. export schema conformance
5. acquisition status transitions and manifest/artifact parity

Minimum validation before handoff:
1. `python3 -m compileall src tests`
2. `.venv/bin/pytest -q` (or `pytest -q` in active venv)

## 8. API Contract Rules

1. Do not break existing endpoint shapes without updating docs and tests.
2. Keep error response semantics consistent (`400/401/404/409/429/500` per spec).
3. Validate request payloads strictly.
4. Keep auth checks on all non-health endpoints.

## 9. Operational Expectations

1. Log with run and iteration context.
2. Keep retry behavior bounded and explicit.
3. Avoid unbounded concurrency.
4. Make export paths deterministic: `./artifacts/{run_id}/sources_raw.json`.

## 10. Change Management

When you learn something useful for future agent tasks:
1. Update this file in the same PR/commit.
2. Keep updates concrete and repository-specific.
3. Remove stale instructions rather than layering duplicates.
