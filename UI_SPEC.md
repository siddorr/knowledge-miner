# UI Specification

Status: Active source of truth (aligned with `Knowledge_Miner_GUI_Spec.docx` imported on 2026-03-11)

## Navigation (Single Canonical Model)

Top-level navigation is fixed to:
1. `Build`
2. `Review`
3. `Documents`
4. `Library`
5. `Advanced`

No stage-first navigation is valid for primary UX.

## Global Shell

1. Global status strip on every page:
- project/corpus
- active topic
- pending review
- accepted waiting for documents
- document failures
- last run state
- one next-action button
2. Task navigation.
3. Main task content.
4. Optional right details panel.

## Page Contracts

## Build

Goal:
1. Planning and iteration workspace.

Must include:
1. Topic list and create-topic flow.
2. Tabs: `Add Sources`, `Queries`, `Runs`.
3. Manual source addition as first-class action (DOI/URL/citation/bulk).
4. Query management and run history.
5. Details panel for selected topic/run.
6. Discovery controls:
- `Run One Iteration` (single-step execution)
- `Run Next Citation Iteration` (explicit, not automatic)
- `Search New Keywords` (available globally in shell, usable from any workflow stage)

## Review

Goal:
1. Dedicated decision workspace.

Must include:
1. Queue filters: `Pending`, `Accepted`, `Rejected`, `Later`.
2. Row actions: `Accept`, `Reject`, `Later`.
3. Batch actions for `Accept Selected` / `Reject Selected` only (no manual transfer step).
4. Details/preview panel with copy buttons.
5. Queue auto-loads on Review page entry and on filter/run-context changes.
6. Optional manual refresh is technical-only (`Refresh Review Queue`), not required for default flow.

Primary action:
1. `Accept` / `Reject` calls review API directly from row context.

## Documents

Goal:
1. Resolve acquisition failures without CLI.

Must include:
1. Acquisition queue with `Awaiting`, `Acquired`, `Failed`, `Manual Recovery`.
2. Queue can be populated directly from approved review sources before acquisition run creation.
3. Primary action label: `Process Approved Docs`.
4. Secondary visible action: `View Issues`.
5. Batch upload action: `Upload PDF Batch` with match result summary (`matched/unmatched/ambiguous`).
6. Actions per row: one recommended next step based on status (`Upload PDF`, `Manual Complete`, `Acquired`, `Awaiting processing`) and optional `Open source`.
7. `Select All` / `Deselect All` controls for checkbox rows.
8. Technical actions (`Retry Failed`, `Copy Selected DOI/URL`, `Export CSV`) are hidden under `More`.

## Library

Goal:
1. Combined corpus browser and search.

Must include:
1. Empty query state behaves as corpus browser.
2. Query state behaves as retrieval/search.
3. Result preview and copy actions.
4. Topic/year/docs/parsed filters.

## Advanced

Goal:
1. Isolate technical complexity from task pages.

Contains:
1. Runs and raw statuses.
2. Logs and diagnostics.
3. Raw records/pipeline status/settings.
4. ID-level operations and low-level controls.

## Behavior Rules

1. Task pages avoid mandatory manual ID entry and raw IDs.
2. IDs are shown in `Advanced` and technical drawers only.
3. Every important state change has clear text feedback.
4. Polling must preserve context/filter/pagination state.
5. Error states must be actionable and visible inline.
6. HMI emits fire-and-forget telemetry events for click/change/input/submit/navigate actions.
7. Telemetry never blocks user actions and redacts sensitive values by default.
8. Copy buttons are required for DOI/title/URL/citation/query/error and selected DOI/URL sets.
9. Status freshness text (`Last update`) and progress bars are visible during long-running operations.
10. Run state is exposed with canonical values: `idle`, `queued`, `running`, `waiting_user`, `completed`, `failed`.
11. Pagination controls are hidden/disabled unless multi-page navigation is applicable.
12. Status strip must show explicit auth wording: `Auth: Yes` or `Auth: No`.
13. Live updates use server push with reconnect and bounded fallback refresh when disconnected/active.
14. Background refresh uses leader-tab model to avoid duplicate polling across multiple tabs.
15. Duplicate in-flight GET requests for identical endpoint+params are deduplicated per tab.
16. Advanced page includes session controls: `Save Session`, `Load Session`, history list, delete, and auto-restore toggle.

## State and Status Presentation

Primary status labels:
1. `Ready` (green)
2. `In Progress` (yellow)
3. `Needs Action` (red)

Accessibility rule:
1. Status cannot rely on color alone.

## API Dependencies

Task pages depend on:
1. `GET /v1/work-queue`
2. `GET /v1/system/status`
3. discovery run/status/source APIs
4. `POST /v1/sources/{source_id}/review`
5. acquisition/manual recovery APIs
6. parse/search APIs
7. `GET /v1/search/global` (Advanced)

## Acceptance Criteria

1. First-time user completes `Build -> Review -> Documents -> Library` without external docs.
2. No manual ID copy/paste required in default flow.
3. Advanced controls remain available without cluttering task pages.
4. Navigation model remains single and consistent across docs/tests/UI.
