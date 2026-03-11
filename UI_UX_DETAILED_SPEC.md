# Knowledge Miner UI/UX Detailed Specification

Version: 2.0  
Scope: Task-first HMI at `/hmi`  
Audience: Product owner, operators, frontend/backend developers, QA

Note:
1. The Phase 4.2 implementation uses task-first navigation (`Dashboard`, `Discover`, `Review`, `Documents`, `Search`, `Advanced`).
2. Older operator-stage labels (`Work Queue`, `Discovery`, `Acquisition`, `Parse`, `Manual Recovery`) are now technical internals placed under `Advanced`.

---

## 1. Product UX Goals

1. Operators complete core triage actions without CLI/API calls.
2. UI is row-first (act directly from queue/stage rows).
3. IDs are available but not primary interaction drivers.
4. Every non-happy status has clear explanation (`reason_code` + readable text).
5. Context is synchronized globally across sections.
6. Polling stays informative but non-disruptive.

---

## 2. Information Architecture

Primary navigation:
1. Work Queue (default)
2. Discovery
3. Acquisition
4. Parse
5. Search
6. Manual Recovery
7. Runs & Logs (Advanced)

Global shell zones:
1. Header (title + purpose)
2. Auth strip (token mode, auth state)
3. Poll/system strip (poll state + readiness badges)
4. AI settings strip
5. Global search strip
6. Context panel (selected run/source/document context)
7. Main content section

---

## 3. High-Level Page Pseudographics

```text
+----------------------------------------------------------------------------------+
| HEADER: Knowledge Miner Ops Dashboard                                            |
| Operator-first workflow for review/recovery/triage                               |
+----------------------------------------------------------------------------------+
| AUTH BAR: [API key input] [Save Key] Auth: {disabled|system|manual}            |
|           Hint: {No token required|System token mode|Manual override mode}      |
+----------------------------------------------------------------------------------+
| POLL BAR: Polling: {Idle|Auto-refreshing every 5s|Stale data...}               |
|           System badges: auth:{...} | ai:{...} | brave:{...} | s2:{...}        |
+----------------------------------------------------------------------------------+
| AI SETTINGS: enabled [true/false] model [....] base_url [....] key [*****]      |
|              [Load AI Settings] [Save AI Settings] state message                 |
+----------------------------------------------------------------------------------+
| GLOBAL SEARCH: [query.................................] limit [25] [Search]      |
|                results state                                                      |
+----------------------------------------------------------------------------------+
| CONTEXT PANEL: {JSON context for selected row/item across sections}              |
+----------------------------------------------------------------------------------+
| NAV: [Work Queue] [Discovery] [Acquisition] [Parse] [Search] [Manual] [Advanced]|
+----------------------------------------------------------------------------------+
| MAIN CONTENT (selected section)                                                   |
+----------------------------------------------------------------------------------+
```

---

## 4. Default Landing: Work Queue

Purpose:
1. Show all actionable items from discovery/acquisition/parse.
2. Support immediate action from queue row.

Data source:
1. `GET /v1/work-queue`

Queue row model:
1. `phase`
2. `status`
3. `title` (or fallback identifier)
4. `reason_text` (or `reason_code`)
5. row actions

Pseudographics:

```text
+----------------------------------------------------------------------------------+
| Work Queue                                                                       |
| Actionable queue across discovery, acquisition, and parse                        |
| [Refresh Queue] items=N                                                          |
+----------------+------------+---------------------------+-------------------------+
| Phase          | Status     | Title                     | Reason                  |
+----------------+------------+---------------------------+-------------------------+
| discovery      | needs_review | "UPW process..."       | AI/heuristic needs ...  |
|   Actions: [Approve] [Reject]                                                 |
+----------------+------------+---------------------------+-------------------------+
| acquisition    | failed       | "Paper X"              | Source appears paywalled|
|   Actions: [Retry Acquisition] [Manual Recovery]                               |
+----------------+------------+---------------------------+-------------------------+
| parse          | failed       | "Doc Y"                | Source retrieval failed  |
|   Actions: [Retry Parse]                                                      |
+----------------------------------------------------------------------------------+
```

Interaction rules:
1. Clicking action executes API immediately.
2. On success, queue refreshes and context panel updates.
3. On failure, inline error shown in queue error area.
4. Queue refresh does not clear selected context.

---

## 5. Discovery UX

Purpose:
1. Start sessions.
2. Review and classify sources.
3. Understand why a source is surfaced.

