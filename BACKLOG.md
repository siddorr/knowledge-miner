# Backlog

Status:
- Active backlog now includes only open tasks.
- Completed history archived in `archive/backlog_completed_2026-03.md`.
- Last update: 2026-03-13.
- Updated on 2026-03-13: UI design authority was replaced by the new GUI Design Specification v1.1 through the rewritten `UI_SPEC.md`.

## Open Tasks

1. [x] P0 - Replace current HMI shell with the new workstation shell
- Goal:
  - align the HMI structure to the new canonical shell contract.
- Scope:
  - implement a header/status row
  - implement a separate controls row
  - implement a separate navigation row
  - preserve a fixed footer row
  - remove shell behaviors that assume the previous top-level layout model
- Acceptance criteria:
  - shell order is `header -> controls -> navigation -> workspace -> footer`
  - controls and navigation are not merged into one competing row
  - footer is stable across screens

2. [x] P0 - Move primary session controls into the dedicated controls row
- Goal:
  - make `New Session`, `Save`, `Load`, and `Delete` visible as top-level controls.
- Scope:
  - add a dedicated controls row above navigation
  - place `New Session`, `Save`, `Load`, and `Delete` there
  - reuse existing session logic where possible
  - remove duplicate/conflicting session-control entry points from task pages
- Acceptance criteria:
  - controls row exists above navigation
  - all four canonical controls are present
  - actions are wired to one consistent session-management flow

3. [x] P0 - Replace `Topic` terminology in primary UX with `Session`
- Goal:
  - use `Session` as the canonical operator-facing term.
- Scope:
  - audit primary HMI labels, status text, helper copy, empty states, and docs
  - replace user-facing `Topic` wording with `Session`
  - keep internal code naming stable where safe during transition
- Acceptance criteria:
  - primary workflow pages use `Session`
  - old `Topic` wording is absent from primary UX
  - remaining `Topic` usage is internal-only or explicitly transitional

4. [x] P0 - Rename final stage from `Library` to `Library Export`
- Goal:
  - match the new design contract and make export intent explicit.
- Scope:
  - replace user-facing `Library` stage labels with `Library Export`
  - update nav labels, page headings, and workflow copy
  - keep API/backend names stable unless there is a separate implementation need
- Acceptance criteria:
  - final stage is labeled `Library Export` in primary UX
  - workflow copy consistently says `Discover -> Review -> Documents -> Library Export`

5. [x] P0 - Rebuild Review as a Rayyan-style two-pane screening workspace
- Goal:
  - make Review a fast triage screen with list on the left and details on the right.
- Scope:
  - left pane paper list
  - right pane details panel
  - list columns: `Year | Cit | Score | Title`
  - details panel includes abstract, metadata, AI signals, and `Accept/Reject/Later`
  - keyboard shortcuts `A/R/L` and next-paper navigation
- Acceptance criteria:
  - Review is clearly two-pane
  - metadata order is fixed and consistent
  - no manual run-ID selection appears in the primary Review pane

6. [x] P0 - Enforce fixed paper metadata order across screens
- Goal:
  - use one metadata order everywhere a paper is shown.
- Required order:
  - `Year | Journal | Citations | Authors | Link`
- Scope:
  - Review details
  - Documents details/rows where metadata appears
  - Library Export details
- Acceptance criteria:
  - metadata order is identical across all relevant screens
  - compact views show at most 3 authors

7. [x] P0 - Redesign Documents screen to match the new acquisition workstation layout
- Goal:
  - align Documents with the new summary/table/actions/upload structure.
- Scope:
  - summary row with `Downloaded | Failed | Manual uploads | Pending`
  - table columns `Rank | Score | Year | Cit | Title | Status`
  - primary actions row with `Download missing` and `Retry failed`
  - batch upload row with file chooser and upload action
- Acceptance criteria:
  - Documents screen matches the target structure
  - summary row is visible and stable
  - action row and upload row are clearly separated

8. [x] P1 - Implement Documents CSV export contract from the new design
- Goal:
  - ensure exported document CSV matches the new required fields.
- Required CSV fields:
  - title
  - authors
  - year
  - journal
  - citations
  - ai_score
  - status
  - source_link
- Acceptance criteria:
  - exported CSV columns match the contract
  - export works from the redesigned Documents screen

9. [x] P0 - Redesign Library Export screen around ranked export workflow
- Goal:
  - make the final stage an export-focused ranked results workspace.
- Scope:
  - page label `Library Export`
  - ranked rows `Rank | AI Score | Year | Citations | Title`
  - right-side paper details
  - export size controls
  - export actions `Export ZIP with PDFs` and `Export Metadata CSV`
  - manual include/exclude controls for export list
- Acceptance criteria:
  - Library Export is clearly export-oriented
  - ranked rows stay minimal
  - export controls are visible and coherent

10. [x] P0 - Keep `Advanced` strictly outside the normal workflow
- Goal:
  - isolate technical complexity from research task pages.
