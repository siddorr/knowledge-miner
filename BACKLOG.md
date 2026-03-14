# Backlog

Status:
- Backlog file currently includes both open tasks and completed items for traceability.
- Completed history archived in `archive/backlog_completed_2026-03.md`.
- Last update: 2026-03-13.
- Updated on 2026-03-13: UI design authority was replaced by the new GUI Design Specification v1.1 through the rewritten `UI_SPEC.md`.
- Updated on 2026-03-13: legacy `hmi` frontend was moved to `archive/frontend/hmi_legacy`; active UI development target is `hmi2`.

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

13. [x] P1 - Refactor `hmi.js` into feature modules
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
  - `hmi/run_context.js` extracted and wired.
- Acceptance criteria:
  - no single frontend module exceeds ~800 lines
  - existing HMI acceptance tests pass
  - no regression in run/session context handling
- Resolution:
  - no longer relevant as an active backlog item because legacy `hmi` has been superseded by `hmi2` for ongoing UI work.

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
- Current: implemented with ranked table, export size, include/exclude, CSV export, and dedicated ZIP export endpoint.
- Target: complete for v1.
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
- Current: legacy-only concern; active UI development moved to `hmi2`.
- Target: no further work planned unless legacy `hmi` is revived.
- Owner task: #13 (closed as not relevant)

9. Backend router split (`main.py`)
- Current: extracted to dedicated domain routers (`settings/system/hmi/search/discovery/acquisition/parse`) and mounted.
- Target: complete.
- Owner task: #14 (done)

16. [x] P0 - Add HMI shell contract tests for the new workstation layout
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

17. [x] P0 - Add primary terminology contract tests for Session and Library Export
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

18. [x] P0 - Add Review layout contract tests for the Rayyan-style screening workspace
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

19. [x] P0 - Add Review session-binding behavior tests
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

20. [x] P0 - Add Documents layout and workflow contract tests
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

21. [x] P0 - Add Library Export contract tests
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

22. [x] P1 - Add Advanced isolation tests
- Goal:
  - ensure technical complexity stays inside `Advanced` and does not leak into the primary workflow.
- Scope:
  - add tests confirming technical IDs and low-level controls live in `Advanced`
  - add tests confirming primary workflow pages do not require technical IDs
  - add tests confirming the main workflow can complete without using `Advanced`
  - target test file: `tests/test_hmi_advanced_isolation.py`
- Acceptance criteria:
  - tests fail if `Review`, `Documents`, or `Library Export` require low-level IDs or Advanced-only controls

23. [x] P1 - Add accessibility and responsive shell contract tests
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

24. [x] P1 - Update existing HMI acceptance tests to the new design vocabulary and shell assumptions
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

25. [x] P0 - Collapse Discover into one simple operator workspace matching the GUI spec
- Goal:
  - replace the current split `build` + `discover` model with one clear discovery screen.
- Problem:
  - current Discover is spread across a technical build console and a separate latest-run panel, which does not match the GUI specification.
- Scope:
  - remove the split between `build` and `discover` as separate operator stages
  - expose one discovery surface with:
    - active session query/topic input
    - visible query list
    - one primary run action
    - iteration indicator
    - single-row summary
  - move add-source, bulk-source, and other technical source-ingest tools out of the primary discovery surface or behind secondary/advanced affordances
- Acceptance criteria:
  - Discover is one operator-oriented screen
  - summary appears in one single row
  - the main user does not need to understand `build` vs `discover`

26. [x] P0 - Make Review header and interaction model match the Rayyan-style spec
- Goal:
  - make Review read like a focused screening workspace instead of an operations panel.
- Problem:
  - the current Review page is missing the explicit `Pending` framing and mixes triage with too many utility controls.
- Scope:
  - show a header like `Review Sources - Pending: N`
  - add a visible keyboard-help row in the main review screen
  - simplify or relocate secondary utility controls that overload the triage surface
  - preserve two-pane review and fast triage
- Acceptance criteria:
  - Review foregrounds pending screening work
  - keyboard guidance is visible without entering a hidden mode
  - primary screening actions dominate the page