Core controls:
1. Create session form (`seed queries`, `max iterations`, `ai mode`)
2. Source status filter (`accepted|rejected|needs_review|all`)
3. Export `sources_raw`
4. Inline row actions: `Approve`, `Reject`, `Use Context`
5. Abstract `Expand/Collapse`

Pseudographics:

```text
+----------------------------------------------------------------------------------+
| Discovery                                                                        |
| Create Session: [seeds.....] [max_iter] [ai mode] [Create]                      |
| Guidance: Run created: run_xxx. Status filter currently: accepted. ...           |
| [Export sources_raw]                                                             |
| Run: [run_xxx] Status filter:[needs_review] Limit:[50] [Load]                    |
| [Prev] [Next] offset/limit/total                                                 |
+----------------------------------------------------------------------------------+
| Metrics JSON (seed_queries, ai warning, totals, etc.)                            |
+----------------------------------------------------------------------------------+
| ID | Title | Abstract | Status | Score | Type | Source | Actions                |
| .. | ....  | ... [Expand] | needs_review | ... | ... | ... | [Approve][Reject] |
|                                                              [Use Context]       |
+----------------------------------------------------------------------------------+
```

Behavior:
1. Row action updates status optimistically then refreshes authoritative data.
2. Review API supports DOI-like IDs with `/` safely.
3. Create-session flow syncs run ID into discovery and advanced runs lookup.

---

## 6. Acquisition UX

Purpose:
1. Monitor full-text retrieval outcomes.
2. Route failures to manual recovery quickly.

Row actions:
1. `Manual Recovery` (opens/syncs recovery section)
2. `Prefill Upload` (fills upload source context)

Status messaging:
1. Use `reason_code` in queue/recovery to explain failure source.
2. Include legal candidate provenance in recovery flow.

Pseudographics:

```text
+----------------------------------------------------------------------------------+
| Acquisition                                                                      |
| Start: discovery_run [run_x] retry_failed_only [true/false] [Start Acquisition] |
| [Export acquisition manifest]                                                    |
| acq_run [acq_x] limit [50] [Load]                                                |
+----------------------------------------------------------------------------------+
| Item ID | Source ID | Status | Attempts | Selected URL | Error | Actions        |
| ...     | ...       | failed | 2        | ...          | http_403 | [Manual ...]|
|                                                         [Prefill Upload]        |
+----------------------------------------------------------------------------------+
```

---

## 7. Parse UX

Purpose:
1. Inspect parse outcomes quickly.
2. Open document detail/text without ID re-entry.

Row actions:
1. `Detail`
2. `Text`

Pseudographics:

```text
+----------------------------------------------------------------------------------+
| Parse                                                                            |
| Start: acq_run [acq_x] retry_failed_only [...] [Start Parse]                    |
| parse_run [parse_x] docs_limit [50] chunks_limit [50] [Load]                    |
+----------------------------------------------------------------------------------+
| Documents table ... [Detail] [Text]                                              |
+----------------------------------------------------------------------------------+
| Selected Document Detail (JSON)                                                   |
+----------------------------------------------------------------------------------+
| Selected Document Full Text                                                       |
+----------------------------------------------------------------------------------+
```

---

## 8. Search UX

Purpose:
1. Find evidence/snippets.
2. Jump directly to document/text/source context.

Actions:
1. `Doc`
2. `Text`
3. `Source`

Context sync on `Source`:
1. Auto-populate discovery run context.
2. Set discovery filter to `all`.
3. Keep global context panel updated.

---

## 9. Manual Recovery UX

Purpose:
1. Resolve failed/partial acquisition items.
2. Use legal candidate links and reason codes.

Row data:
1. item/source/status
2. attempt count
3. URLs
4. `manual_url_candidates`
5. `legal_candidates` (with source/rank)
6. `reason_code`

Actions:
1. `Prefill Upload`
2. CSV export
3. Upload registration

Pseudographics:

```text
+----------------------------------------------------------------------------------+
| Manual Recovery                                                                  |
| acq_run [acq_x] limit [50] [Load Queue] [Export CSV]                            |
+----------------------------------------------------------------------------------+
| Item | Source | Status | Attempts | Title | URLs... | Reason | Action           |
| ...  | ...    | failed | 2        | ...   | ...     | paywalled | [Prefill ...] |
+----------------------------------------------------------------------------------+
| Upload: source_id [prefilled] file [choose] [Register Manual Upload]            |
+----------------------------------------------------------------------------------+
```

