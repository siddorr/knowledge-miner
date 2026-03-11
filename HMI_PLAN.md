# HMI Plan - Task-First UX (Implemented Baseline)

## Summary

HMI must present user tasks, not pipeline internals.

Primary user flow:
1. Discover
2. Review
3. Fix Documents
4. Search

Pipeline terms (`discovery`, `acquisition`, `parse`) remain in system internals and in `Advanced` only.

## Core UX Principle

User-facing model:
1. `Discover -> Review -> Fix -> Search`

System-facing model:
1. Discovery
2. Acquisition
3. Parse
4. Diagnostics

Rule:
1. Do not require first-time users to understand system stages to complete core tasks.

## Navigation Model

Top-level navigation:
1. `Dashboard`
2. `Discover`
3. `Review`
4. `Documents`
5. `Search`
6. `Advanced`

Meaning:
1. `Dashboard`: overview + next actions.
2. `Discover`: start discovery runs.
3. `Review`: accept/reject found sources.
4. `Documents`: resolve download issues.
5. `Search`: query accepted/parsed knowledge.
6. `Advanced`: runs, logs, IDs, raw technical views.

## One-Screen Dashboard

Dashboard sections:
1. Discover Knowledge
- `Run Discovery` primary button.
2. Things Needing Attention
- `Sources to review` + CTA `Review`.
- `Failed document downloads` + CTA `Fix`.
- `Processing errors` + CTA `Inspect` (to Advanced).
3. Explore Knowledge
- `Search Library` button.
4. Recent Activity
- last run time.
- discovered/accepted/rejected counts.

Dashboard success criterion:
1. User knows what to do next in under 5 seconds.

## Page Specifications

## Discover
Goal:
1. Start discovery with minimal input.

UI:
1. Query list input.
2. `Run Discovery` button.
3. Last run summary (found/accepted/rejected).

Non-goals:
1. No technical IDs in default view.
2. No cross-phase controls.

## Review
Goal:
1. Fast relevance triage.

Table columns:
1. Title
2. Decision (`Accept` / `Reject`)

Expandable per-row details:
1. Abstract
2. Keywords (if available)
3. Citation count (if available)
4. Why panel (`reason_code`, `reason_text`, confidence when available)

Rule:
1. No manual source ID entry in primary review workflow.

## Documents
Goal:
1. Resolve acquisition problems clearly.

Table columns:
1. Title
2. Problem (`Paywalled`, `Blocked`, `Retryable`, `No OA found`)
3. Action (`Upload PDF`, `Retry`, `Open source`)

Rule:
1. User acts from row context only; no manual ID copy/paste.

## Search
Goal:
1. Explore knowledge with minimal friction.

UI:
1. Single search box.
2. Result title + snippet list.
3. Open source/document details on click.

Rule:
1. Avoid technical status fields in primary results.

## Advanced
Goal:
1. Keep full operator/developer control without cluttering primary UX.

Contains:
1. Run tables and filters.
2. Logs and diagnostics.
3. API/system status details.
4. Raw IDs and JSON/debug payloads.

## ID Visibility Policy

Default:
1. Hide `run_id`, `source_id`, `acq_run_id`, `parse_run_id` from task pages.

Allowed visibility:
1. `Advanced`.
2. Explicit "Technical details" drawer.
3. Copy-ID controls for support/debug only.

## Status Presentation Model

Primary UI status model:
1. Green: completed/ready
2. Yellow: needs attention/review/in progress
3. Red: failed/blocker

Rule:
1. Use text + color together.
2. Avoid exposing full backend status taxonomy in primary pages.

## API Contract Requirements

Existing action APIs remain authoritative:
1. Discovery run create/status/source listing.
2. Source review.
3. Acquisition runs/items/manual recovery/upload.
4. Parse and search APIs.

Task-first aggregator/status APIs:
1. `GET /v1/work-queue`
- Task-grouped actionable items for Dashboard/Review/Documents.
2. `GET /v1/system/status`
- Auth mode, AI readiness, provider readiness.
3. `GET /v1/search/global`
- Unified search across runs/sources/acquisition/parse artifacts.

Preferred response additions for task pages:
1. `reason_code`
2. `reason_text`
3. `ui_status` (mapped status for green/yellow/red)

## First-Time User Journey (Acceptance Path)

1. User opens Dashboard.
2. User clicks `Run Discovery`.
3. User reviews sources in `Review`.
4. User fixes failed downloads in `Documents`.
5. User searches corpus in `Search`.

Acceptance criterion:
1. This flow must be executable without reading documentation and without manual ID entry.

## Accessibility and Clarity

1. Primary actions must be text-labeled buttons.
2. Keyboard navigation for task flow must work end-to-end.
3. Important state changes must be visible and understandable without logs.

## Out of Scope

1. WebSocket architecture changes.
2. Frontend framework migration.
3. New auth/RBAC system.
4. Mobile-native application.
