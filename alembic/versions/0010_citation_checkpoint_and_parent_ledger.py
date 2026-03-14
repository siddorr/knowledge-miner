"""Add citation checkpoint fields and expanded-parent ledger

Revision ID: 0010_citation_checkpoint_and_parent_ledger
Revises: 0009_query_phase_and_provider_counts
Create Date: 2026-03-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_citation_checkpoint_and_parent_ledger"
down_revision = "0009_query_phase_and_provider_counts"
branch_labels = None
depends_on = None


def _rebuild_discovery_run_queries() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "discovery_run_queries" not in inspector.get_table_names():
        return

    old = sa.Table("discovery_run_queries", sa.MetaData(), autoload_with=bind)
    old_columns = {c.name for c in old.columns}

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
        sa.Column("scope_total_parents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scope_processed_parents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checkpoint_state", sa.String(), nullable=False, server_default="none"),
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
        sa.CheckConstraint("semantic_scholar_count >= 0", name="ck_discovery_run_queries_semantic_scholar_count_gte_0"),
        sa.CheckConstraint("accepted_count >= 0", name="ck_discovery_run_queries_accepted_count_gte_0"),
        sa.CheckConstraint("rejected_count >= 0", name="ck_discovery_run_queries_rejected_count_gte_0"),
        sa.CheckConstraint("pending_count >= 0", name="ck_discovery_run_queries_pending_count_gte_0"),
        sa.CheckConstraint("processing_count >= 0", name="ck_discovery_run_queries_processing_count_gte_0"),
        sa.CheckConstraint("scope_total_parents >= 0", name="ck_discovery_run_queries_scope_total_parents_gte_0"),
        sa.CheckConstraint(
            "scope_processed_parents >= 0",
            name="ck_discovery_run_queries_scope_processed_parents_gte_0",
        ),
        sa.CheckConstraint(
            "checkpoint_state IN ('none','running','resumable','completed','failed')",
            name="ck_discovery_run_queries_checkpoint_state_values",
        ),
        sa.CheckConstraint(
            "status IN ('waiting','searching','ranking_relevance','completed','failed')",
            name="ck_discovery_run_queries_status_values",
        ),
    )

    rows = bind.execute(old.select()).mappings().all()
    for row in rows:
        payload = {
            "id": row["id"],
            "run_id": row["run_id"],
            "query_text": row["query_text"],
            "position": row["position"],
            "status": row["status"],
            "discovered_count": row["discovered_count"],
            "openalex_count": row.get("openalex_count", 0),
            "brave_count": row.get("brave_count", 0),
            "semantic_scholar_count": row.get("semantic_scholar_count", 0),
            "accepted_count": row.get("accepted_count", 0),
            "rejected_count": row.get("rejected_count", 0),
            "pending_count": row.get("pending_count", 0),
            "processing_count": row.get("processing_count", 0),
            "scope_total_parents": row.get("scope_total_parents", 0),
            "scope_processed_parents": row.get("scope_processed_parents", 0),
            "checkpoint_state": row.get("checkpoint_state", "none"),
            "error_message": row.get("error_message"),
            "started_at": row.get("started_at"),
            "completed_at": row.get("completed_at"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        bind.execute(
            sa.text(
                """
                INSERT INTO discovery_run_queries_tmp (
                    id, run_id, query_text, position, status, discovered_count,
                    openalex_count, brave_count, semantic_scholar_count,
                    accepted_count, rejected_count, pending_count, processing_count,
                    scope_total_parents, scope_processed_parents, checkpoint_state,
                    error_message, started_at, completed_at, created_at, updated_at
                ) VALUES (
                    :id, :run_id, :query_text, :position, :status, :discovered_count,
                    :openalex_count, :brave_count, :semantic_scholar_count,
                    :accepted_count, :rejected_count, :pending_count, :processing_count,
                    :scope_total_parents, :scope_processed_parents, :checkpoint_state,
                    :error_message, :started_at, :completed_at, :created_at, :updated_at
                )
                """
            ),
            payload,
        )

    op.drop_table("discovery_run_queries")
    op.rename_table("discovery_run_queries_tmp", "discovery_run_queries")
    op.create_index(
        "ix_discovery_run_queries_run_id_position",
        "discovery_run_queries",
        ["run_id", "position"],
        unique=False,
    )


def upgrade() -> None:
    _rebuild_discovery_run_queries()
    op.create_table(
        "citation_expansion_parents",
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("parent_source_id", sa.String(), nullable=False),
        sa.Column("query_id", sa.String(), nullable=True),
        sa.Column("expanded_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["parent_source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("run_id", "parent_source_id"),
    )
    op.create_index(
        "ix_citation_expansion_parents_run_id_expanded_at",
        "citation_expansion_parents",
        ["run_id", "expanded_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_citation_expansion_parents_run_id_expanded_at", table_name="citation_expansion_parents")
    op.drop_table("citation_expansion_parents")
    _rebuild_discovery_run_queries()
