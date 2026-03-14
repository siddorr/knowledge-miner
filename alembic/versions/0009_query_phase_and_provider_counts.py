"""Add query phase/provider counters for HMI2 discovery timeline

Revision ID: 0009_query_phase_and_provider_counts
Revises: 0008_hmi2_query_state_and_metadata
Create Date: 2026-03-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009_query_phase_and_provider_counts"
down_revision = "0008_hmi2_query_state_and_metadata"
branch_labels = None
depends_on = None


def _rebuild_discovery_run_queries(*, include_new_fields: bool) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "discovery_run_queries" not in inspector.get_table_names():
        return

    old = sa.Table("discovery_run_queries", sa.MetaData(), autoload_with=bind)

    op.create_table(
        "discovery_run_queries_tmp",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("discovered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("openalex_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("brave_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("semantic_scholar_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processing_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("position >= 1", name="ck_discovery_run_queries_position_gte_1"),
        sa.CheckConstraint("discovered_count >= 0", name="ck_discovery_run_queries_discovered_count_gte_0"),
        sa.CheckConstraint("openalex_count >= 0", name="ck_discovery_run_queries_openalex_count_gte_0"),
        sa.CheckConstraint("brave_count >= 0", name="ck_discovery_run_queries_brave_count_gte_0"),
        sa.CheckConstraint(
            "semantic_scholar_count >= 0",
            name="ck_discovery_run_queries_semantic_scholar_count_gte_0",
        ),
        sa.CheckConstraint("accepted_count >= 0", name="ck_discovery_run_queries_accepted_count_gte_0"),
        sa.CheckConstraint("rejected_count >= 0", name="ck_discovery_run_queries_rejected_count_gte_0"),
        sa.CheckConstraint("pending_count >= 0", name="ck_discovery_run_queries_pending_count_gte_0"),
        sa.CheckConstraint("processing_count >= 0", name="ck_discovery_run_queries_processing_count_gte_0"),
        sa.CheckConstraint(
            "status IN ('waiting','searching','ranking_relevance','completed','failed')"
            if include_new_fields
            else "status IN ('waiting','running','completed','failed')",
            name="ck_discovery_run_queries_status_values",
        ),
    )

    old_columns = {c.name for c in old.columns}
    query = old.select()
    rows = bind.execute(query).mappings().all()
    for row in rows:
        payload = {
            "id": row["id"],
            "run_id": row["run_id"],
            "query_text": row["query_text"],
            "position": row["position"],
            "status": row["status"],
            "discovered_count": row["discovered_count"],
            "openalex_count": row["openalex_count"] if "openalex_count" in old_columns else 0,
            "brave_count": row["brave_count"] if "brave_count" in old_columns else 0,
            "semantic_scholar_count": row["semantic_scholar_count"] if "semantic_scholar_count" in old_columns else 0,
            "accepted_count": row["accepted_count"] if "accepted_count" in old_columns else 0,
            "rejected_count": row["rejected_count"] if "rejected_count" in old_columns else 0,
            "pending_count": row["pending_count"] if "pending_count" in old_columns else 0,
            "processing_count": row["processing_count"] if "processing_count" in old_columns else 0,
            "error_message": row.get("error_message"),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        bind.execute(sa.text(
            """
            INSERT INTO discovery_run_queries_tmp (
                id, run_id, query_text, position, status, discovered_count,
                openalex_count, brave_count, semantic_scholar_count,
                accepted_count, rejected_count, pending_count, processing_count,
                error_message, started_at, completed_at, created_at, updated_at
            ) VALUES (
                :id, :run_id, :query_text, :position, :status, :discovered_count,
                :openalex_count, :brave_count, :semantic_scholar_count,
                :accepted_count, :rejected_count, :pending_count, :processing_count,
                :error_message, :started_at, :completed_at, :created_at, :updated_at
            )
            """
        ), payload)

    op.drop_table("discovery_run_queries")
    op.rename_table("discovery_run_queries_tmp", "discovery_run_queries")
    op.create_index(
        "ix_discovery_run_queries_run_id_position",
        "discovery_run_queries",
        ["run_id", "position"],
        unique=False,
    )


def upgrade() -> None:
    _rebuild_discovery_run_queries(include_new_fields=True)


def downgrade() -> None:
    _rebuild_discovery_run_queries(include_new_fields=False)
