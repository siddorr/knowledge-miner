"""Phase 2.2 legal-resolution fields on acquisition_items

Revision ID: 0007_phase22_legal_resolution_fields
Revises: 0006_phase41_ai_first_decision_fields
Create Date: 2026-03-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_phase22_legal_resolution_fields"
down_revision = "0006_phase41_ai_first_decision_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("acquisition_items", sa.Column("selected_url_source", sa.String(), nullable=True))
    op.add_column(
        "acquisition_items",
        sa.Column("resolution_attempts", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column("acquisition_items", sa.Column("reason_code", sa.String(), nullable=True))
    op.create_check_constraint(
        "ck_acquisition_items_reason_code_values",
        "acquisition_items",
        "reason_code IS NULL OR reason_code IN ('paywalled','no_oa_found','rate_limited','robots_blocked','source_error')",
    )
    op.alter_column("acquisition_items", "resolution_attempts", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_acquisition_items_reason_code_values", "acquisition_items", type_="check")
    op.drop_column("acquisition_items", "reason_code")
    op.drop_column("acquisition_items", "resolution_attempts")
    op.drop_column("acquisition_items", "selected_url_source")
