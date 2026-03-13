# UI Specification

Status: Active source of truth for HMI design, rewritten on 2026-03-13 to align with `Downloads/knowledge_miner_gui_design_spec_v1_1.md`.

Supersession note:
1. This file supersedes the previous HMI contract.
2. Older task-first shell descriptions, screenshots, and mixed layout rules are historical only unless revalidated here.
3. GUI Design Specification v1.1 is now the basis of the in-repo UI contract.

## 1. Design Principles

The GUI is a research workstation, not a website.

Primary goals:
1. Fast literature screening.
2. Predictable interface.
3. Minimal cognitive load.
4. AI-assisted ranking.
5. Efficient export of knowledge packages.

Canonical workflow:
1. `Discover`
2. `Review`
3. `Documents`
4. `Library Export`
5. `Advanced`

Primary operator-facing concept:
1. `Session`

Deprecated operator-facing concept:
1. `Topic`

## 2. Global Layout

The shell is fixed in this order on every screen:
1. Header/status row.
2. Controls row.
3. Navigation row.
4. Main workspace.
5. Footer row.

The shell must not collapse these into a competing primary layout model in docs or implementation targets.

## 3. Header and Status Row

The top row contains:
1. Product name: `Knowledge Miner`
2. One concise work-in-progress message when background work exists
3. Progress wording in plain text

Example:
1. `Search in progress... discovering papers (iteration 2 / 8)`

Rules:
1. Status text must be visible without opening technical panels.
2. Status must not rely on color alone.
3. This row communicates current work only; it is not the main controls row.

## 4. Controls Row

The controls row is separate from the navigation row.

Canonical controls:
1. `New Session`
2. `Save`
3. `Load`
4. `Delete`

Rules:
1. Controls are always visible.
2. Controls belong in one dedicated row above navigation.
3. Session operations must not be hidden inside `Advanced` as the primary path.
4. Technical session/history details may still exist in `Advanced`, but the primary operator actions must live here.

## 5. Navigation Row

Canonical top navigation:
1. `Discover`
2. `Review`
3. `Documents`
4. `Library Export`
5. `Advanced`

Rules:
1. Navigation is always visible.
2. `Advanced` is not part of the normal research workflow.
3. No alternate primary navigation model is valid.

Deprecated stage names:
1. `Build`
2. `Library` as the final canonical stage label

## 6. Footer

The footer is fixed and identical across screens.

It must contain concise operational state such as:
1. system readiness
2. AI readiness
3. DB readiness
4. last update time

Rules:
1. Footer wording stays stable across screens.
2. Footer is global, not page-specific.

## 7. Standard Paper Metadata

Metadata order is fixed everywhere:
1. `Year`
2. `Journal`
3. `Citations`
4. `Authors`
5. `Link`

Rules:
1. Show at most 3 authors in the compact view.
2. Full authors may appear in expanded detail.
3. `Link` opens DOI or the source page.
4. This ordering must stay the same in Review, Documents, and Library Export.

## 8. Discover Screen

Purpose:
1. Control literature discovery and query iteration.

Required visible elements:
1. Active session field.
2. Query list.
3. `Run discovery` action.
4. Iteration indicator.
5. One-row summary of discovery state.

Discover summary row must stay in one line and include:
1. `Discovered`
2. `Approved`
3. `Rejected`
4. `Reviewed`
5. `Pending review`

Session rules:
1. User-facing wording is `Session`, not `Topic`.
2. Session creation and switching are part of the primary flow.

## 9. Review Screen

Purpose:
1. Fast triage of candidate papers.

Review layout is Rayyan-style:
1. Left pane: paper list
2. Right pane: paper details

Required paper list columns:
1. `Year`
2. `Cit`
3. `Score`
4. `Title`

Required details pane content:
1. Title
2. Abstract
3. Metadata block in the fixed metadata order
4. AI signals
5. Action buttons:
   - `Accept`
   - `Reject`
   - `Later`

Keyboard requirements:
1. `A = Accept`
2. `R = Reject`
3. `L = Later`
4. Down arrow moves to next paper

Review workflow rules:
1. Review is automatically bound to the active session.
2. Review must not expose run-ID selection in the primary workflow.
3. Technical IDs belong only in `Advanced` or technical drawers.
4. Pending count must match the active session context.

## 10. Documents Screen

Purpose:
1. Acquisition and tracking of full-text documents.

Required layout:
1. Summary row
2. Main table
3. Primary actions row
4. Batch upload row

Documents summary row must include:
1. `Downloaded`
2. `Failed`
3. `Manual uploads`
4. `Pending`

Required table columns:
1. `Rank`
2. `Score`
3. `Year`
4. `Cit`
5. `Title`
6. `Status`

Primary actions:
1. `Download missing`
2. `Retry failed`

Batch upload actions:
1. `Choose files`
2. `Upload`

Documents CSV export contract must include:
1. title
2. authors
3. year
4. journal
5. citations
6. ai_score
7. status
8. source_link

## 11. Library Export Screen

Purpose:
1. Export curated literature packages.

Canonical label:
1. `Library Export`

Required layout:
1. Query field
2. Summary row
3. Ranked results pane
4. Details pane
5. Export controls

Required ranked row format:
1. `Rank`
2. `AI Score`
3. `Year`
4. `Citations`
5. `Title`

Required export controls:
1. Export size choices
2. `Export ZIP with PDFs`
3. `Export Metadata CSV`

Manual controls may include:
1. `Remove from export list`
2. `Add to export list`

This screen is export-oriented, not a generic library browser contract.

## 12. Advanced Screen

Purpose:
1. Diagnostics and system inspection only.

Required content categories:
1. Discovery runs
2. Acquisition runs
3. Parsing runs
4. System logs
5. Provider logs
6. AI scoring logs

Rules:
1. `Advanced` is outside the normal research workflow.
2. Technical IDs, diagnostics, and low-level controls belong here by default.
3. Task pages must not require users to open `Advanced` to complete the main workflow.

## 13. Workflow Rules

Canonical workflow:
1. `Discover -> Review -> Documents -> Library Export`

Rules:
1. Session state drives the primary workflow.
2. Review is session-bound, not run-ID-driven in the operator view.
3. Documents operate on the current session’s approved/retrieval set.
4. `Advanced` supports diagnostics only.

## 14. Data Field and Labeling Rules

Rules:
1. Use `Session` in primary UX text.
2. Do not use `Topic` as the main operator-facing noun.
3. Use `Library Export` as the final stage label.
4. Keep metadata order fixed everywhere.
5. Use concise, predictable status wording.

## 15. Accessibility and Responsiveness

Rules:
1. Status cannot rely on color alone.
2. Controls row and navigation row must remain understandable on desktop and mobile.
3. Desktop may use wider multi-pane layouts.
4. Mobile may stack sections, but control order and meaning must remain intact.

## 16. Deprecated Old Patterns

The following are no longer canonical design targets:
1. `Build` as the primary stage label.
2. `Topic` as the primary operator-facing concept.
3. `Library` as the final canonical stage label.
4. A shell model where the old global status strip is the dominant top-level organizer.
5. Review workflows that expose manual run-ID selection in the primary pane.
6. Mixed old/new layout contracts coexisting as active truth.