- Scope:
  - keep diagnostics, logs, IDs, and low-level run controls in `Advanced`
  - remove primary-workflow dependence on `Advanced`
  - remove duplicated technical controls from task pages where possible
- Acceptance criteria:
  - a first-time user can complete the main workflow without `Advanced`
  - technical IDs and low-level controls are not required in task pages

11. [x] P1 - Replace the old global status-strip model with the new header/footer contract
- Goal:
  - stop treating the old status strip as the primary shell organizer.
- Scope:
  - shift status communication into the header/status row and fixed footer
  - remove or demote legacy shell behaviors that conflict with the new design
- Acceptance criteria:
  - docs and implementation target the new shell model only
  - no competing top-level shell contract remains

12. [x] P1 - Align footer contract with the new design
- Goal:
  - standardize the footer as a stable global operational summary.
- Scope:
  - include system readiness
  - include AI readiness
  - include DB readiness
  - include last update time
- Acceptance criteria:
  - footer content is consistent across screens
  - footer remains visible and stable

13. [ ] P1 - Refactor `hmi.js` into feature modules
- Problem:
  - `src/knowledge_miner/hmi/static/hmi.js` is large and mixes API/state/UI/event logic.
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
  - `hmi/state.js` extracted and wired.
  - `hmi/telemetry.js` extracted and wired.
  - `hmi/documents.js` extracted and wired.
  - `hmi/review.js` extracted and wired.
  - `hmi/library.js` extracted and wired.
- Acceptance criteria:
  - no single frontend module exceeds ~800 lines
  - existing HMI acceptance tests pass
  - no regression in run/session context handling

14. [x] P1 - Refactor `main.py` into FastAPI routers by domain
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
  - `routes/settings.py` extracted and mounted.
  - `routes/system.py` extracted and mounted.
  - `routes/hmi.py` extracted and mounted.
  - `routes/search.py` extracted and mounted.
  - `routes/discovery.py` extracted and mounted.
  - `routes/parse.py` extracted and mounted.
  - `routes/acquisition.py` extracted and mounted.
- Acceptance criteria:
  - public API paths and response contracts unchanged
  - app startup/import stable
  - route-level tests pass without contract drift

15. [x] P1 - Map current implementation gaps explicitly against the new design
- Goal:
  - keep implementation work decision-complete while the code still reflects the old design in parts.
- Scope:
  - produce a gap matrix:
    - current shell
    - controls row
    - Review layout
    - Documents layout
    - Library Export layout
    - terminology
  - tie each gap to one backlog item
- Acceptance criteria:
  - no major new-design requirement is left without an owner task
  - reviewers can see current-vs-target state clearly

## Current-vs-Target Gap Matrix

1. Shell contract (`header -> controls -> navigation -> workspace -> footer`)
- Current: implemented in HMI shell with distinct rows and fixed footer.
- Target: complete.
- Owner task: #1 (done)

2. Controls row (`New/Save/Load/Delete Session`)
- Current: implemented as top controls row with shared session logic.
- Target: complete.
- Owner task: #2 (done)

3. Review two-pane workflow
- Current: implemented with left list and right details panel, including `A/R/L`.
- Target: complete.
- Owner task: #5 (done)

4. Documents workstation layout
- Current: implemented with summary row, action row, upload row, and rank/status table.
- Target: complete.
- Owner task: #7 (done)

5. Library Export workflow
- Current: implemented with ranked table, export size, include/exclude, and CSV/manifest exports.
- Target: complete for v1; dedicated ZIP endpoint still planned.
- Owner task: #9 (done)

6. Terminology (`Session`, `Library Export`)
- Current: updated in primary UX labels and flow text.
- Target: complete.
- Owner tasks: #3, #4 (done)

7. Header/Footer status contract
- Current: header status row + fixed footer readiness fields implemented.
- Target: complete.
- Owner tasks: #11, #12 (done)

8. Frontend modularization (`hmi.js`)
- Current: partial extraction completed (`api/state/telemetry/review/documents/library/session`), orchestration still oversized.
- Target: pending.
- Owner task: #13 (open)

9. Backend router split (`main.py`)
- Current: partial extraction completed (`settings/system/hmi/search/discovery`), acquisition and parse routes remain in `main.py`.
- Target: pending.
- Owner task: #14 (open)

16. [ ] P0 - Add HMI shell contract tests for the new workstation layout
- Goal:
  - verify the implemented HMI matches the canonical shell structure.
- Scope:
  - add tests for separate `header/status`, `controls`, `navigation`, `workspace`, and `footer` sections
  - verify shell order is `header -> controls -> navigation -> workspace -> footer`
  - verify controls and navigation are not merged into one competing row
  - verify canonical controls row contents
  - target test file: `tests/test_hmi_shell_contract.py`
- Acceptance criteria:
  - tests fail if shell sections are missing, merged, or out of order
  - tests assert `New Session | Save | Load | Delete` in the controls row
  - tests assert `Discover | Review | Documents | Library Export | Advanced` in the nav row

17. [ ] P0 - Add primary terminology contract tests for Session and Library Export
- Goal:
  - prevent regression to deprecated user-facing naming.
