# UPW Literature Discovery Engine

Automated literature discovery for Ultrapure Water (UPW) systems in semiconductor manufacturing.

## v1 Scope

In scope:
1. Search query execution
2. Citation expansion (forward and backward)
3. Abstract retrieval and relevance scoring
4. Deduplication
5. Iterative query generation
6. Corpus storage and export

Out of scope:
1. PDF download and parsing
2. Knowledge graph and clustering
3. UI for manual report generation

## v1 Targets

1. 2000+ accepted unique sources within 6 iterations
2. Precision@50 >= 0.80 (human spot check)
3. Accepted duplicate rate <= 2%

## Source of Truth

Primary implementation contract: `V1_SPEC.md`.

Supporting docs:
1. `ARCHITECTURE.md`
2. `SEARCH_ENGINE.md`
3. `CITATION_EXPANSION.md`
4. `ABSTRACT_FILTER.md`
5. `ITERATION_PROCESS.md`
6. `DATA_SCHEMA.md`
7. `DEVELOPMENT_PLAN.md`
