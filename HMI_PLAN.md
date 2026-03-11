# HMI Plan - UX Rebuild (Operator-First)

## Summary

Rebuild the HMI as an operator-first console with a search-first workflow.

Primary goals:
1. Fast triage loop across Discovery -> Acquisition -> Parse -> Manual Recovery.
2. Minimal manual ID handling in normal operations.
3. Clear status reasons and actionability for non-technical operators.

Decision lock:
1. Scope: full dashboard redesign (all tabs).
2. Primary interaction: search-first.
3. ID visibility: hidden by default.
4. Auth/key UX: system env by default + optional temporary manual override.
5. Rollout: hard replace (no side-by-side feature flag).

## UX Principles

1. Row-first actions:
- Operators act from the item row (`Approve`, `Reject`, `Retry`, `Manual Upload`) without copy/paste.

2. Human context before technical data:
- Show title, abstract snippet, source/provider, status reason first.
- Keep IDs in optional "technical details" panels.

3. One selected context:
- Selecting an item synchronizes context across sections (Discovery/Acquisition/Parse/Search/Recovery).

4. Explicit "why" messaging:
- Every non-terminal/problem state must expose reason code + readable explanation.

## Information Architecture

1. Global shell:
- Top bar:
  - global search
  - `Create New Session`
  - connection/health badges
  - auth mode indicator (`System token` / `Manual override`)
- Left navigation:
  - `Work Queue` (default)
  - `Discovery`
  - `Acquisition`
  - `Parse`
  - `Search`
  - `Manual Recovery`
  - `Runs & Logs` (advanced)

2. Work Queue (new default screen):
- Unified actionable items:
  - discovery `needs_review`
  - acquisition `failed|partial`
  - parse `failed`
- Inline actions:
  - `Approve`
  - `Reject`
  - `Retry`
  - `Open Source`
  - `Manual Upload`

3. Stage screens:
- Discovery:
  - create run
  - review queue
  - status filters and decision provenance
- Acquisition:
  - run counters
  - item outcomes + retry actions
  - send failures to recovery
- Parse:
  - parse run counters
  - document/chunk viewers
  - failed parse visibility
- Search:
  - query parsed corpus
  - open document/detail/source context
- Manual Recovery:
  - failed/partial list
  - legal candidate links
  - upload registration
- Runs & Logs:
  - explicit run IDs and technical diagnostics

## API Contract

Existing endpoints remain in use:
1. Discovery, review, export endpoints
2. Acquisition run/items/manifest/manual recovery endpoints
3. Parse run/documents/chunks endpoints
4. Search endpoint
5. AI filter settings endpoint

New endpoints for UX v2:
1. `GET /v1/work-queue`
- Aggregated actionable rows across phases.
- Query support:
  - `kind=discovery|acquisition|parse|all`
  - `status=needs_review|failed|partial|all`
  - `limit`
  - `offset` (or cursor in future iteration)

2. `GET /v1/search/global`
- Unified search across:
  - sources
  - runs
  - acquisition items
  - parsed documents
  - chunks

3. `GET /v1/system/status`
- Returns:
  - auth mode and readiness
  - AI filter readiness and warning
  - provider readiness summary

Contract additions for operator clarity:
1. list responses should include:
- `reason_code`
- `reason_text`
- `last_transition_at` (when available)

## ID Handling Policy

1. IDs are not required as primary user input in core workflows.
2. IDs are displayed only in:
- copy controls
- technical details drawers
- `Runs & Logs` section
3. Existing ID-based APIs remain unchanged for compatibility.

## Auth and Token UX

1. `AUTH_ENABLED=false`:
- hide token input controls
- show "No app token required"

2. `AUTH_ENABLED=true`:
- show current mode:
  - system token present
  - manual override active
- allow temporary manual override from UI
- never echo plaintext token in API responses

3. Provider keys:
- system env remains source of truth
- readiness is shown through `GET /v1/system/status`

## Polling and Runtime Behavior

1. Polling:
- 5s for active tabs/runs
- 15s when browser tab is hidden

2. Polling stop:
- stop for terminal statuses (`completed`, `failed`) with manual refresh option

3. State persistence:
- preserve filters/pagination/selected context on refresh

4. Error mapping:
- `400`: invalid request
- `401/403`: auth/config
- `404`: resource missing
- `409`: invalid run state
- `429`: rate limit
- `5xx`: retry guidance

## Accessibility and Clarity Requirements

1. All action buttons must have clear labels (no icon-only critical actions).
2. Keyboard focus order must allow operator workflow without mouse.
3. Status uses text + color (not color only).
4. Tables support truncation + expand/collapse for abstract/error text.

## Test and Acceptance Matrix

1. Functional:
- create session from UI without manual run-ID typing
- review from queue row (approve/reject)
- retry failed acquisition from row
- register manual upload from row context
- open parsed document and source context from search result

2. UX:
- IDs hidden by default in core views
- row-level actions complete without copy-paste IDs
- context sync works across sections

3. Security/config:
- auth disabled mode works with no token UI
- auth enabled mode validates token and supports manual override
- AI readiness/warnings visible and accurate

4. Regression:
- existing API clients remain compatible
- DOI-style source IDs remain reviewable
- manual recovery CSV/export/upload flows unchanged

## Implementation Order

1. Add backend aggregator/status endpoints (`work-queue`, `global-search`, `system-status`).
2. Implement new shell + global search + context store.
3. Build Work Queue and row action flows.
4. Refactor Discovery/Acquisition/Parse/Search/Recovery screens to row-first behavior.
5. Remove legacy ID-first forms from core paths.
6. Keep advanced technical views under `Runs & Logs`.
7. Hard replace old HMI route.

## Out of Scope for This UX Rebuild

1. Separate frontend repository/framework migration.
2. WebSocket event streaming.
3. Multi-user RBAC/login redesign.
4. Mobile-native app.
