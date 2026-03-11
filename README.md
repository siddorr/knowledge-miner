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

Implemented extension beyond v1 baseline:
1. Phase 2 document acquisition (PDF-first, HTML fallback)
2. Acquisition API and manifest artifact generation

Still out of scope:
1. Full-text parsing
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

## Quick Start

```bash
cd /home/garik/Documents/git/knowledge-miner
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn knowledge_miner.main:app --reload
```

Use header:
- `Authorization: Bearer dev-token`

## Real Provider Search

To run against real providers instead of mock data:

```bash
cd /home/garik/Documents/git/knowledge-miner
cp .env.example .env
# edit .env and set BRAVE_API_KEY / SEMANTIC_SCHOLAR_API_KEY
set -a
source .env
set +a
```

Required settings:
1. `USE_MOCK_CONNECTORS=false`
2. `BRAVE_API_KEY=<your_key>`
3. `SEMANTIC_SCHOLAR_API_KEY=<your_key>` (optional but recommended)

Note:
1. OpenAlex does not require an API key.
2. Real-provider mode requires host network access (sandboxed runs may not have DNS/internet).
