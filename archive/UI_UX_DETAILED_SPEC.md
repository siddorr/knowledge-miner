# Knowledge Miner UI/UX Detailed Specification

Version: 3.0  
Scope: Task-first HMI at `/hmi`  
Audience: Product owner, operators, frontend/backend developers, QA

## 1. Canonical Navigation Model

This document uses exactly the same navigation model as `HMI_PLAN.md`.

Top-level navigation:
1. `Dashboard`
2. `Discover`
3. `Review`
4. `Documents`
5. `Search`
6. `Advanced`

No alternative/stage-first model is valid in this spec.

## 2. Product UX Goals

1. First-time user can complete the core flow without CLI.
2. Primary pages are task-oriented, not pipeline-oriented.
3. Technical IDs are hidden on task pages by default.
4. Actions are row-context based (no copy/paste ID workflow).
5. Status is always shown as text + color.

## 3. Global Layout (Pseudographics)

```text
+----------------------------------------------------------------------------------+
| HEADER: Knowledge Miner Task Dashboard                                           |
| First-time-user flow: Discover -> Review -> Documents -> Search                  |
+----------------------------------------------------------------------------------+
| POLL BAR: Polling status + system readiness badges                               |
+----------------------------------------------------------------------------------+
| NAV: [Dashboard] [Discover] [Review] [Documents] [Search] [Advanced]            |
+----------------------------------------------------------------------------------+
| MAIN: selected task page                                                          |
+----------------------------------------------------------------------------------+
```

## 4. Dashboard

Purpose:
1. Start discovery quickly.
2. Show what needs attention next.

Required UI:
1. `Run Discovery` form (queries, max iterations, AI mode).
2. Attention cards/counts:
- sources needing review
- download issues
- parse errors
3. Recent activity summary.

Pseudographics:

```text
+----------------------------------------------------------------------------------+
| Dashboard                                                                        |
| [queries........] [max_iter] [ai mode] [Run Discovery]                           |
| State: Run created: run_xxx                                                      |
+---------------------+--------------------+-------------------+-------------------+
| Needs Review        | Download Issues    | Parse Errors      | Recent Discovery  |
| 12                  | 5                  | 1                 | status=running... |
+---------------------+--------------------+-------------------+-------------------+
```

## 5. Discover

Purpose:
1. Show latest run summary.
2. Keep detailed controls available only in technical drawer.

Required UI:
1. `Load Latest Run`
2. `Export sources_raw`
3. Summary panel (status, iteration, totals, AI state)
4. `Technical details` drawer with run ID override/filter controls.

## 6. Review

Purpose:
1. Fast accept/reject workflow.

Required UI:
1. Table columns:
- title
- abstract (expand/collapse)
- decision controls (`Accept`, `Reject`)
- why/reason context
2. Pagination controls.
3. `Technical details` drawer for run ID override.

Pseudographics:

```text
+----------------------------------------------------------------------------------+
| Review                                                                           |
| status [needs_review] limit [50] [Load Review Queue]                             |
+----------------------------------------------------------------------------------+
| Title                | Abstract                    | Decision | Why              |
| ...                  | ... [Expand]                | [A][R]   | ai|score=...     |
+----------------------------------------------------------------------------------+
```

## 7. Documents

Purpose:
1. Resolve failed/partial acquisition items.

Required UI:
1. Table columns:
- title
- problem
- actions (`Retry`, `Upload PDF`, `Open source`)
2. CSV export.
3. Manual upload form.
4. `Technical details` drawer for acquisition/source ID overrides.

Pseudographics:

```text
+----------------------------------------------------------------------------------+
| Documents                                                                        |
| limit [50] [Load Download Issues] [Export CSV]                                   |
+----------------------------------------------------------------------------------+
| Title                | Problem                | Actions                           |
| ...                  | Paywalled              | [Retry] [Upload PDF] [Open source]|
+----------------------------------------------------------------------------------+
| Upload: [file] [Upload PDF]                                                      |
+----------------------------------------------------------------------------------+
```

## 8. Search

Purpose:
1. Simple query UX for end users.
2. Keep deep technical outputs behind a drawer.

Required UI:
1. Single query input + limit + search button.
2. Results table with snippet and row actions (`Doc`, `Text`, `Source`).
3. `Technical details` drawer with parse run override and JSON panels.

## 9. Advanced

Purpose:
1. Keep all technical/operator controls out of primary task pages.

Contains:
1. API auth controls.
2. AI runtime settings.
3. Latest IDs + copy controls.
4. Global technical search.
5. Run lookup and phase controls.
6. Acquisition/parse start tools and manifest export.

## 10. Status Mapping (Text + Color)

Primary status labels:
1. `Ready` (green)
2. `In Progress` (yellow)
3. `Needs Action` (red)

Rule:
1. Never rely on color alone.

## 11. Accessibility Baseline

1. All primary actions are text-labeled buttons.
2. Keyboard navigation supports the full task flow.
3. Error and state messages are visible in plain text.
4. Collapsible technical sections use semantic `<details>/<summary>`.

## 12. API Dependencies

Task pages consume:
1. `GET /v1/work-queue`
2. `GET /v1/system/status`
3. Discovery APIs (`/v1/discovery/...`)
4. Source review API (`POST /v1/sources/{source_id}/review`)
5. Manual recovery/upload APIs
6. Parse/search APIs
7. `GET /v1/search/global` (Advanced)

## 13. Acceptance Checklist

1. User can complete `Dashboard -> Discover -> Review -> Documents -> Search` without reading docs.
2. No manual ID entry required in the default path.
3. IDs and low-level controls are accessible from `Advanced` and technical drawers only.
4. Status badges use both text and color.
5. Docs and tests reflect one navigation model only.
