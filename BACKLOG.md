# Backlog

Status:
- Active backlog now includes only open tasks.
- Completed history archived in `archive/backlog_completed_2026-03.md`.
- Last update: 2026-03-13.
- Updated on 2026-03-13: Review run-context resolver is fixed (#0), HMI partial template composition is in place (#20), and file-size guardrails with exceptions policy are added (#22).

## Open Tasks

0. [x] P0 - Fix Review page run-context gap when badge shows pending sources
- Goal: if the shell shows `N sources need review`, the Review page must resolve and display those rows or explicitly ask the operator to choose the owning run.
- Tasks:
  - On entering Review, if `state.latest.discovery` is empty and pending review work exists, resolve run context from `/v1/runs/latest` or `/v1/work-queue`.
  - If pending review sources belong to multiple runs, show explicit run selector/banner instead of empty review content.
  - Prevent task badges from advertising actionable review work when Review run context cannot be resolved.
  - Add telemetry for autoload outcomes:
    - `review_autoload:resolved_run`
    - `review_autoload:no_run_context`
    - `review_autoload:multiple_runs`
- Acceptance criteria:
  - pending review count + no active run -> Review auto-resolves correct run and loads rows
  - multiple pending runs -> Review shows explicit chooser, not blank list
  - no run context available -> actionable message, not silent empty state

18. [ ] P1 - Refactor `hmi.js` into feature modules
- Problem:
  - `src/knowledge_miner/hmi/static/hmi.js` is >3k lines and mixes API/state/UI/event logic.
- Scope:
  - split into modules:
    - `hmi/api.js`
    - `hmi/state.js`
    - `hmi/review.js`
    - `hmi/documents.js`
    - `hmi/library.js`
    - `hmi/live_updates.js`
    - `hmi/telemetry.js`
  - keep behavior parity and existing UI flows.
- Progress:
  - `hmi/api.js` extracted and wired.
  - `hmi/state.js` extracted and wired (state/constants moved out of monolith).
  - `hmi/telemetry.js` extracted and wired.
  - `hmi/documents.js` extracted and wired (documents action handlers/batch upload flow moved out).
- Acceptance criteria:
  - no single frontend module exceeds ~800 lines
  - existing HMI acceptance tests pass
  - no regression in run/session context handling

19. [ ] P1 - Refactor `main.py` into FastAPI routers by domain
- Problem:
  - `src/knowledge_miner/main.py` is large and couples system, HMI, discovery, acquisition, parse, search.
- Scope:
  - split into routers:
    - `routes/system.py`
    - `routes/hmi.py`
    - `routes/discovery.py`
    - `routes/acquisition.py`
    - `routes/parse.py`
    - `routes/search.py`
    - `routes/settings.py`
  - keep shared auth/rate-limit/dependency wiring centralized.
- Progress:
  - `routes/settings.py` extracted and mounted; `/v1/settings/ai-filter` now served from router module.
  - `routes/system.py` extracted and mounted; `/healthz` now served from router module.
- Acceptance criteria:
  - public API paths and response contracts unchanged
  - app startup/import stable
  - route-level tests pass without contract drift

20. [x] P2 - Refactor `index.html` into partial templates/components
- Problem:
  - `src/knowledge_miner/hmi/index.html` is large and hard to maintain as a single monolith.
- Scope:
  - split into reusable partials:
    - `partials/nav.html`
    - `partials/status_strip.html`
    - `partials/review.html`
    - `partials/documents.html`
    - `partials/library.html`
    - `partials/advanced.html`
  - preserve current IDs/hooks required by JS.
- Acceptance criteria:
  - no broken selectors/listeners in JS
  - full HMI renders and behaves the same
  - HTML validity preserved for composed output

21. [ ] P1 - Move `Save` button to the top button row after `Advanced`
- Goal:
  - make session save visible in the primary top navigation/button line.
- Scope:
  - add a `Save` button immediately after `Advanced` in the top button row
  - wire it to the existing save-session behavior
  - avoid duplicate save logic or secondary inconsistent save paths
  - preserve sensible order on desktop and mobile layouts
- Acceptance criteria:
  - top button order includes `... Advanced | Save`
  - clicking top-row `Save` triggers the existing session save flow
  - no regression in current session save/load behavior

22. [ ] P0 - Remove run ID selection from Review pane and bind Review to active working session/topic
- Goal:
  - Review must work automatically on the current working session/topic without exposing run-ID selection to the operator.
- Scope:
  - remove/hide Review run-ID selector/input from the Review pane
  - auto-bind Review queue loading to the active session/topic context
  - when session/topic changes, Review must switch context automatically and refresh rows
  - if no active working session/topic exists, show an operator-facing actionable state instead of technical run-ID controls
- Acceptance criteria:
  - Review pane has no run-ID selection control
  - Review rows always reflect the active working session/topic
  - changing working session/topic updates Review automatically
  - no manual run-ID entry is required anywhere in the primary Review workflow

23. [ ] P1 - Replace `topic` terminology in primary UX with `session` or equivalent workflow language
- Goal:
  - use operator-friendly workflow terminology; `topic` should not be the primary concept if `session` is a better fit.
- Scope:
  - audit primary HMI labels, status strip text, button text, and workflow copy for `topic`
  - replace primary-user-facing `topic` wording with `session` or another approved workflow term
  - keep internal implementation names unchanged where renaming code would add unnecessary risk
  - update docs to match the chosen operator-facing terminology
- Acceptance criteria:
  - primary HMI no longer relies on `topic` as the main operator-facing concept
  - session/workflow wording is consistent across Build/Review/Documents/Library
  - internal code may still use `topic` where safe, but user-facing text is aligned

21. [x] P2 - Keep backlog maintainable by archiving completed items
- Completed:
  - completed tasks moved to `archive/backlog_completed_2026-03.md`
  - active backlog now focused on open tasks only

22. [x] P2 - Add file-size guardrails and lint checks for maintainability
- Problem:
  - oversized files grow unnoticed and increase regression risk.
- Scope:
  - add CI/pre-commit checks for:
    - JS/TS files over threshold (warn/fail)
    - Python files over threshold (warn/fail)
  - document exceptions policy.
- Acceptance criteria:
  - thresholds enforced in automated checks
  - violations emit actionable messages with file paths
