> Deprecated: This document is archived and not an active product spec. Use ../UI_SPEC.md, ../CURRENT_SCOPE.md, and ../BACKLOG.md as source of truth.

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

## Decision Rules (AI-First Policy)

Heuristic thresholds are recommendation-only under AI-first policy:

1. score >= 5.0 -> heuristic recommendation `auto_accept`
2. score >= 3.0 and < 5.0 -> heuristic recommendation `needs_review`
3. score < 3.0 -> heuristic recommendation `auto_reject`

Final decision policy:

| AI available | AI call result | Final decision | Decision source |
|---|---|---|---|
| yes | valid | AI decision | `ai` |
| yes | failed/timeout/error | `needs_review` | `fallback_heuristic` |
| no (disabled/missing key) | n/a | `needs_review` | `policy_no_ai` |
| human review applied | n/a | human decision | `human_review` |

## Review Workflow

1. `needs_review` records are excluded from accepted corpus until reviewed.
2. Human review endpoint: `POST /v1/sources/{source_id}/review`
3. Decision values: `accept` or `reject`
4. Human decision is final for v1 (`human_accept` or `human_reject`)

## AI Override Runtime Signal

Run status includes:
1. `ai_filter_active` (`true` when AI primary mode is enabled and token is configured)
2. `ai_filter_warning` (set when AI is disabled or token is missing)

In AI-first mode:
1. Heuristic score/recommendation is always stored for reviewer context.
2. AI failure does not promote heuristic to final decision; final decision remains `needs_review` until AI success or human review.