27. [x] P0 - Remove technical and recovery leakage from the Documents main pane
- Goal:
  - keep the Documents screen aligned to the spec’s acquisition-focused operator flow.
- Problem:
  - the current Documents page exposes queue filters, pagination, copy utilities, manual source-id upload flow, and a `More` bucket in the main task page.
- Scope:
  - keep only the primary surface defined by the spec:
    - summary row
    - export CSV
    - ranked document table
    - `Download missing`
    - `Retry failed`
    - batch upload
  - move technical/manual recovery controls out of the primary pane
  - remove `Source ID for manual upload` from the main Documents workflow
- Acceptance criteria:
  - normal document handling does not require technical identifiers
  - the Documents screen is materially simpler and spec-aligned

28. [x] P0 - Rebuild Library Export as a true two-pane ranked export workspace
- Goal:
  - align Library Export with the GUI spec’s left-results / right-details model.
- Problem:
  - the current screen is table-plus-preview, with missing summary row and mismatched control labels.
- Scope:
  - add a summary row with:
    - matching papers
    - highest AI relevance
    - lowest AI relevance
  - implement a true two-pane layout:
    - ranked results on the left
    - paper details on the right
  - change manual controls to:
    - `Remove from export list`
    - `Add to export list`
  - add copy-title behavior near the link if retained by the spec
- Acceptance criteria:
  - Library Export is visually and behaviorally an export workspace
  - summary row is visible
  - details are shown beside results, not only below them

29. [x] P0 - Remove technical details from the main Library Export operator screen
- Goal:
  - keep technical parsed/source internals out of the primary export workflow.
- Problem:
  - the current Library Export screen exposes parsed-document and source-context technical detail blocks directly in the main page.
- Scope:
  - move parsed detail, full text, and related source context out of the primary Library Export pane
  - keep only operator-relevant export detail in the main workspace
  - relocate technical inspection to `Advanced` or a clearly secondary diagnostic path
- Acceptance criteria:
  - Library Export focuses on ranking, review, and export
  - technical parsed/source internals are not part of the default operator surface

30. [x] P0 - Enforce Advanced-only ownership of technical IDs and stage-control forms
- Goal:
  - make `Advanced` the only place for run IDs, source IDs, and low-level stage-control forms.
- Problem:
  - technical IDs are still leaking into normal workflow pages such as Discover and Documents.
- Scope:
  - audit `Discover`, `Review`, `Documents`, and `Library Export` for:
    - run-ID entry
    - source-ID entry
    - stage-control forms
    - technical detail panes
  - move those controls to `Advanced` or remove them
- Acceptance criteria:
  - normal workflow pages do not require technical IDs
  - `Advanced` remains the single diagnostics/control surface

31. [x] P1 - Resolve terminology inconsistencies inside the GUI description source
- Goal:
  - make the design reference internally consistent before final implementation sign-off.
- Problem:
  - the downloaded GUI description still uses `New Topic` and `Library` in the global-layout example while the approved project terminology is `Session` and `Library Export`.
- Scope:
  - normalize the project-facing interpretation of the spec to:
    - `New Session`
    - `Library Export`
  - note the inconsistency explicitly in implementation/review docs
  - ensure engineering and QA do not validate against conflicting labels
- Acceptance criteria:
  - there is one canonical terminology set for implementation and testing
  - no developer has to guess whether `Topic` or `Session` is authoritative

32. [ ] P0 - Add AI-generated query suggestions with explicit selected-query list in Discover
- Goal:
  - let operators build discovery runs from AI-suggested queries while keeping final execution under human control.
- Scope:
  - generate suggested queries with AI in Discover
  - show separate `Suggested queries` and `Selected queries` lists
  - allow operator to press/move suggestions into the selected-query list
  - allow regeneration of suggestions without auto-running discovery
  - use only the selected-query list for discovery run creation and citation iteration
- Acceptance criteria:
  - Discover shows separate suggestion and selection lists
  - operator can move suggestions into the selected-query list
  - only selected queries are sent to discovery run creation and citation iteration
  - suggestion regeneration refreshes suggestions without changing the selected-query list automatically

33. [ ] P0 - Show executed selected queries for the active run in Discover
- Goal:
  - make active run queries transparent to the operator.
