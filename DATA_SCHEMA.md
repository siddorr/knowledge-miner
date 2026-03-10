# Data Schema

## sources table

- `id TEXT PRIMARY KEY`
- `run_id TEXT NOT NULL`
- `title TEXT NOT NULL`
- `year INT NULL CHECK (year BETWEEN 1900 AND 2100)`
- `url TEXT NULL`
- `doi TEXT NULL`
- `abstract TEXT NULL`
- `type TEXT NOT NULL` (`academic|web|patent`)
- `source TEXT NOT NULL` (provider name)
- `iteration INT NOT NULL`
- `discovery_method TEXT NOT NULL` (`seed_search|forward_citation|backward_citation|query_expansion`)
- `relevance_score NUMERIC(5,2) NOT NULL`
- `accepted BOOLEAN NOT NULL`
- `review_status TEXT NOT NULL` (`auto_accept|auto_reject|needs_review|human_accept|human_reject`)
- `parent_source_id TEXT NULL`
- `created_at TIMESTAMP NOT NULL`
- `updated_at TIMESTAMP NOT NULL`

Indexes:
- `(run_id, iteration)`
- `(run_id, accepted)`
- `(doi)`
- title trigram index

## citation_edges table

- `source_id TEXT NOT NULL`
- `target_id TEXT NOT NULL`
- `relationship_type TEXT NOT NULL`
- `run_id TEXT NOT NULL`
- `iteration INT NOT NULL`
- `PRIMARY KEY (source_id, target_id, relationship_type, run_id)`

relationship_type values:
- `cites`
- `cited_by`

## keywords table

- `run_id TEXT NOT NULL`
- `iteration INT NOT NULL`
- `keyword TEXT NOT NULL`
- `frequency INT NOT NULL`
- `PRIMARY KEY (run_id, iteration, keyword)`

## runs table

- `id TEXT PRIMARY KEY`
- `status TEXT NOT NULL` (`queued|running|completed|failed`)
- `seed_queries JSONB NOT NULL`
- `max_iterations INT NOT NULL DEFAULT 6`
- `current_iteration INT NOT NULL DEFAULT 0`
- `accepted_total INT NOT NULL DEFAULT 0`
- `new_accept_rate NUMERIC(6,4) NULL`
- `error_message TEXT NULL`
- `created_at TIMESTAMP NOT NULL`
- `updated_at TIMESTAMP NOT NULL`

For full constraints and semantics, see `V1_SPEC.md`.
