# UI Specification

Status: Implemented baseline (task-first model)

## Navigation (Single Canonical Model)

Top-level navigation is fixed to:
1. `Dashboard`
2. `Discover`
3. `Review`
4. `Documents`
5. `Search`
6. `Advanced`

No stage-first navigation is valid for primary UX.

## Global Shell

1. Header with first-time flow guidance.
2. Poll/system status strip.
3. Task navigation.
4. Main task content.
5. Advanced diagnostics/settings section.

## Page Contracts

## Dashboard

Goal:
1. Show next action in under 5 seconds.

Must include:
1. `Run Discovery` action (queries, max iterations, AI mode).
2. Attention summary counts:
- sources needing review
- download issues
- parse errors
3. Recent discovery summary.

## Discover

Goal:
1. Minimal run-start and summary view.

Must include:
1. Latest run summary.
2. `Export sources_raw` action.
3. Technical drawer for run overrides/filters.

Default rule:
1. No raw IDs in primary surface.

## Review

Goal:
1. Fast row-level accept/reject decisions.

Must include:
1. Source table with title, abstract, decisions, reason context.
2. Expand/collapse abstract behavior.
3. Status text + color badge.
4. Pagination.

Primary action:
1. `Accept` / `Reject` calls review API directly from row context.

## Documents

Goal:
1. Resolve acquisition failures without CLI.

Must include:
1. Failed/partial download queue.
2. Actions per row:
- `Retry`
- `Upload PDF`
- `Open source`
3. CSV export.
4. Manual upload form.
5. Technical drawer for run/source overrides.

## Search

Goal:
1. Simple search-first experience for end users.

Must include:
1. Single query input and result list.
2. Row actions: `Doc`, `Text`, `Source`.
3. Technical drawer for parse-run override and detailed payloads.

## Advanced

Goal:
1. Isolate technical complexity from task pages.

Contains:
1. Auth/token controls.
2. AI runtime settings.
3. Run lookup and filters.
4. Global technical search.
5. ID copy controls.
6. Low-level start/export/diagnostic controls.

## Behavior Rules

1. Task pages avoid mandatory manual ID entry.
2. IDs are shown in `Advanced` and technical drawers only.
3. Every important state change has clear text feedback.
4. Polling must preserve context/filter/pagination state.
5. Error states must be actionable and visible inline.
6. HMI emits fire-and-forget telemetry events for click/change/input/submit/navigate actions.
7. Telemetry never blocks user actions and redacts sensitive values by default.

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

1. First-time user completes `Dashboard -> Discover -> Review -> Documents -> Search` without docs.
2. No manual ID copy/paste required in default flow.
3. Advanced controls remain available without cluttering task pages.
4. Navigation model remains single and consistent across docs/tests/UI.
