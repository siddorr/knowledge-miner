# Pipeline Rules

This file defines discovery/relevance decision rules and iteration behavior.

## 1. Provider Search Rules

Sources:
1. OpenAlex
2. Semantic Scholar
3. Brave Search (SerpAPI fallback)
4. Patent metadata sources when configured

Constraints:
1. Respect provider ToS and robots limits.
2. Domain-restricted web search uses allowlist (`config/domains_allowlist.txt`).
3. No broad crawling; max depth 1 from search result URL.
4. Outbound retry policy: 3 attempts, 1s/2s/4s backoff, retry on 429/5xx.

## 2. Canonical ID Rules

Canonical source ID precedence:
1. DOI
2. OpenAlex id
3. Semantic Scholar id
4. Patent id
5. URL hash
6. Title-year hash

Collision behavior:
1. Cross-run collisions may use run-scoped suffix for PK safety.

## 3. Citation Expansion Rules

1. Backward expansion: references cited by source.
2. Forward expansion: sources citing the source.
3. Per-direction limit: 50 items.
4. Pagination stops on provider exhaustion or limit reached.

Prioritization before truncation:
1. has abstract
2. has DOI
3. newer publication year
4. higher keyword overlap
5. deterministic tie-break (normalized title + canonical id)

## 4. Heuristic Scoring Rules

Scoring input:
1. `title + abstract`
2. case-insensitive whole-word/phrase matching

Weights:
1. domain keyword: +2.0
2. process keyword: +1.5
3. contamination keyword: +1.0
4. negative keyword: -3.0

Heuristic recommendation thresholds:
1. score >= 5.0 -> `auto_accept`
2. 3.0 <= score < 5.0 -> `needs_review`
3. score < 3.0 -> `auto_reject`

## 5. Final Decision Policy (AI-First)

1. AI classifier is the primary final auto-decision source.
2. Heuristic score/recommendation is retained as reviewer context metadata.
3. If AI call fails/times out/errors for candidate: final decision is `needs_review`.
4. If AI is unavailable at run start: run is allowed, candidates route to `needs_review`.
5. Human review endpoint (`accept`/`reject`) remains final override.

Decision-source values:
1. `ai`
2. `fallback_heuristic`
3. `policy_no_ai`
4. `human_review`

## 6. Deduplication Rules

Order:
1. exact DOI
2. exact canonical URL
3. exact provider-native id
4. fuzzy title+year match (normalized title similarity threshold + year tolerance)

Merge policy:
1. keep most complete record fields
2. preserve provenance/discovery lineage
3. avoid duplicate accepted records

## 7. Iteration Query Generation Rules

Per iteration:
1. extract keywords from accepted `title + abstract`
2. rank candidate keywords by frequency and relevance
3. generate up to 10 next queries from fixed templates
4. require anchor term presence (`ultrapure water` or `UPW` or `semiconductor`)
5. reject drifted queries dominated by negatives unless both `UPW` and `semiconductor` are present

## 8. Stop Conditions

1. Stop when `new_accepted_unique / accepted_total < 0.05` for 2 consecutive iterations.
2. Force stop when iteration count reaches max configured limit (default 6).

Definitions:
1. `new_accepted_unique`: newly accepted after dedup in current iteration
2. `accepted_total`: cumulative accepted after dedup up to current iteration

## 9. Review Triggers

Candidates route to review when:
1. final decision is `needs_review`
2. AI unavailable/degraded
3. operator override requested

Review queue behavior:
1. `needs_review` sources are excluded from accepted corpus until reviewed.
2. Human decision updates review status and final decision source.
