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
- `source_native_id TEXT NULL`
- `patent_office TEXT NULL`
- `patent_number TEXT NULL`
- `iteration INT NOT NULL`
- `discovery_method TEXT NOT NULL` (`seed_search|forward_citation|backward_citation|query_expansion`)
- `relevance_score NUMERIC(5,2) NOT NULL`
- `accepted BOOLEAN NOT NULL`
- `review_status TEXT NOT NULL` (`auto_accept|auto_reject|needs_review|human_accept|human_reject`)
- `ai_decision TEXT NULL`
- `ai_confidence NUMERIC(4,3) NULL`
- `parent_source_id TEXT NULL`
- `provenance_history JSON NOT NULL` (append-only provenance events)
- `created_at TIMESTAMP NOT NULL`
- `updated_at TIMESTAMP NOT NULL`

Indexes:
- `(run_id, iteration)`
- `(run_id, accepted)`
- `(doi)`
- `title` trigram index for PostgreSQL (`pg_trgm`), btree fallback for SQLite

Note:
- Canonical IDs can be run-scoped (`<canonical>::run:<run_id>`) when a canonical source already exists in another run.

## citation_edges table

- `source_id TEXT NOT NULL`
- `target_id TEXT NOT NULL`
- `relationship_type TEXT NOT NULL` (`cites|cited_by`)
- `run_id TEXT NOT NULL`
- `iteration INT NOT NULL`
- `PRIMARY KEY (source_id, target_id, relationship_type, run_id)`

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
- `expanded_candidates_total INT NOT NULL DEFAULT 0`
- `citation_edges_total INT NOT NULL DEFAULT 0`
- `ai_filter_active BOOLEAN NOT NULL DEFAULT false`
- `ai_filter_warning TEXT NULL`
- `new_accept_rate NUMERIC(6,4) NULL`
- `error_message TEXT NULL`
- `created_at TIMESTAMP NOT NULL`
- `updated_at TIMESTAMP NOT NULL`

## acquisition_runs table

- `id TEXT PRIMARY KEY`
- `discovery_run_id TEXT NOT NULL` (FK `runs.id`)
- `retry_failed_only BOOLEAN NOT NULL DEFAULT false`
- `status TEXT NOT NULL` (`queued|running|completed|failed`)
- `total_sources INT NOT NULL DEFAULT 0`
- `downloaded_total INT NOT NULL DEFAULT 0`
- `partial_total INT NOT NULL DEFAULT 0`
- `failed_total INT NOT NULL DEFAULT 0`
- `skipped_total INT NOT NULL DEFAULT 0`
- `error_message TEXT NULL`
- `created_at TIMESTAMP NOT NULL`
- `updated_at TIMESTAMP NOT NULL`

## acquisition_items table

- `id TEXT PRIMARY KEY`
- `acq_run_id TEXT NOT NULL` (FK `acquisition_runs.id`)
- `source_id TEXT NOT NULL` (FK `sources.id`)
- `status TEXT NOT NULL` (`queued|downloaded|partial|failed|skipped`)
- `attempt_count INT NOT NULL DEFAULT 0`
- `selected_url TEXT NULL`
- `last_error TEXT NULL`
- `created_at TIMESTAMP NOT NULL`
- `updated_at TIMESTAMP NOT NULL`

Indexes:
- `(acq_run_id)`
- `(source_id)`
- `(acq_run_id, status)`

## artifacts table

- `id TEXT PRIMARY KEY`
- `acq_run_id TEXT NOT NULL` (FK `acquisition_runs.id`)
- `source_id TEXT NOT NULL`
- `item_id TEXT NULL` (FK `acquisition_items.id`)
- `kind TEXT NOT NULL` (`pdf|html`)
- `path TEXT NOT NULL` (relative artifact path)
- `checksum_sha256 TEXT NULL`
- `size_bytes INT NULL`
- `mime_type TEXT NULL`
- `created_at TIMESTAMP NOT NULL`

Indexes:
- `(acq_run_id)`
- `(source_id)`
- `(item_id)`
- `(checksum_sha256)`
- `(acq_run_id, source_id)`
