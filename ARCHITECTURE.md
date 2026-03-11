# System Architecture

v1 deploys as a single Python service with PostgreSQL and batch-style run execution.

## Pipeline

1. Seed queries
2. Provider search (OpenAlex, Semantic Scholar, Brave or SerpAPI, patents)
3. Candidate normalization
4. Citation expansion
5. Abstract retrieval
6. Relevance scoring
7. Deduplication
8. Review queue handling (`needs_review`)
9. Accepted corpus persistence
10. Keyword extraction
11. Query generation
12. Next iteration
13. Document acquisition (Phase 2): PDF-first download, HTML fallback, artifact indexing

## Runtime Model

1. `POST /v1/discovery/runs` creates a run (`queued` -> `running`)
2. Worker executes up to `max_iterations` (default 6)
3. Each iteration persists checkpoints
4. Run stops on convergence or max iteration
5. Run ends `completed` or `failed`

## Core Components

1. API layer (auth, validation, run control)
2. Connector layer (external provider clients + retry/backoff)
3. Scoring and filtering layer
4. Deduplication layer
5. Iteration planner (keyword extraction and query generation)
6. Persistence layer (PostgreSQL)
7. Export layer (`sources_raw.json`)
8. Acquisition worker layer (download jobs, retries, resume)
9. Artifact storage layer (filesystem paths + DB metadata index)
10. Observability layer (structured JSON logs, counters, latency histograms)

## Decision Engine Policy (AI-First)

1. Heuristic scoring is always computed first as recommendation metadata.
2. AI classifier is the primary auto-decision source for candidate relevance.
3. If AI decision is valid, final decision follows AI output.
4. If AI call fails or times out for a candidate, final decision is forced to `needs_review`.
5. If AI is unavailable at run start (`USE_AI_FILTER=false` or token missing), run is allowed and all candidates default to `needs_review`.
6. Human review (`accept`/`reject`) remains final override for v1.

Invariants:
1. Heuristic does not directly auto-accept/auto-reject in AI-first mode.
2. AI runtime degradation never hard-fails the full discovery run by itself; affected candidates are routed to review.

## Phase 2 Acquisition Runtime

1. `POST /v1/acquisition/runs` queues acquisition for a completed discovery run.
2. Background worker resolves URLs and downloads files per source.
3. Format policy: PDF preferred, HTML snapshot fallback.
4. Progress and outcomes persist in acquisition tables.
5. Manifest endpoint exports complete artifact listing for downstream parsing.

## Observability

Discovery:
1. Structured logs for provider calls with `run_id`, `iteration`, `provider`, `latency_ms`
2. Run summary logs with counters and latency histograms

Acquisition:
1. Structured logs with `acq_run_id`, `source_id`, `domain`, `latency_ms`, `status`
2. Acquisition summary logs with counters (`attempted`, `downloaded`, `partial`, `failed`, `skipped`, `retries`, `api_errors`)

See `V1_SPEC.md` for detailed contracts.
