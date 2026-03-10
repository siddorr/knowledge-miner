"""Initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-03-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("seed_queries", sa.JSON(), nullable=False),
        sa.Column("max_iterations", sa.Integer(), nullable=False),
        sa.Column("current_iteration", sa.Integer(), nullable=False),
        sa.Column("accepted_total", sa.Integer(), nullable=False),
        sa.Column("expanded_candidates_total", sa.Integer(), nullable=False),
        sa.Column("citation_edges_total", sa.Integer(), nullable=False),
        sa.Column("new_accept_rate", sa.Numeric(6, 4), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('queued','running','completed','failed')", name="ck_runs_status_values"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "sources",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("doi", sa.Text(), nullable=True),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_native_id", sa.String(), nullable=True),
        sa.Column("patent_office", sa.String(), nullable=True),
        sa.Column("patent_number", sa.String(), nullable=True),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("discovery_method", sa.String(), nullable=False),
        sa.Column("relevance_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("review_status", sa.String(), nullable=False),
        sa.Column("ai_decision", sa.String(), nullable=True),
        sa.Column("ai_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("parent_source_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "discovery_method IN ('seed_search','forward_citation','backward_citation','query_expansion')",
            name="ck_sources_discovery_method_values",
        ),
        sa.CheckConstraint(
            "review_status IN ('auto_accept','auto_reject','needs_review','human_accept','human_reject')",
            name="ck_sources_review_status_values",
        ),
        sa.CheckConstraint("type IN ('academic','web','patent')", name="ck_sources_type_values"),
        sa.CheckConstraint("iteration >= 1", name="ck_sources_iteration_gte_1"),
        sa.CheckConstraint("year IS NULL OR (year BETWEEN 1900 AND 2100)", name="ck_sources_year_range"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sources_iteration", "sources", ["iteration"], unique=False)
    op.create_index("ix_sources_run_id", "sources", ["run_id"], unique=False)

    op.create_table(
        "citation_edges",
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        sa.Column("relationship_type", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "relationship_type IN ('cites','cited_by')",
            name="ck_citation_edges_relationship_type_values",
        ),
        sa.PrimaryKeyConstraint("source_id", "target_id", "relationship_type", "run_id"),
    )

    op.create_table(
        "keywords",
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("iteration", sa.Integer(), nullable=False),
        sa.Column("keyword", sa.String(), nullable=False),
        sa.Column("frequency", sa.Integer(), nullable=False),
        sa.CheckConstraint("frequency >= 1", name="ck_keywords_frequency_gte_1"),
        sa.PrimaryKeyConstraint("run_id", "iteration", "keyword"),
    )


def downgrade() -> None:
    op.drop_table("keywords")
    op.drop_table("citation_edges")
    op.drop_index("ix_sources_run_id", table_name="sources")
    op.drop_index("ix_sources_iteration", table_name="sources")
    op.drop_table("sources")
    op.drop_table("runs")

