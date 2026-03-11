"""Add Phase 3 parse schema.

Revision ID: 0004_phase3_parse_schema
Revises: 0003_acquisition_phase2_schema
Create Date: 2026-03-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_phase3_parse_schema"
down_revision = "0003_acquisition_phase2_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "parse_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("acq_run_id", sa.String(), nullable=False),
        sa.Column("retry_failed_only", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("total_documents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parsed_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunked_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('queued','running','completed','failed')", name="ck_parse_runs_status_values"),
        sa.CheckConstraint("total_documents >= 0", name="ck_parse_runs_total_documents_gte_0"),
        sa.CheckConstraint("parsed_total >= 0", name="ck_parse_runs_parsed_total_gte_0"),
        sa.CheckConstraint("failed_total >= 0", name="ck_parse_runs_failed_total_gte_0"),
        sa.CheckConstraint("chunked_total >= 0", name="ck_parse_runs_chunked_total_gte_0"),
        sa.ForeignKeyConstraint(["acq_run_id"], ["acquisition_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_parse_runs_acq_run_id", "parse_runs", ["acq_run_id"], unique=False)

    op.create_table(
        "parsed_documents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("parse_run_id", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("artifact_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(), nullable=True),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("parser_used", sa.String(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("section_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('queued','parsed','failed','skipped')", name="ck_parsed_documents_status_values"),
        sa.CheckConstraint("char_count >= 0", name="ck_parsed_documents_char_count_gte_0"),
        sa.CheckConstraint("section_count >= 0", name="ck_parsed_documents_section_count_gte_0"),
        sa.ForeignKeyConstraint(["parse_run_id"], ["parse_runs.id"]),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_parsed_documents_parse_run_id", "parsed_documents", ["parse_run_id"], unique=False)
    op.create_index("ix_parsed_documents_source_id", "parsed_documents", ["source_id"], unique=False)
    op.create_index("ix_parsed_documents_artifact_id", "parsed_documents", ["artifact_id"], unique=False)
    op.create_index("ix_parsed_documents_content_hash", "parsed_documents", ["content_hash"], unique=False)
    op.create_index("ix_parsed_documents_parse_run_id_status", "parsed_documents", ["parse_run_id", "status"], unique=False)

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("parse_run_id", sa.String(), nullable=False),
        sa.Column("parsed_document_id", sa.String(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("chunk_index >= 0", name="ck_document_chunks_chunk_index_gte_0"),
        sa.CheckConstraint("start_char >= 0", name="ck_document_chunks_start_char_gte_0"),
        sa.CheckConstraint("end_char >= 0", name="ck_document_chunks_end_char_gte_0"),
        sa.CheckConstraint("end_char >= start_char", name="ck_document_chunks_end_char_gte_start_char"),
        sa.ForeignKeyConstraint(["parse_run_id"], ["parse_runs.id"]),
        sa.ForeignKeyConstraint(["parsed_document_id"], ["parsed_documents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_document_chunks_parse_run_id", "document_chunks", ["parse_run_id"], unique=False)
    op.create_index("ix_document_chunks_parsed_document_id", "document_chunks", ["parsed_document_id"], unique=False)
    op.create_index("ix_document_chunks_content_hash", "document_chunks", ["content_hash"], unique=False)
    op.create_index(
        "ix_document_chunks_document_chunk_index",
        "document_chunks",
        ["parsed_document_id", "chunk_index"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_document_chunk_index", table_name="document_chunks")
    op.drop_index("ix_document_chunks_content_hash", table_name="document_chunks")
    op.drop_index("ix_document_chunks_parsed_document_id", table_name="document_chunks")
    op.drop_index("ix_document_chunks_parse_run_id", table_name="document_chunks")
    op.drop_table("document_chunks")

    op.drop_index("ix_parsed_documents_parse_run_id_status", table_name="parsed_documents")
    op.drop_index("ix_parsed_documents_content_hash", table_name="parsed_documents")
    op.drop_index("ix_parsed_documents_artifact_id", table_name="parsed_documents")
    op.drop_index("ix_parsed_documents_source_id", table_name="parsed_documents")
    op.drop_index("ix_parsed_documents_parse_run_id", table_name="parsed_documents")
    op.drop_table("parsed_documents")

    op.drop_index("ix_parse_runs_acq_run_id", table_name="parse_runs")
    op.drop_table("parse_runs")