- Scope:
  - display the exact executed selected queries for the active run in `Discover`
  - keep list visible while run is in progress and after completion
  - ensure list updates when active run/session changes
- Acceptance criteria:
  - operator can see exact query strings actually used for current run execution
  - list is backend-derived from persisted run queries
  - suggestion pool is not mixed into the run-status area

34. [ ] P0 - Show per-query execution state in Discover using `waiting`, `searching`, `ranking`, `completed`, `failed`
- Goal:
  - expose progress at query level instead of only global run-level progress.
- Scope:
  - track query execution state in backend for each run query
  - expose query states in API response consumed by HMI
  - render state indicator next to each query in `Discover`
- Acceptance criteria:
  - every query shows one of `waiting`, `searching`, `ranking`, `completed`, `failed`
  - state transitions occur during execution and settle at completion
  - UI maps backend execution phases consistently to these display states
  - UI remains consistent after refresh/reload

35. [ ] P0 - Disable keyword/frequency query generation and use AI-generated suggestions plus explicit human selection
- Goal:
  - remove noisy keyword-frequency query generation while keeping automatic AI suggestions in the workflow.
- Problem:
  - current keyword-frequency generation is weak/noisy, but automatic query suggestion is still desired.
- Scope:
  - disable keyword/frequency extraction as the active source of Discover queries
  - use AI-generated suggestions as the supported automatic source
  - require explicit human selection of queries before run creation and citation iteration
- Acceptance criteria:
  - keyword/frequency generation is not used in active Discover workflow
  - AI-generated suggestions are supported in Discover
  - no discovery run executes without explicit human-selected queries
  - docs/backlog no longer describe manual-only query origin
- Current decision:
  - AI generates suggestions; human selects what actually runs.

36. [ ] P0 - Disable `Run Next Citation Iteration` when no accepted papers exist
- Goal:
  - prevent unusable citation-iteration actions when there is no eligible parent set.
- Scope:
  - in `Discover`, disable citation-iteration action when accepted count is zero
  - show explicit reason near the control (for example: `Need at least 1 accepted paper`)
  - enforce same validation in backend endpoint for safety
- Acceptance criteria:
  - citation iteration cannot be started from GUI while accepted count is zero
  - user sees clear explanation why action is disabled
  - API returns clear validation error if called without accepted parents

37. [ ] P0 - Show raw discovered candidate counts per executed query in Discover
- Goal:
  - expose query-level outcome after each query finishes.
- Scope:
  - for each executed run query, show state and raw discovered candidate count in the query-status list
  - status shape should reflect the approved query states and final raw count
  - preserve values after refresh/reload and when revisiting Discover
- Acceptance criteria:
  - each completed query shows raw discovered candidate count for that query before human review
  - status transitions are visible in near-real-time
  - values remain consistent with backend state

38. [ ] P1 - Keep Review filter options visibly discoverable in the primary Review surface
- Goal:
  - ensure end users clearly understand they can browse pending, reviewed, and latest auto-decided items.
- Scope:
  - keep Review filter controls always visible in the main Review surface
  - do not hide access to accepted/rejected/latest-auto views behind dropdown-only or Advanced-only controls
- Acceptance criteria:
  - Review filter controls are visible at all times in the primary Review pane
  - user does not need Advanced or hidden controls to switch queue views

39. [ ] P0 - Keep `hmi2` Review filter behavior aligned with the approved Review filter contract
- Goal:
  - ensure `hmi2` Review behavior matches the approved filter contract directly, without relying on legacy `hmi` wording.
- Scope:
  - keep Review filters visible in `hmi2` as pressable controls with:
    - `Pending`
    - `Accepted`
    - `Rejected`
    - `Later`
    - `All`
    - `Latest Auto-Approved`
    - `Latest Auto-Rejected`
  - ensure each visible filter maps to the correct backend query/status semantics
- Acceptance criteria:
  - `hmi2` Review exposes the full approved filter set
  - each filter updates the visible review list using the correct backend semantics

40. [ ] P0 - Add `hmi2` Review queue filter contract test
- Goal:
  - prevent regression where `hmi2` loses accepted/rejected queue visibility.
