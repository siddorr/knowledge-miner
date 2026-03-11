"""Add AI-first decision provenance fields to sources.

Revision ID: 0006_phase41_ai_first_decision_fields
Revises: 0005_phase3_classification_fields
Create Date: 2026-03-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_phase41_ai_first_decision_fields"
down_revision = "0005_phase3_classification_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("final_decision", sa.String(), nullable=False, server_default=sa.text("'needs_review'")))
    op.add_column("sources", sa.Column("decision_source", sa.String(), nullable=False, server_default=sa.text("'policy_no_ai'")))
    op.add_column(
        "sources",
        sa.Column("heuristic_recommendation", sa.String(), nullable=False, server_default=sa.text("'needs_review'")),
    )
    op.add_column("sources", sa.Column("heuristic_score", sa.Numeric(5, 2), nullable=False, server_default=sa.text("0")))

    op.execute("UPDATE sources SET final_decision = review_status")
    op.execute(
        """
        UPDATE sources
        SET decision_source = CASE
            WHEN ai_decision IS NOT NULL THEN 'ai'
            ELSE 'fallback_heuristic'
        END
        """
    )
    op.execute("UPDATE sources SET heuristic_recommendation = review_status")
    op.execute("UPDATE sources SET heuristic_score = relevance_score")

    op.create_check_constraint(
        "ck_sources_final_decision_values",
        "sources",
        "final_decision IN ('auto_accept','auto_reject','needs_review','human_accept','human_reject')",
    )
    op.create_check_constraint(
        "ck_sources_decision_source_values",
        "sources",
        "decision_source IN ('ai','fallback_heuristic','policy_no_ai','human_review')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_sources_decision_source_values", "sources", type_="check")
    op.drop_constraint("ck_sources_final_decision_values", "sources", type_="check")
    op.drop_column("sources", "heuristic_score")
    op.drop_column("sources", "heuristic_recommendation")
    op.drop_column("sources", "decision_source")
    op.drop_column("sources", "final_decision")
