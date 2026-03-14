"""Add internal repository base URL to acquisition runs

Revision ID: 0010_internal_repository_acquisition_url
Revises: 0009_query_phase_and_provider_counts
Create Date: 2026-03-14
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0010_internal_repository_acquisition_url"
down_revision = "0009_query_phase_and_provider_counts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("acquisition_runs", sa.Column("internal_repository_base_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("acquisition_runs", "internal_repository_base_url")
