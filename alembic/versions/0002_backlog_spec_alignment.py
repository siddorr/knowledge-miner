"""Backlog spec alignment: indexes, provenance, and pg trigram notes.

Revision ID: 0002_backlog_spec_alignment
Revises: 0001_initial_schema
Create Date: 2026-03-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_backlog_spec_alignment"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("ai_filter_active", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("runs", sa.Column("ai_filter_warning", sa.Text(), nullable=True))
    op.add_column(
        "sources",
        sa.Column("provenance_history", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )

    op.create_index("ix_sources_run_id_iteration", "sources", ["run_id", "iteration"], unique=False)
    op.create_index("ix_sources_run_id_accepted", "sources", ["run_id", "accepted"], unique=False)
    op.create_index("ix_sources_doi", "sources", ["doi"], unique=False)

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # PostgreSQL-only trigram support for fuzzy title matching/query acceleration.
        op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        op.execute("CREATE INDEX IF NOT EXISTS idx_sources_title_trgm ON sources USING gin (title gin_trgm_ops)")
    else:
        # SQLite (tests/dev) uses normal btree index via model declaration; no pg_trgm available.
        pass


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS idx_sources_title_trgm")

    op.drop_index("ix_sources_doi", table_name="sources")
    op.drop_index("ix_sources_run_id_accepted", table_name="sources")
    op.drop_index("ix_sources_run_id_iteration", table_name="sources")
    op.drop_column("sources", "provenance_history")
    op.drop_column("runs", "ai_filter_warning")
    op.drop_column("runs", "ai_filter_active")