- Scope:
  - add/extend frontend contract test for `hmi2`:
    - filter controls exist in Review
    - options include `Pending/Accepted/Rejected/Later/All/Latest Auto-Approved/Latest Auto-Rejected`
    - selected option maps to the correct backend review query semantics
- Acceptance criteria:
  - tests fail if filter is missing or options drift
  - tests fail if filter no longer drives the correct backend query behavior

41. [ ] P1 - Add concise helper copy near Review filter controls
- Goal:
  - make Review queue/filter browsing obvious to end users in `hmi2`.
- Scope:
  - add short helper text near Review filter controls explaining queue modes
  - mention that the user can browse pending, reviewed, and latest auto-decided papers
  - keep wording aligned with project terminology (`Session`, `Library Export`)
- Acceptance criteria:
  - helper copy is visible in `hmi2` Review
  - users can understand queue switching without external guidance or docs

42. [x] P1 - Recheck `hmi2` activity indicator and stage message behavior against current implementation
- Goal:
  - verify whether current `hmi2` activity messaging already satisfies the intended contract before scheduling more implementation.
- Scope:
  - verify animated indicator visibility while work is active
  - verify stage-specific messages in `hmi2` header status area
  - close task if current implementation already satisfies the intended behavior
  - otherwise rewrite remaining gap precisely
- Acceptance criteria:
  - task is either closed as already complete or rewritten to the exact missing delta
  - no vague duplicate activity-message work remains open
- Result:
  - closed as complete; `hmi2` header already shows animated activity indicator and stage-specific runtime messages.

43. [x] P0 - Recheck `hmi2` Documents row selection behavior against current implementation
- Goal:
  - verify current row selection/details behavior before keeping additional Documents interaction work open.
- Scope:
  - verify row click selection with visible highlight
  - verify lightweight selected-document details in Documents pane
  - verify action behavior by selected-row status
  - close task if current implementation already satisfies this behavior
  - otherwise rewrite only the missing delta
- Acceptance criteria:
  - task is either closed as already complete or narrowed to a precise missing behavior
  - no duplicate open task remains for already-implemented row selection
- Result:
  - closed as complete; row selection, highlight, selected-row details, and context-sensitive actions are already implemented in `hmi2` Documents.

44. [x] P1 - Recheck clickable source and DOI links in primary workflow pages
- Goal:
  - verify whether clickable link behavior is already complete before keeping link work open.
- Scope:
  - verify clickable anchor behavior in Review, Documents, and Library Export
  - verify safe external-link attributes and visible link styling
  - close task if current implementation already satisfies the link contract
  - otherwise rewrite the exact remaining gap
- Acceptance criteria:
  - task is either closed as already complete or rewritten to the precise missing link behavior
  - no duplicate open work remains for already-implemented links
- Result:
  - closed as complete; Review, Documents, and Library Export render clickable source links with safe external-link attributes.

45. [x] P0 - Recheck real `Journal`, `Authors`, and `Citations` metadata in `hmi2`
- Goal:
  - verify whether metadata plumbing/display is already complete before scheduling more implementation.
- Scope:
  - verify payload and UI rendering for `journal`, `authors`, and citation count in `hmi2`
  - verify placeholder fallback only when data is truly absent
  - close task if current implementation already satisfies metadata contract
  - otherwise rewrite the exact remaining gap
- Acceptance criteria:
  - task is either closed as already complete or rewritten to the exact missing metadata behavior
  - no vague metadata work remains open
- Result:
  - closed as complete; API payloads and `hmi2` metadata rendering already use real `journal`, `authors`, and `citation_count` values with fallback only when absent.

46. [ ] P1 - Place session naming control next to `Save` in `hmi2` controls row
- Goal:
  - make naming/renaming a session immediate and obvious in the primary session-controls area.
- Problem:
  - session name editing is separated from the `Save` action, which makes the naming flow less discoverable.
- Scope:
  - place the session-name input directly near the `Save` button in the top controls row
  - keep existing `New/Save/Load/Delete` session actions and behavior
  - preserve local session persistence and current naming logic
