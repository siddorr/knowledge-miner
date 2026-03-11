# Iterative Search Process

Each iteration performs:

1. Run search queries
2. Collect candidate sources
3. Expand citations
4. Retrieve abstracts
5. Apply relevance filter
6. Deduplicate
7. Queue borderline sources for review
8. Store accepted sources
9. Extract keywords from accepted `title + abstract`
10. Generate next queries with drift guardrails
11. Start next iteration if stop conditions are not met

## Query Generation Rules

1. Select top keywords by frequency from latest accepted set
2. Generate up to 10 queries using fixed templates
3. Each query must include at least one anchor term:
   - `ultrapure water` or `UPW` or `semiconductor`
4. Reject generated queries dominated by negative keywords unless both `UPW` and `semiconductor` are present

## Stop Conditions

1. Stop if `new_accepted_unique / accepted_total < 0.05` for 2 consecutive iterations
2. Force stop when iteration count >= 6

Definitions:
1. `new_accepted_unique`: accepted sources added in current iteration after dedup
2. `accepted_total`: cumulative accepted sources after dedup up to current iteration
