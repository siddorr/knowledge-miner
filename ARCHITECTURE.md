# Architecture

Knowledge Miner runs as a single Python service with database-backed state, background workers, and filesystem artifact storage.

## 1. System Components

1. API layer:
- request validation
- auth/rate-limit checks
- run orchestration endpoints
2. Discovery engine:
- provider connector calls
- normalization and canonical ID assignment
- citation expansion
- deduplication
- iteration planner
3. Decision engine:
- heuristic scoring metadata
- AI-first final decision policy
- review queue integration
4. Acquisition engine:
- URL resolution and legal-source preference
- file download/retry/resume
- manifest/artifact registration
5. Parse/search engine:
- artifact parsing/chunking
- parse run lifecycle
- chunk-level search
6. HMI layer:
- workstation-style HMI shell
- task pages for `Discover`, `Review`, `Documents`, and `Library Export`
- `Advanced` diagnostics and controls
7. Persistence and observability:
- relational DB for runs/sources/artifacts/parse data
- structured logs and metrics
- filesystem artifacts/runtime state

## 2. End-to-End Data Flow

1. `POST /v1/discovery/runs` creates run (`queued -> running`).
2. Discovery iterations execute provider search, expansion, filtering, dedup, and checkpoint updates.
3. Final decisioning routes candidates to accepted/rejected/review states.
4. Acquisition runs fetch full text for accepted sources and persist artifacts/manifest.
5. Parse runs process artifacts into document/chunk records.
6. Search queries retrieve scored snippets from parsed corpus.
7. HMI exposes session-driven task operations for discovery, review, document recovery, and export/search workflows.

## 3. Pipeline Stages and Ownership

1. Discovery stage:
- connector operations and candidate generation
- citation graph growth
- query expansion and stop-condition control
2. Decision stage:
- AI-first final decisions
- heuristic recommendation metadata
- human review override path
3. Acquisition stage:
- retrieval and artifact indexing
- manual recovery queue
4. Parse/search stage:
- text extraction and chunk indexing
- search endpoints and UX actions

Detailed decision logic and thresholds are defined in `PIPELINE_RULES.md`.

## 4. Runtime Behavior

1. Runs are asynchronous and checkpointed.
2. Startup performs runtime lock cleanup and instance lock acquisition.
3. Retries are bounded and policy-driven.
4. Terminal run statuses are `completed` or `failed`.
5. Task pages in HMI avoid mandatory manual ID entry; advanced controls remain available only in `Advanced` unless explicitly surfaced by the UI contract.

## 5. HMI Shell Architecture

The approved HMI shell model is:
1. Header/status row
2. Controls row
3. Navigation row
4. Main workspace
5. Footer

UI architecture rules:
1. Primary operator workflow is session-bound.
2. `Advanced` is the only diagnostics-first screen.
3. Task pages should remain operator-oriented and avoid technical identifiers in the primary flow.
4. Review is bound to the active session context, not manual run-ID selection in the primary pane.

## 6. External Dependencies

1. Search/discovery providers (OpenAlex, Semantic Scholar, Brave; optional others).
2. AI provider for classification when enabled.
3. Filesystem for artifacts/logs/runtime state.
4. Database backend for persistent run and corpus data.

## 7. API and Service Boundaries

1. API handlers are thin and delegate business logic to service modules.
2. Connectors are isolated from scoring/dedup logic.
3. Decision policy is independent from transport/provider clients.
4. UI task flows consume stable API contracts (`work-queue`, discovery/review, acquisition/manual recovery, parse/search).

## 8. Operational Observability

1. Structured logs include run and provider context.
2. Stage summaries include counters and failure classifications.
3. Status endpoints expose auth/provider/AI readiness for operators.
