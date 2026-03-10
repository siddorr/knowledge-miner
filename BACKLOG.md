# Backlog

## Must-Fix (Spec Compliance)

1. Align default runtime database with v1 spec
- Current default uses SQLite; spec baseline is PostgreSQL.
- Tasks:
  - Set PostgreSQL as documented default for non-test runtime.
  - Keep SQLite test/dev override explicit.
  - Add environment validation warning when running production mode on SQLite.

2. Implement missing source table indexes from spec
- Missing/partial indexes: `(run_id, accepted)`, `(doi)`, and trigram title index.
- Tasks:
  - Add index declarations/migrations.
  - Add migration notes for SQLite compatibility vs PostgreSQL-only trigram.
  - Verify query plans for list/export/dedup paths.

3. Enforce web source allowlist for Brave/vendor/conference coverage
- Spec requires allowlist-controlled domain scope.
- Tasks:
  - Add allowlist config file loader (`config/domains_allowlist.txt`).
  - Filter Brave results by allowlisted domains before ingestion.
  - Add tests for allow/deny domain behavior.

4. Preserve provenance history during dedup merges
- Current merge fills missing fields but does not persist provenance history.
- Tasks:
  - Add append-only provenance history field/store.
  - Record all discovery methods and parent lineage on merge.
  - Expose provenance in export artifact.

5. Apply citation expansion prioritization before truncation
- Spec requires ranking by abstract/DOI/recency/keyword overlap.
- Tasks:
  - Rank citation candidates per spec before applying `per_direction_limit`.
  - Add deterministic tie-breakers.
  - Add tests to validate ranking and truncation order.

6. Add operational observability baseline
- Spec expects structured logs and metrics.
- Tasks:
  - Add structured JSON logs with `run_id`, `iteration`, `provider`, `latency_ms`.
  - Add counters: fetched/accepted/rejected/dedup/api_errors.
  - Add latency histograms per provider call.

