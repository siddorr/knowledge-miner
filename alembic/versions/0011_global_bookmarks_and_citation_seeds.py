"""Add global bookmarks and citation seed ledger

Revision ID: 0011_global_bookmarks_and_citation_seeds
Revises: 0010_citation_checkpoint_and_parent_ledger, 0010_internal_repository_acquisition_url
Create Date: 2026-03-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_global_bookmarks_and_citation_seeds"
down_revision = ("0010_citation_checkpoint_and_parent_ledger", "0010_internal_repository_acquisition_url")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bookmarks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("doi", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_session_id", sa.String(), nullable=True),
        sa.Column("source_run_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.ForeignKeyConstraint(["source_run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id"),
    )
    op.create_index("ix_bookmarks_created_at", "bookmarks", ["created_at"])
    op.create_index("ix_bookmarks_source_id", "bookmarks", ["source_id"])
    op.create_index("ix_bookmarks_source_session_id", "bookmarks", ["source_session_id"])
    op.create_index("ix_bookmarks_title", "bookmarks", ["title"])

    op.create_table(
        "discovery_citation_seeds",
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("seed_source_id", sa.String(), nullable=False),
        sa.Column("origin_bookmark_id", sa.String(), nullable=True),
        sa.Column("origin_session_id", sa.String(), nullable=True),
        sa.Column("seed_kind", sa.String(), nullable=False, server_default="bookmark"),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"]),
        sa.ForeignKeyConstraint(["seed_source_id"], ["sources.id"]),
        sa.ForeignKeyConstraint(["origin_bookmark_id"], ["bookmarks.id"]),
        sa.PrimaryKeyConstraint("run_id", "seed_source_id"),
        sa.CheckConstraint("position >= 1", name="ck_discovery_citation_seeds_position_gte_1"),
        sa.CheckConstraint("seed_kind IN ('bookmark')", name="ck_discovery_citation_seeds_kind_values"),
    )
    op.create_index(
        "ix_discovery_citation_seeds_run_id_position",
        "discovery_citation_seeds",
        ["run_id", "position"],
    )


def downgrade() -> None:
    op.drop_index("ix_discovery_citation_seeds_run_id_position", table_name="discovery_citation_seeds")
    op.drop_table("discovery_citation_seeds")
    op.drop_index("ix_bookmarks_title", table_name="bookmarks")
    op.drop_index("ix_bookmarks_source_session_id", table_name="bookmarks")
    op.drop_index("ix_bookmarks_source_id", table_name="bookmarks")
    op.drop_index("ix_bookmarks_created_at", table_name="bookmarks")
    op.drop_table("bookmarks")
