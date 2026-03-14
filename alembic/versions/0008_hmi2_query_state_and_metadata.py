"""Add discovery run query tracking and source metadata fields

Revision ID: 0008_hmi2_query_state_and_metadata
Revises: 0007_phase22_legal_resolution_fields
Create Date: 2026-03-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_hmi2_query_state_and_metadata"
down_revision = "0007_phase22_legal_resolution_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_run_queries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("discovered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("position >= 1", name="ck_discovery_run_queries_position_gte_1"),
        sa.CheckConstraint("discovered_count >= 0", name="ck_discovery_run_queries_discovered_count_gte_0"),
        sa.CheckConstraint(
            "status IN ('waiting','running','completed','failed')",
            name="ck_discovery_run_queries_status_values",
        ),
    )
    op.create_index(
        "ix_discovery_run_queries_run_id_position",
        "discovery_run_queries",
        ["run_id", "position"],
        unique=False,
    )

    op.add_column("sources", sa.Column("journal", sa.Text(), nullable=True))
    op.add_column("sources", sa.Column("authors", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column("sources", sa.Column("citation_count", sa.Integer(), nullable=True))
    op.alter_column("sources", "authors", server_default=None)


def downgrade() -> None:
    op.drop_column("sources", "citation_count")
    op.drop_column("sources", "authors")
    op.drop_column("sources", "journal")
    op.drop_index("ix_discovery_run_queries_run_id_position", table_name="discovery_run_queries")
    op.drop_table("discovery_run_queries")