- Scope:
  - add tests that primary UX uses `Session`, not `Topic`
  - add tests that the final stage label is `Library Export`, not `Library`
  - add tests for workflow copy `Discover -> Review -> Documents -> Library Export`
  - target test file: `tests/test_hmi_terminology_contract.py`
- Acceptance criteria:
  - tests fail when deprecated labels appear in primary UX
  - remaining `topic` usage is allowed only in internal code or explicitly transitional contexts

18. [ ] P0 - Add Review layout contract tests for the Rayyan-style screening workspace
- Goal:
  - verify Review matches the new two-pane triage design.
- Scope:
  - add tests for left list and right details pane
  - verify list columns `Year | Cit | Score | Title`
  - verify fixed metadata order `Year | Journal | Citations | Authors | Link`
  - verify `Accept | Reject | Later` actions in the details pane
  - verify keyboard guidance for `A / R / L` and next-item navigation
  - verify no run-ID selector is present in Review
  - target test file: `tests/test_hmi_review_contract.py`
- Acceptance criteria:
  - tests fail when Review is not clearly two-pane
  - tests fail when Review exposes manual run-ID selection in the primary workflow

19. [ ] P0 - Add Review session-binding behavior tests
- Goal:
  - ensure Review automatically follows the active session rather than requiring manual technical context.
- Scope:
  - seed session-backed review queues and verify Review auto-loads from the active session
  - verify session switch updates the visible review queue
  - verify pending counts reflect the active session only
  - verify `Later` persists across refresh/reload
  - extend `tests/test_hmi_ux_acceptance.py`
- Acceptance criteria:
  - Review works without manual ID entry
  - Review state changes when the active session changes
  - queue counts match the active session context

20. [ ] P0 - Add Documents layout and workflow contract tests
- Goal:
  - verify Documents matches the new acquisition workstation design and still performs required actions.
- Scope:
  - add tests for summary row `Downloaded | Failed | Manual uploads | Pending`
  - add tests for table columns `Rank | Score | Year | Cit | Title | Status`
  - add tests for primary actions `Download missing | Retry failed`
  - add tests for batch upload row and upload action
  - add tests for Documents CSV export contract
  - target test file: `tests/test_hmi_documents_contract.py`
  - extend `tests/test_hmi_acceptance.py` and `tests/test_acquisition_api.py`
- Acceptance criteria:
  - tests fail if Documents layout drifts from the new contract
  - export verification covers fields:
    - `title`
    - `authors`
    - `year`
    - `journal`
    - `citations`
    - `ai_score`
    - `status`
    - `source_link`

21. [ ] P0 - Add Library Export contract tests
- Goal:
  - verify the final stage is an export workspace, not a generic library browser.
- Scope:
  - add tests for nav label and page heading `Library Export`
  - verify ranked row format `Rank | AI Score | Year | Citations | Title`
  - verify details pane presence
  - verify export size controls
  - verify export actions `Export ZIP with PDFs` and `Export Metadata CSV`
  - verify include/exclude controls for export selection
  - target test file: `tests/test_hmi_library_export_contract.py`
- Acceptance criteria:
  - tests fail if the final stage behaves like generic browsing instead of ranked export preparation
  - export controls are present and clearly tied to the selected export set

22. [ ] P1 - Add Advanced isolation tests
- Goal:
  - ensure technical complexity stays inside `Advanced` and does not leak into the primary workflow.
- Scope:
  - add tests confirming technical IDs and low-level controls live in `Advanced`
  - add tests confirming primary workflow pages do not require technical IDs
  - add tests confirming the main workflow can complete without using `Advanced`
  - target test file: `tests/test_hmi_advanced_isolation.py`
- Acceptance criteria:
  - tests fail if `Review`, `Documents`, or `Library Export` require low-level IDs or Advanced-only controls

23. [ ] P1 - Add accessibility and responsive shell contract tests
- Goal:
  - verify key non-visual and layout-order rules of the new HMI design.
- Scope:
  - add tests confirming status is not conveyed by color alone
  - add tests confirming stable DOM order for controls and navigation
  - add tests confirming header/footer status text is explicit
  - target test file: `tests/test_hmi_accessibility_contract.py`
- Acceptance criteria:
  - tests fail when status meaning is only implicit
  - tests fail when shell ordering becomes ambiguous for responsive layouts

24. [ ] P1 - Update existing HMI acceptance tests to the new design vocabulary and shell assumptions
- Goal:
  - align existing tests with the new authoritative GUI contract instead of preserving legacy wording.
- Scope:
  - replace old `Library` expectations with `Library Export`
  - replace old `Topic` expectations with `Session`
  - remove old shell-model assumptions from acceptance tests
  - update:
    - `tests/test_hmi_ux_acceptance.py`
    - `tests/test_hmi_acceptance.py`
    - `tests/test_acquisition_api.py`
- Acceptance criteria:
  - existing acceptance tests no longer encode deprecated GUI terminology or old shell structure
