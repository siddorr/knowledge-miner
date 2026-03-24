"""Add paper annotations and session summary settings

Revision ID: 0013_paper_annotations
Revises: 0012_source_query_lineage_fields
Create Date: 2026-03-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0013_paper_annotations"
down_revision = "0012_source_query_lineage_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_tag_catalog",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("tag", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_session_tag_catalog_session_id", "session_tag_catalog", ["session_id"])
    op.create_index("ix_session_tag_catalog_session_id_tag", "session_tag_catalog", ["session_id", "tag"], unique=True)

    op.create_table(
        "session_summary_settings",
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("session_id"),
    )

    op.create_table(
        "paper_annotations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("freeform_tags_json", sa.JSON(), nullable=False),
        sa.Column("approved_tags_json", sa.JSON(), nullable=False),
        sa.Column("ai_summary", sa.Text(), nullable=True),
        sa.Column("summary_status", sa.String(), nullable=False),
        sa.Column("summary_prompt_snapshot", sa.Text(), nullable=True),
        sa.Column("summary_model", sa.String(), nullable=True),
        sa.Column("summary_generated_at", sa.DateTime(), nullable=True),
        sa.Column("summary_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "summary_status IN ('none','queued','running','completed','failed')",
            name="ck_paper_annotations_summary_status_values",
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paper_annotations_session_id", "paper_annotations", ["session_id"])
    op.create_index("ix_paper_annotations_source_id", "paper_annotations", ["source_id"])
    op.create_index(
        "ix_paper_annotations_session_id_source_id",
        "paper_annotations",
        ["session_id", "source_id"],
        unique=True,
    )
    op.create_index(
        "ix_paper_annotations_session_id_updated_at",
        "paper_annotations",
        ["session_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_paper_annotations_session_id_updated_at", table_name="paper_annotations")
    op.drop_index("ix_paper_annotations_session_id_source_id", table_name="paper_annotations")
    op.drop_index("ix_paper_annotations_source_id", table_name="paper_annotations")
    op.drop_index("ix_paper_annotations_session_id", table_name="paper_annotations")
    op.drop_table("paper_annotations")
    op.drop_table("session_summary_settings")
    op.drop_index("ix_session_tag_catalog_session_id_tag", table_name="session_tag_catalog")
    op.drop_index("ix_session_tag_catalog_session_id", table_name="session_tag_catalog")
    op.drop_table("session_tag_catalog")
