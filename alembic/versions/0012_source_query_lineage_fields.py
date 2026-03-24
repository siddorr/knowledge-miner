"""Add persistent source query lineage fields

Revision ID: 0012_source_query_lineage_fields
Revises: 0011_global_bookmarks_and_citation_seeds
Create Date: 2026-03-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_source_query_lineage_fields"
down_revision = "0011_global_bookmarks_and_citation_seeds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("query_id", sa.String(), nullable=True))
    op.add_column("sources", sa.Column("query_step_number", sa.Integer(), nullable=True))
    op.add_column("sources", sa.Column("query_source_number", sa.Integer(), nullable=True))
    op.create_index("ix_sources_query_id", "sources", ["query_id"])
    op.create_index("ix_sources_run_query_lineage", "sources", ["run_id", "query_id", "query_source_number"])


def downgrade() -> None:
    op.drop_index("ix_sources_run_query_lineage", table_name="sources")
    op.drop_index("ix_sources_query_id", table_name="sources")
    op.drop_column("sources", "query_source_number")
    op.drop_column("sources", "query_step_number")
    op.drop_column("sources", "query_id")
