# Manual Live Logic Test

This workflow verifies the tool end to end against live providers first, with deterministic fallback records used only when a provider does not return enough usable results.

## Purpose

The test covers:

1. Search each enabled provider for 5 documents.
2. Extract and preserve abstracts from provider responses where available.
3. Deduplicate and score relevance with AI-first logic.
4. Rank candidates by AI-first relevance outcome.
5. Select the best 3 academic documents.
6. Research backward and forward citations for those 3 documents.
7. Expand the source pool with citation-derived records.
8. Re-filter the combined source set with AI relevance checks.
9. Attempt PDF download for accepted sources.
10. Export a final sources CSV and, when needed, a manual-downloads CSV.

## Prerequisites

Use a configured environment:

```bash
cd /home/garik/Documents/git/knowledge-miner
source .venv/bin/activate
set -a
[ -f .env ] && source .env || true
set +a
```

Recommended environment:

```bash
USE_MOCK_CONNECTORS=false
BRAVE_API_KEY=...
USE_SEMANTIC_SCHOLAR=true
SEMANTIC_SCHOLAR_API_KEY=...
OPENAI_API_KEY=...
```

Notes:

1. `OpenAlex` does not require an API key.
2. `Semantic Scholar` is optional and will be skipped when disabled.
3. If AI is unavailable, the script falls back to heuristic relevance unless `--require-ai` is passed.

## Run

```bash
python scripts/manual_live_logic_test.py \
  --query "ultrapure water semiconductor" \
  --per-provider 5 \
  --top-k 3 \
  --citations-per-direction 5
```

Optional strict AI mode:

```bash
python scripts/manual_live_logic_test.py --require-ai
```

Wrapper command:

```bash
./run_manual_logic_test.sh \
  --query "ultrapure water semiconductor" \
  --per-provider 5 \
  --top-k 3 \
  --citations-per-direction 5
```

## Outputs

The script writes a timestamped directory under:

```bash
artifacts/manual_logic_test/<timestamp>/
```

Files:

1. `summary.json`
2. `final_sources.csv`
3. `manual_downloads_<acq_run_id>.csv` when acquisition has failed or partial items
4. `sources_raw.json` under the run artifact directory created by the existing export flow

The script also exports accepted-source JSON through the existing `sources_raw` flow.

## Covered Actions

The workflow explicitly exercises these tool actions:

1. Provider search:
   - `OpenAlex`
   - `Semantic Scholar` when enabled
   - `Brave`
2. Abstract extraction:
   - provider abstract fields are collected and persisted where available
3. AI ranking:
   - AI-first relevance decision
   - heuristic fallback when AI is unavailable or fails
4. Research/citation expansion:
   - backward citations
   - forward citations
   - top 3 academic documents only
5. Relevance re-check:
   - citation-derived records are filtered again with the same relevance policy
6. PDF acquisition:
   - accepted documents go through the acquisition engine
7. Source export:
   - final CSV of all resulting sources
   - manual-downloads CSV for unresolved documents

## Pass Interpretation

The run is considered successful when:

1. Every enabled provider contributes 5 records total after live-first search plus fallback supplementation when needed.
2. Three academic documents are selected for citation expansion.
3. Backward and forward citation expansion is attempted for all three.
4. Final CSV output is produced.

PDF download is informational:

1. Some or all downloads may fail.
2. Failures should produce a manual-downloads CSV rather than invalidate the run.

## Warnings You May See

Typical warnings:

1. Live provider returned too few records, fallback rows added.
2. AI unavailable, heuristic fallback used.
3. Live citation expansion returned no rows, fallback citation expansion used.

These warnings are expected in sparse or partially configured environments.
