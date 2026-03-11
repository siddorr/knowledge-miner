# UPW Literature Discovery Engine - v1 Specification

Status: Approved baseline for implementation
Date: 2026-03-10

Implementation update (current repository state as of 2026-03-11):
- v1 baseline is implemented.
- Phase 2 document acquisition is also implemented in addition to this baseline.
- For current operational behavior, read `DEVELOPMENT_PLAN.md`, `BACKLOG.md`, and module docs.

## 1. MVP Boundary

In scope (v1):
- Phase 1 only from `DEVELOPMENT_PLAN.md`:
  - query execution
  - citation expansion
  - abstract filtering
  - deduplication
  - iterative query refinement
  - corpus persistence

Out of scope (v1):
- PDF download/storage
- full document parsing
- entity/relationship extraction from full text
- knowledge graph construction
- topic clustering
- manual report generation UI

## 2. System Interfaces

Deployment model:
- Single Python service + PostgreSQL
- Batch jobs triggered by API

Auth:
- API key in `Authorization: Bearer <token>`
- Single tenant for v1

Rate limiting:
- 60 requests/min per API key

Retry policy (outbound APIs):
- max 3 attempts
- exponential backoff: 1s, 2s, 4s
- retry on 429 and 5xx

## 3. API Contract

### 3.1 POST `/v1/discovery/runs`
Start one iterative discovery run.

Request:
```json
{
  "seed_queries": ["ultrapure water semiconductor", "UPW wafer cleaning"],
  "max_iterations": 6
}
```

Response `202`:
```json
{
  "run_id": "run_01H...",
  "status": "queued"
}
```

### 3.2 GET `/v1/discovery/runs/{run_id}`
Get run status and metrics.

Response `200`:
```json
{
  "run_id": "run_01H...",
  "status": "running",
  "current_iteration": 2,
  "accepted_total": 413,
  "new_accept_rate": 0.18
}
```

### 3.3 GET `/v1/discovery/runs/{run_id}/sources`
List accepted sources.

Query params:
- `limit` (default 100, max 1000)
- `offset` (default 0)
- `type` optional (`academic|web|patent`)
- `min_score` optional

### 3.4 POST `/v1/sources/{source_id}/review`
Human override for borderline items.

Request:
```json
{
  "decision": "accept",
  "note": "Directly discusses TOC control in UPW loop"
}
```

Response `200`:
```json
{
  "source_id": "src_...",
  "accepted": true,
  "decision_source": "human_review"
}
```

### 3.5 GET `/v1/exports/sources_raw?run_id=...`
Export normalized artifact.

### 3.6 Acquisition endpoints (implemented extension)
- `POST /v1/acquisition/runs`
- `GET /v1/acquisition/runs/{acq_run_id}`
- `GET /v1/acquisition/runs/{acq_run_id}/items`
- `GET /v1/acquisition/artifacts/{artifact_id}`
- `GET /v1/acquisition/runs/{acq_run_id}/manifest`

Errors:
- `400 invalid_request`
- `401 unauthorized`
- `404 run_not_found`
- `409 run_not_complete`
- `429 rate_limited`
- `500 internal_error`

## 4. Data Model (PostgreSQL)

### 4.1 `sources`
- `id TEXT PRIMARY KEY` (canonical, see ID rules)
- `run_id TEXT NOT NULL`
- `title TEXT NOT NULL`
- `year INT NULL CHECK (year BETWEEN 1900 AND 2100)`
- `url TEXT NULL`
- `doi TEXT NULL`
- `abstract TEXT NULL`
- `type TEXT NOT NULL CHECK (type IN ('academic','web','patent'))`
- `source TEXT NOT NULL` (provider: openalex, semscholar, brave, etc.)
- `iteration INT NOT NULL CHECK (iteration >= 1)`
- `discovery_method TEXT NOT NULL CHECK (discovery_method IN ('seed_search','forward_citation','backward_citation','query_expansion'))`
- `relevance_score NUMERIC(5,2) NOT NULL`
- `accepted BOOLEAN NOT NULL`
- `review_status TEXT NOT NULL CHECK (review_status IN ('auto_accept','auto_reject','needs_review','human_accept','human_reject'))`
- `parent_source_id TEXT NULL`
- `created_at TIMESTAMP NOT NULL DEFAULT NOW()`
- `updated_at TIMESTAMP NOT NULL DEFAULT NOW()`

