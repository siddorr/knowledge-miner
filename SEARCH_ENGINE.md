# Search Engine Module

Responsible for retrieving candidate literature sources.

## Data Sources

Academic
- OpenAlex
- Semantic Scholar

Web
- Brave Search API (default) or SerpAPI fallback
- Vendor and conference domains from allowlist only (`config/domains_allowlist.txt`)

Patents
- Google Patents metadata
- EPO metadata (if credentials exist)

## Provider Rules

1. Respect provider ToS and robots limits
2. No broad crawling; max depth 1 from search result URL
3. Retry policy: 3 attempts with 1s/2s/4s backoff for 429 and 5xx
4. Save provider provenance for each source
5. In real-provider mode, set `USE_MOCK_CONNECTORS=false` and provide API keys as needed

## Output

Intermediates are persisted to database tables (`sources`, `citation_edges`, `keywords`).
Final export artifact:
- `sources_raw.json` via `GET /v1/exports/sources_raw?run_id=...`

Fields:
- `id` (canonical)
- `title`
- `year`
- `url`
- `doi`
- `abstract`
- `source`
- `type`
- `discovery_method`
- `iteration`
- `relevance_score`
- `accepted`
- `review_status`
- `parent_source`

Canonical id precedence:
1. DOI
2. OpenAlex id
3. Semantic Scholar id
4. Patent id
5. URL hash
6. Title-year hash

Collision behavior:
- If a canonical source ID already exists in another run, ingestion stores the new record with a run-scoped ID suffix:
  - `<canonical_id>::run:<run_id>`