- Acceptance criteria:
  - operator can name/rename the current session from the controls row next to `Save`
  - naming flow is visible without navigating to secondary page sections
  - existing session save/load behavior remains stable

47. [ ] P1 - Add AI query suggestion generation and regeneration in Discover
- Goal:
  - help operators bootstrap discovery faster by generating AI suggestions directly in `hmi2` Discover.
- Scope:
  - add a Discover-screen action to generate AI query suggestions
  - show generated suggestions in the `Suggested queries` list
  - allow regeneration of the suggestion pool
  - let operator explicitly move/select suggestions before running discovery
  - keep final execution under operator control
- Acceptance criteria:
  - Discover can generate and regenerate AI suggestions in-place
  - suggestions appear in the suggestion list only
  - discovery run uses only the queries explicitly selected by the operator

48. [ ] P0 - Replace Review filter dropdown with pressable filter chips and persistent sort behavior
- Goal:
  - make Review queue filtering faster and more visible than a dropdown interaction.
- Scope:
  - replace the current Review filter dropdown with pressable text controls (tabs/chips/buttons)
  - include options for `Pending`, `Accepted`, `Rejected`, `Later`, `All`, `Latest Auto-Approved`, and `Latest Auto-Rejected`
  - add clear visual indication of the active filter
  - preserve the current Review sort when filter changes
  - reset sort to default only on page load or session change
- Acceptance criteria:
  - Review filter is selectable via pressable text controls, not dropdown
  - active filter is clearly highlighted
  - switching filters updates the queue exactly as current status API mapping expects
  - sort is preserved across filter changes and reset only on page load/session change

49. [ ] P0 - Add session-level per-provider search limits in Discover
- Goal:
  - let operator control provider fetch limits per session from the primary Discover workflow.
- Problem:
  - provider fetch limits are not controlled from the primary Discover workflow at session level.
- Scope:
  - add Discover controls for per-provider fetch limits
  - store limits at session level
  - validate numeric bounds in UI and backend
  - apply configured limits to future runs in that session
  - keep Discover as the editable owner of these controls; `Advanced` may show effective values read-only if needed
- Acceptance criteria:
  - operator can set per-provider limits before run start
  - limits are saved with the session
  - invalid values are rejected with clear feedback
  - future runs in that session use the configured provider limits

50. [ ] P1 - Add structured live operational event viewer in `Advanced`
- Goal:
  - give operators a clear real-time view of what the system is doing without opening server files manually.
- Scope:
  - add a structured live event viewer in `Advanced`
  - use polling first
  - keep entries readable as one-line structured rows
  - include compact endpoint/action summaries
  - show grouped counters by endpoint/action or operation class
  - support auto-scroll with ability to pause/freeze view for inspection
- Acceptance criteria:
  - `Advanced` shows live, continuously updating structured operational events
  - event format is detailed yet scannable on one line per event
  - operator can see grouped counters alongside event lines
  - raw server-log tailing is not required for completion

51. [ ] P0 - Auto-update raw fetched candidate counts per provider in Discover query status
- Goal:
  - make provider progress visible in real time for each query execution line.
- Scope:
  - extend run query status to show live raw fetched candidate counts for each provider (OpenAlex/Brave/Semantic Scholar when enabled)
  - update counts automatically while query is running, not only after completion
  - keep final per-provider totals after query completes
- Acceptance criteria:
  - each query row shows provider-specific raw fetched counts with auto-refresh
  - counts increase during execution and settle to final totals on completion
  - UI state remains consistent after reload/rebind to the same run

52. [ ] P0 - Keep Review list pointer on next paper after `Reject` (no jump to start)
- Goal:
  - preserve reviewer flow by advancing to the next item after decision.
- Problem:
  - after rejecting a paper, the list pointer resets to the start instead of continuing forward.
- Scope:
  - update Review selection/index logic so `Reject` moves focus to the next eligible paper
  - apply same stable-next behavior after review list refresh caused by decision updates
  - keep behavior consistent with keyboard shortcuts and button actions
- Acceptance criteria:
  - after `Reject`, selected pointer moves to next paper in list
  - pointer does not jump back to first item unless list is truly exhausted/reset
  - behavior is stable across repeated reject actions

