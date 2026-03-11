# Abstract Relevance Filtering

Candidate sources are filtered based on abstract relevance.

## Keyword Scoring

Domain keywords:
- ultrapure water
- UPW
- semiconductor
- wafer cleaning

Process keywords:
- RO
- EDI
- UV185
- UV254
- degassing
- mixed bed

Contamination keywords:
- particles
- TOC
- trace metals
- silica
- bacteria

Negative keywords:
- drinking water
- desalination
- agriculture irrigation

## Weights

1. Domain keyword match: +2.0 each
2. Process keyword match: +1.5 each
3. Contamination keyword match: +1.0 each
4. Negative keyword match: -3.0 each

Scoring text input:
- `title + abstract`
- case-insensitive phrase matching
- whole word or phrase matching

## Decision Rules

1. score >= 5.0 -> ACCEPT (`auto_accept`)
2. score >= 3.0 and < 5.0 -> REVIEW (`needs_review`)
3. score < 3.0 -> REJECT (`auto_reject`)

## Review Workflow

1. `needs_review` records are excluded from accepted corpus until reviewed.
2. Human review endpoint: `POST /v1/sources/{source_id}/review`
3. Decision values: `accept` or `reject`
4. Human decision is final for v1 (`human_accept` or `human_reject`)

## AI Override Runtime Signal

Run status includes:
1. `ai_filter_active` (`true` when AI override is enabled and token is configured)
2. `ai_filter_warning` (set when heuristic-only mode is active)
