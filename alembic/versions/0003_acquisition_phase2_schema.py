"""Add Phase 2 acquisition tables.

Revision ID: 0003_acquisition_phase2_schema
Revises: 0002_backlog_spec_alignment
Create Date: 2026-03-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_acquisition_phase2_schema"
down_revision = "0002_backlog_spec_alignment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "acquisition_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("discovery_run_id", sa.String(), nullable=False),
        sa.Column("retry_failed_only", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("total_sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("downloaded_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("partial_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('queued','running','completed','failed')", name="ck_acquisition_runs_status_values"),
        sa.CheckConstraint("total_sources >= 0", name="ck_acquisition_runs_total_sources_gte_0"),
        sa.CheckConstraint("downloaded_total >= 0", name="ck_acquisition_runs_downloaded_total_gte_0"),
        sa.CheckConstraint("partial_total >= 0", name="ck_acquisition_runs_partial_total_gte_0"),
        sa.CheckConstraint("failed_total >= 0", name="ck_acquisition_runs_failed_total_gte_0"),
        sa.CheckConstraint("skipped_total >= 0", name="ck_acquisition_runs_skipped_total_gte_0"),
        sa.ForeignKeyConstraint(["discovery_run_id"], ["runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_acquisition_runs_discovery_run_id", "acquisition_runs", ["discovery_run_id"], unique=False)

    op.create_table(
        "acquisition_items",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("acq_run_id", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("selected_url", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued','downloaded','partial','failed','skipped')",
            name="ck_acquisition_items_status_values",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_acquisition_items_attempt_count_gte_0"),
        sa.ForeignKeyConstraint(["acq_run_id"], ["acquisition_runs.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_acquisition_items_acq_run_id", "acquisition_items", ["acq_run_id"], unique=False)
    op.create_index("ix_acquisition_items_source_id", "acquisition_items", ["source_id"], unique=False)
    op.create_index(
        "ix_acquisition_items_acq_run_id_status",
        "acquisition_items",
        ["acq_run_id", "status"],
        unique=False,
    )

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("acq_run_id", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("item_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("checksum_sha256", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('pdf','html')", name="ck_artifacts_kind_values"),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_artifacts_size_bytes_gte_0"),
        sa.ForeignKeyConstraint(["acq_run_id"], ["acquisition_runs.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["acquisition_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_artifacts_acq_run_id", "artifacts", ["acq_run_id"], unique=False)
    op.create_index("ix_artifacts_source_id", "artifacts", ["source_id"], unique=False)
    op.create_index("ix_artifacts_item_id", "artifacts", ["item_id"], unique=False)
    op.create_index("ix_artifacts_checksum_sha256", "artifacts", ["checksum_sha256"], unique=False)
    op.create_index("ix_artifacts_acq_run_id_source_id", "artifacts", ["acq_run_id", "source_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_artifacts_acq_run_id_source_id", table_name="artifacts")
    op.drop_index("ix_artifacts_checksum_sha256", table_name="artifacts")
    op.drop_index("ix_artifacts_item_id", table_name="artifacts")
    op.drop_index("ix_artifacts_source_id", table_name="artifacts")
    op.drop_index("ix_artifacts_acq_run_id", table_name="artifacts")
    op.drop_table("artifacts")

    op.drop_index("ix_acquisition_items_acq_run_id_status", table_name="acquisition_items")
    op.drop_index("ix_acquisition_items_source_id", table_name="acquisition_items")
    op.drop_index("ix_acquisition_items_acq_run_id", table_name="acquisition_items")
    op.drop_table("acquisition_items")

    op.drop_index("ix_acquisition_runs_discovery_run_id", table_name="acquisition_runs")
    op.drop_table("acquisition_runs")