Indexes:
- `idx_sources_run_iteration (run_id, iteration)`
- `idx_sources_accepted (run_id, accepted)`
- `idx_sources_doi (doi)`
- `idx_sources_title_trgm` using trigram

### 4.2 `citation_edges`
- `source_id TEXT NOT NULL`
- `target_id TEXT NOT NULL`
- `relationship_type TEXT NOT NULL CHECK (relationship_type IN ('cites','cited_by'))`
- `run_id TEXT NOT NULL`
- `iteration INT NOT NULL`
- `PRIMARY KEY (source_id, target_id, relationship_type, run_id)`

### 4.3 `keywords`
- `run_id TEXT NOT NULL`
- `iteration INT NOT NULL`
- `keyword TEXT NOT NULL`
- `frequency INT NOT NULL CHECK (frequency >= 1)`
- `PRIMARY KEY (run_id, iteration, keyword)`

### 4.4 `runs`
- `id TEXT PRIMARY KEY`
- `status TEXT NOT NULL CHECK (status IN ('queued','running','completed','failed'))`
- `seed_queries JSONB NOT NULL`
- `max_iterations INT NOT NULL DEFAULT 6`
- `current_iteration INT NOT NULL DEFAULT 0`
- `accepted_total INT NOT NULL DEFAULT 0`
- `new_accept_rate NUMERIC(6,4) NULL`
- `error_message TEXT NULL`
- `created_at TIMESTAMP NOT NULL DEFAULT NOW()`
- `updated_at TIMESTAMP NOT NULL DEFAULT NOW()`

## 5. Identifier Strategy

Canonical source id precedence:
1. DOI -> `doi:<lowercased_doi>`
2. OpenAlex id -> `openalex:<id>`
3. Semantic Scholar id -> `s2:<id>`
4. Patent number -> `patent:<office>:<number>`
5. URL hash -> `urlsha1:<sha1(canonical_url)>`
6. Fallback title/year hash -> `titleyearsha1:<sha1(norm_title|year)>`

Provider IDs are stored in metadata JSON (future field) if available.

Cross-run storage note:
- Canonical IDs remain the dedup identity.
- When the same canonical source appears in a different run, storage may apply a run-scoped suffix for primary-key safety:
  - `<canonical_id>::run:<run_id>`

## 6. Deduplication Rules

Dedup pass order:
1. Exact DOI match
2. Exact canonical URL match
3. Exact provider-native id match
4. Fuzzy title+year candidate match:
   - normalize title (lowercase, remove punctuation, collapse spaces)
   - similarity >= 0.92 (Jaro-Winkler or trigram equivalent)
   - year difference <= 1

Merge policy:
- keep record with most complete fields (`abstract`, `doi`, `year`, `url`)
- preserve all provenance in `discovery_method` history (append-only log in future; for v1 keep first method and parent id)

## 7. Scoring and Filtering

Keyword groups and weights:
- Domain keywords: +2 each
- Process keywords: +1.5 each
- Contamination keywords: +1 each
- Negative keywords: -3 each

Phrase matching:
- case-insensitive
- whole word/phrase
- search in `title + abstract`

Final score:
`score = sum(matches * weight)`

Decision:
- `score >= 5.0` -> `ACCEPT` (`auto_accept`)
- `3.0 <= score < 5.0` -> `REVIEW` (`needs_review`)
- `score < 3.0` -> `REJECT` (`auto_reject`)

## 8. Review Workflow

