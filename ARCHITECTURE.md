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

See `V1_SPEC.md` for detailed contracts.
