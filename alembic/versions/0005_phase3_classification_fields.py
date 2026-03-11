"""Add Phase 3 classification and AI warning fields.

Revision ID: 0005_phase3_classification_fields
Revises: 0004_phase3_parse_schema
Create Date: 2026-03-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_phase3_classification_fields"
down_revision = "0004_phase3_parse_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("parse_runs", sa.Column("ai_filter_active", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("parse_runs", sa.Column("ai_filter_warning", sa.Text(), nullable=True))

    op.add_column("parsed_documents", sa.Column("relevance_score", sa.Numeric(5, 2), nullable=True))
    op.add_column("parsed_documents", sa.Column("decision", sa.String(), nullable=True))
    op.add_column("parsed_documents", sa.Column("confidence", sa.Numeric(4, 3), nullable=True))
    op.add_column("parsed_documents", sa.Column("reason", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_parsed_documents_decision_values",
        "parsed_documents",
        "decision IS NULL OR decision IN ('auto_accept','needs_review','auto_reject')",
    )

    op.add_column("document_chunks", sa.Column("relevance_score", sa.Numeric(5, 2), nullable=True))
    op.add_column("document_chunks", sa.Column("decision", sa.String(), nullable=True))
    op.add_column("document_chunks", sa.Column("confidence", sa.Numeric(4, 3), nullable=True))
    op.add_column("document_chunks", sa.Column("reason", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_document_chunks_decision_values",
        "document_chunks",
        "decision IS NULL OR decision IN ('auto_accept','needs_review','auto_reject')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_document_chunks_decision_values", "document_chunks", type_="check")
    op.drop_column("document_chunks", "reason")
    op.drop_column("document_chunks", "confidence")
    op.drop_column("document_chunks", "decision")
    op.drop_column("document_chunks", "relevance_score")

    op.drop_constraint("ck_parsed_documents_decision_values", "parsed_documents", type_="check")
    op.drop_column("parsed_documents", "reason")
    op.drop_column("parsed_documents", "confidence")
    op.drop_column("parsed_documents", "decision")
    op.drop_column("parsed_documents", "relevance_score")

    op.drop_column("parse_runs", "ai_filter_warning")
    op.drop_column("parse_runs", "ai_filter_active")