Rules:
- `needs_review` sources are excluded from accepted corpus until reviewed
- reviewer can `accept` or `reject`
- decision updates:
  - `accepted=true/false`
  - `review_status=human_accept|human_reject`
  - `relevance_score` unchanged in v1
- human decisions are final in v1 (no second-level approval)

## 9. Iteration Query Generation

Per iteration:
1. Collect top accepted sources from latest iteration
2. Extract candidate keywords from `title + abstract`:
   - tokenize
   - remove stopwords
   - keep noun-like tokens and domain phrases (rule-based dictionary + ngram frequency)
3. Select top 20 keywords by frequency
4. Build up to 10 next queries using templates

Templates:
- `"<k1> <k2> semiconductor UPW"`
- `"<k1> process control ultrapure water"`
- `"<k1> contamination wafer cleaning"`

Drift guardrails:
- every generated query must include at least one anchor term:
  - `ultrapure water`, `UPW`, or `semiconductor`
- reject generated queries containing any negative keyword unless also containing `semiconductor` and `UPW`

## 10. Iteration and Stop Conditions

Max iterations: 6

Primary stop condition:
- stop if `new_accepted_unique / accepted_total < 0.05` for 2 consecutive iterations

Definitions:
- `new_accepted_unique`: newly accepted sources after dedup in current iteration
- `accepted_total`: cumulative accepted sources after dedup through current iteration

Fail-safe:
- force stop at iteration 6

## 11. Source Coverage Rules

Academic:
- OpenAlex
- Semantic Scholar

Web:
- Brave Search API (default) or SerpAPI fallback
- allowlist-only vendor/conference domains in config file `config/domains_allowlist.txt`
- max crawl depth: 1 from search result URL (no broad crawling)

Patents:
- Google Patents metadata
- EPO metadata (if API key available)

Robots/legal:
- respect provider ToS
- do not scrape blocked endpoints
- store only metadata/abstract links in v1

## 12. Citation Expansion Rules

Limits (per seed/accepted source):
- backward references max 50
- forward citations max 50

Prioritization before truncation:
1. has abstract
2. has DOI
3. more recent year
4. source relevance by keyword overlap

Pagination:
- fetch until limit reached or provider exhausted

## 13. Output Artifact Contract (`sources_raw.json`)

Schema:
```json
{
  "schema_version": "1.0",
  "run_id": "run_...",
  "generated_at": "ISO-8601",
  "sources": [
    {
      "id": "doi:10....",
      "title": "string",
      "year": 2021,
      "url": "https://...",
      "doi": "10....",
      "abstract": "string",
      "type": "academic",
      "source": "openalex",
      "iteration": 2,
      "discovery_method": "forward_citation",
      "relevance_score": 6.5,
      "accepted": true,
      "review_status": "auto_accept",
      "parent_source": "doi:..."
    }
  ],
  "provenance": {
    "seed_queries": [],
    "apis_used": ["openalex", "semantic_scholar", "brave"]
  }
}
```

Ordering:
- sort `sources` by:
  1. `accepted desc`
  2. `relevance_score desc`
  3. `year desc nulls last`
  4. `id asc`

## 14. Measurable Quality Targets

v1 acceptance criteria:
- At least 2000 accepted unique sources after <=6 iterations on full run
- Precision@50 for accepted sources >= 0.80 (human spot-check on random 50)
- Duplicate rate among accepted <= 2%
- Run completion rate >= 95% across 20 consecutive runs

## 15. Operational Requirements

Runtime:
- target full run completion: <= 3 hours

Concurrency:
- max 5 concurrent runs
- max 10 outbound API requests in parallel per run

Storage:
- PostgreSQL for core records
- local object storage path for exports: `./artifacts/{run_id}/`

Logging:
- structured JSON logs with `run_id`, `iteration`, `provider`, `event`, `latency_ms`

Monitoring:
- counters: sources_fetched, accepted_count, rejected_count, dedup_count, api_errors
- latency histograms per provider call

Failure recovery:
- iteration checkpoint persisted in `runs`
- rerun resumes from last completed iteration