53. [ ] P1 - Add `Iter` column to the Review list and make numeric Review columns sortable
- Goal:
  - make it clear which search iteration produced each review item and improve numeric review ordering.
- Scope:
  - add a visible `Iter` column to the Review list
  - use the existing source iteration field from discovery/source APIs
  - make numeric Review columns sortable, including at least:
    - `Iter`
    - `Year`
    - `Cit`
    - `Score`
  - default Review sort should be `Iter desc`
  - preserve current sort across filter changes
  - reset sort to default on page load or session change
- Acceptance criteria:
  - every Review row shows the paper iteration in a visible numeric column
  - user can sort by iteration/year/citations/score
  - default sort is `Iter desc`
  - current sort persists across filter changes and resets only on page load/session change

54. [ ] P1 - Add Review filters for latest auto-approved and latest auto-rejected documents
- Goal:
  - let operator quickly inspect the newest AI/heuristic auto decisions without mixing them with older reviewed sets.
- Problem:
  - current Review filtering does not expose a focused view for the latest auto-approved and auto-rejected papers.
- Scope:
  - add Review filter options for:
    - latest auto-approved
    - latest auto-rejected
  - define "latest" by the active session/current discovery run only
  - order these views newest first within that run context
  - keep these filters separate from human `Accepted` and `Rejected` browsing
- Acceptance criteria:
  - Review exposes `Latest Auto-Approved` and `Latest Auto-Rejected` filters
  - each filter shows only papers auto-decided in the active session/current run context
  - switching between these filters and the existing queue views updates the Review list correctly

## GUI Spec Review Checklist

- [ ] Global shell is `header/status -> controls -> navigation -> workspace -> footer`
- [ ] Controls row shows `New Session | Save | Load | Delete`
- [ ] Navigation shows `Discover | Review | Documents | Library Export | Advanced`
- [ ] Discover is one screen, not split into competing `build` and `discover` operator workflows
- [ ] Discover shows one single-row summary for discovered/approved/rejected/reviewed/pending
- [ ] Discover shows separate AI-generated suggested queries and explicit selected queries
- [ ] Discover shows all executed run queries in a list
- [ ] Discover shows per-query state (`waiting`/`searching`/`ranking`/`completed`/`failed`)
- [ ] Discover query generation uses AI suggestions; keyword/frequency generation is disabled
- [ ] Citation iteration action is disabled until at least one paper is accepted
- [ ] Discover per-query status includes raw discovered candidate counts after completion
- [ ] Discover exposes session-level per-provider search limits
- [ ] Review header shows pending count prominently
- [ ] Review queue controls clearly expose Accepted, Rejected, and latest-auto views
- [ ] `hmi2` Review filter behavior matches the approved Review filter contract
- [ ] Active-work status uses animated indicator and explanatory stage text
- [ ] `hmi2` Documents row click selects item and shows actionable details
- [ ] Source and DOI links are visibly clickable in Review, Documents, and Library Export
- [ ] `hmi2` metadata shows real `Journal`, `Authors`, and `Citations` values
- [ ] Review is clearly two-pane: list left, details right
- [ ] Review list shows a visible `Iter` column for each paper
- [ ] Review exposes focused filters for latest auto-approved and latest auto-rejected papers
- [ ] Review shows visible keyboard help for `A`, `R`, `L`, and next-paper navigation
- [ ] Review metadata order is `Year | Journal | Citations | Authors | Link`
- [ ] Documents shows only the primary spec flow in the main pane, while allowing lightweight selected-row details/actions consistent with task `#43`
- [ ] Documents includes summary row, export CSV, ranked table, actions row, and batch upload row
- [ ] Documents does not require `Source ID` entry in the normal workflow
- [ ] Library Export shows a summary row with counts/relevance range
- [ ] Library Export is two-pane: ranked results left, details right
- [ ] Library Export uses export-list controls matching the spec intent
- [ ] Technical parsed/source internals are not shown in the main Library Export surface
- [ ] `Advanced` is the only place for low-level IDs, run lookup, and stage controls
- [ ] Footer is stable and shows system, AI, DB, and last-update status