---

## 10. Runs & Logs (Advanced)

Purpose:
1. Keep explicit ID-centric technical workflows out of operator-first default flow.
2. Preserve diagnostics and direct lookups for power users.

Contains:
1. Run lookup by phase + ID
2. Run filter tables
3. latest IDs copy controls

---

## 11. Auth UX Parity

`AUTH_ENABLED=false`:
1. No API token required.
2. Key input/save controls hidden.
3. UI explicitly displays: `No app token required`.

`AUTH_ENABLED=true`:
1. Token mode visible (`system token` vs `manual override`).
2. Manual override supported in session.
3. Secrets never rendered back in plaintext from backend.

---

## 12. Polling Contract

Rules:
1. Active visible tab: 5s.
2. Hidden tab: 15s.
3. Terminal runs: stop auto-polling for that workflow.
4. Poll failures: show stale-state warning, keep manual recovery path available.
5. Preserve context/filter/pagination during polling refresh.

---

## 13. Operator “Why” Messaging

Reason codes:
1. `needs_review`
2. `paywalled`
3. `no_oa_found`
4. `rate_limited`
5. `robots_blocked`
6. `source_error`

Reason text examples:
1. `paywalled` -> "Source appears paywalled; manual or alternate legal source required."
2. `no_oa_found` -> "No open-access source found from legal resolution chain."
3. `rate_limited` -> "Provider was rate limited; retry later."

Display policy:
1. Always show reason on queue and failure tables.
2. Never force operator to infer failure from HTTP code alone.

---

## 14. Accessibility Baseline

Requirements:
1. Keyboard reachable primary buttons (`Approve/Reject/Retry/Upload`).
2. Status not color-only; include text labels.
3. Action buttons must be text-labeled.
4. Table headers explicit and consistent.
5. Error states announced in dedicated text regions.

---

## 15. Context Synchronization Contract

Global context object fields (examples):
1. `discovery_run_id`
2. `acq_run_id`
3. `parse_run_id`
4. `source_id`
5. `document_id`
6. `chunk_id`

Update triggers:
1. Queue row action click.
2. Discovery `Use Context`.
3. Parse doc actions.
4. Search source navigation.
5. Manual recovery prefill action.

Persistence expectation:
1. In-memory during session.
2. Survives polling refreshes.
3. Can be overwritten by newer user actions.

---

## 16. API Dependency Matrix

```text
UI Action                          -> API Endpoint
-----------------------------------------------------------------
Load work queue                    -> GET /v1/work-queue
Queue Approve/Reject               -> POST /v1/sources/{id}/review
Queue Retry Acquisition            -> POST /v1/acquisition/runs (retry=true)
Queue Retry Parse                  -> POST /v1/parse/runs (retry=true)
Queue Open Manual Recovery         -> GET /v1/acquisition/runs/{id}/manual-downloads
Global search                      -> GET /v1/search/global
System badges                      -> GET /v1/system/status
Create discovery session           -> POST /v1/discovery/runs
Discovery source list              -> GET /v1/discovery/runs/{id}/sources
Acquisition details                -> GET /v1/acquisition/runs/{id}, /items
Parse details                      -> GET /v1/parse/runs/{id}, /documents, /chunks
Search corpus                      -> POST /v1/search
Manual upload                      -> POST /v1/acquisition/runs/{id}/manual-upload
```

---

## 17. QA Acceptance Checklist

Operator flow:
1. Open `/hmi`, land on `Work Queue`.
2. Approve a discovery item without entering IDs manually.
3. Retry an acquisition failure from queue row.
4. Jump to manual recovery and upload with prefilled source.
5. Use global search to navigate to a related context.
6. Verify context panel updates after each action.

Auth flow:
1. Auth disabled: no token inputs required for action flow.
2. Auth enabled: system token mode visible; manual override possible.

Robustness:
1. Polling changes from 5s to 15s when tab hidden.
2. Poll errors surface stale warning without freezing controls.

---

## 18. Non-Goals (for current iteration)

1. Multi-user presence/locking UX.
2. Fine-grained RBAC and role-specific UI.
3. Full WebSocket push transport.
4. Separate frontend deployment repo.

---

## 19. Future Enhancements

1. Queue prioritization scoring and SLA timers.
2. Bulk actions (approve/reject/retry in batch).
3. Persisted user preferences for layout/filter presets.
4. Enhanced accessibility audit automation.
