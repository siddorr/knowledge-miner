# Citation Expansion

Expands the literature network using references and citations.

## Backward Expansion
Retrieve references cited by the paper.

## Forward Expansion
Retrieve papers citing the paper.

## Limits
max references per paper = 50
max citations per paper = 50

## Prioritization Before Truncation

1. Has abstract
2. Has DOI
3. Newer publication year
4. Higher keyword overlap with UPW domain terms
5. Deterministic tie-break by normalized title and canonical target id

## Pagination

Fetch pages until:
1. provider is exhausted, or
2. 50 items are collected for backward/forward expansion.

## Data Stored
`source_id -> cites|cited_by -> target_id` in `citation_edges`.

## Notes

1. Expansion candidates still go through scoring and dedup.
2. Duplicates are not inserted as new source records.
