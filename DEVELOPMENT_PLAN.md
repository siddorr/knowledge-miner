# Development Plan

## v1 Delivery Plan (In Scope)

Phase 1 - Core service skeleton
- API scaffolding and auth
- run lifecycle (`queued/running/completed/failed`)
- PostgreSQL schema and migrations

Phase 2 - Search connectors
- OpenAlex connector
- Semantic Scholar connector
- Brave search connector (SerpAPI fallback)
- provider retry/backoff and error handling

Phase 3 - Processing pipeline
- normalization and canonical ID assignment
- citation expansion (forward and backward)
- abstract scoring and decision classification
- deduplication pipeline

Phase 4 - Iteration engine
- keyword extraction from accepted corpus
- guarded query generation
- stop-condition evaluation
- checkpoint/resume support

Phase 5 - Review and export
- review endpoint for `needs_review` records
- `sources_raw.json` export endpoint
- run metrics and basic monitoring counters

## Post-v1 Backlog (Out of Scope for v1)

1. PDF acquisition and storage
2. Full-text parsing
3. Entity and relationship extraction
4. Knowledge graph
5. Topic clustering
6. Manual generation tooling
