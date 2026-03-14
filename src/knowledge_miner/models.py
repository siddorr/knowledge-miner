from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','completed','failed')", name="ck_runs_status_values"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    seed_queries: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    current_iteration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expanded_candidates_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    citation_edges_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_filter_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_filter_warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_accept_rate: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class DiscoveryRunQuery(Base):
    __tablename__ = "discovery_run_queries"
    __table_args__ = (
        CheckConstraint("position >= 1", name="ck_discovery_run_queries_position_gte_1"),
        CheckConstraint("discovered_count >= 0", name="ck_discovery_run_queries_discovered_count_gte_0"),
        CheckConstraint("openalex_count >= 0", name="ck_discovery_run_queries_openalex_count_gte_0"),
        CheckConstraint("brave_count >= 0", name="ck_discovery_run_queries_brave_count_gte_0"),
        CheckConstraint("semantic_scholar_count >= 0", name="ck_discovery_run_queries_semantic_scholar_count_gte_0"),
        CheckConstraint("accepted_count >= 0", name="ck_discovery_run_queries_accepted_count_gte_0"),
        CheckConstraint("rejected_count >= 0", name="ck_discovery_run_queries_rejected_count_gte_0"),
        CheckConstraint("pending_count >= 0", name="ck_discovery_run_queries_pending_count_gte_0"),
        CheckConstraint("processing_count >= 0", name="ck_discovery_run_queries_processing_count_gte_0"),
        CheckConstraint(
            "checkpoint_state IN ('none','running','resumable','completed','failed')",
            name="ck_discovery_run_queries_checkpoint_state_values",
        ),
        CheckConstraint(
            "status IN ('waiting','searching','ranking_relevance','completed','failed')",
            name="ck_discovery_run_queries_status_values",
        ),
        Index("ix_discovery_run_queries_run_id_position", "run_id", "position"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="waiting")
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    openalex_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    brave_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    semantic_scholar_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processing_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scope_total_parents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scope_processed_parents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checkpoint_state: Mapped[str] = mapped_column(String, nullable=False, default="none")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("iteration >= 1", name="ck_sources_iteration_gte_1"),
        CheckConstraint("year IS NULL OR (year BETWEEN 1900 AND 2100)", name="ck_sources_year_range"),
        CheckConstraint("type IN ('academic','web','patent')", name="ck_sources_type_values"),
        CheckConstraint(
            "discovery_method IN ('seed_search','forward_citation','backward_citation','query_expansion')",
            name="ck_sources_discovery_method_values",
        ),
        CheckConstraint(
            "review_status IN ('auto_accept','auto_reject','needs_review','human_accept','human_reject','human_later')",
            name="ck_sources_review_status_values",
        ),
        CheckConstraint(
            "final_decision IN ('auto_accept','auto_reject','needs_review','human_accept','human_reject','human_later')",
            name="ck_sources_final_decision_values",
        ),
        CheckConstraint(
            "decision_source IN ('ai','fallback_heuristic','policy_no_ai','human_review')",
            name="ck_sources_decision_source_values",
        ),
        Index("ix_sources_run_id_iteration", "run_id", "iteration"),
        Index("ix_sources_run_id_accepted", "run_id", "accepted"),
        Index("ix_sources_doi", "doi"),
        # PostgreSQL can apply trigram ops via migration; this generic index keeps
        # SQLite/local behavior consistent for create_all paths.
        Index("ix_sources_title", "title"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(Text, nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    journal: Mapped[str | None] = mapped_column(Text, nullable=True)
    authors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    citation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    source_native_id: Mapped[str | None] = mapped_column(String, nullable=True)
    patent_office: Mapped[str | None] = mapped_column(String, nullable=True)
    patent_number: Mapped[str | None] = mapped_column(String, nullable=True)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    discovery_method: Mapped[str] = mapped_column(String, nullable=False)
    relevance_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    review_status: Mapped[str] = mapped_column(String, nullable=False)
    final_decision: Mapped[str] = mapped_column(String, nullable=False, default="needs_review")
    decision_source: Mapped[str] = mapped_column(String, nullable=False, default="policy_no_ai")
    heuristic_recommendation: Mapped[str] = mapped_column(String, nullable=False, default="needs_review")
    heuristic_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=0.0)
    ai_decision: Mapped[str | None] = mapped_column(String, nullable=True)
    ai_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    parent_source_id: Mapped[str | None] = mapped_column(String, nullable=True)
    provenance_history: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class CitationEdge(Base):
    __tablename__ = "citation_edges"
    __table_args__ = (
        PrimaryKeyConstraint("source_id", "target_id", "relationship_type", "run_id"),
        CheckConstraint("relationship_type IN ('cites','cited_by')", name="ck_citation_edges_relationship_type_values"),
    )

    source_id: Mapped[str] = mapped_column(String, nullable=False)
    target_id: Mapped[str] = mapped_column(String, nullable=False)
    relationship_type: Mapped[str] = mapped_column(String, nullable=False)
    run_id: Mapped[str] = mapped_column(String, nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)


class CitationExpansionParent(Base):
    __tablename__ = "citation_expansion_parents"
    __table_args__ = (
        PrimaryKeyConstraint("run_id", "parent_source_id"),
        Index("ix_citation_expansion_parents_run_id_expanded_at", "run_id", "expanded_at"),
    )

    run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False)
    parent_source_id: Mapped[str] = mapped_column(String, ForeignKey("sources.id"), nullable=False)
    query_id: Mapped[str | None] = mapped_column(String, nullable=True)
    expanded_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class Keyword(Base):
    __tablename__ = "keywords"
    __table_args__ = (
        PrimaryKeyConstraint("run_id", "iteration", "keyword"),
        CheckConstraint("frequency >= 1", name="ck_keywords_frequency_gte_1"),
    )

    run_id: Mapped[str] = mapped_column(String, nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False)
    keyword: Mapped[str] = mapped_column(String, nullable=False)
    frequency: Mapped[int] = mapped_column(Integer, nullable=False)


class AcquisitionRun(Base):
    __tablename__ = "acquisition_runs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','completed','failed')", name="ck_acquisition_runs_status_values"),
        CheckConstraint("total_sources >= 0", name="ck_acquisition_runs_total_sources_gte_0"),
        CheckConstraint("downloaded_total >= 0", name="ck_acquisition_runs_downloaded_total_gte_0"),
        CheckConstraint("partial_total >= 0", name="ck_acquisition_runs_partial_total_gte_0"),
        CheckConstraint("failed_total >= 0", name="ck_acquisition_runs_failed_total_gte_0"),
        CheckConstraint("skipped_total >= 0", name="ck_acquisition_runs_skipped_total_gte_0"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    discovery_run_id: Mapped[str] = mapped_column(String, ForeignKey("runs.id"), nullable=False, index=True)
    retry_failed_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    internal_repository_base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    total_sources: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    downloaded_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    partial_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class AcquisitionItem(Base):
    __tablename__ = "acquisition_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','downloaded','partial','failed','skipped')",
            name="ck_acquisition_items_status_values",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_acquisition_items_attempt_count_gte_0"),
        CheckConstraint(
            "reason_code IS NULL OR reason_code IN ('paywalled','no_oa_found','rate_limited','robots_blocked','source_error','manual_complete')",
            name="ck_acquisition_items_reason_code_values",
        ),
        Index("ix_acquisition_items_acq_run_id_status", "acq_run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    acq_run_id: Mapped[str] = mapped_column(String, ForeignKey("acquisition_runs.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String, ForeignKey("sources.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    selected_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_url_source: Mapped[str | None] = mapped_column(String, nullable=True)
    resolution_attempts: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    reason_code: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        CheckConstraint("kind IN ('pdf','html')", name="ck_artifacts_kind_values"),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_artifacts_size_bytes_gte_0"),
        Index("ix_artifacts_acq_run_id_source_id", "acq_run_id", "source_id"),
        Index("ix_artifacts_checksum_sha256", "checksum_sha256"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    acq_run_id: Mapped[str] = mapped_column(String, ForeignKey("acquisition_runs.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    item_id: Mapped[str | None] = mapped_column(String, ForeignKey("acquisition_items.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class ParseRun(Base):
    __tablename__ = "parse_runs"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','completed','failed')", name="ck_parse_runs_status_values"),
        CheckConstraint("total_documents >= 0", name="ck_parse_runs_total_documents_gte_0"),
        CheckConstraint("parsed_total >= 0", name="ck_parse_runs_parsed_total_gte_0"),
        CheckConstraint("failed_total >= 0", name="ck_parse_runs_failed_total_gte_0"),
        CheckConstraint("chunked_total >= 0", name="ck_parse_runs_chunked_total_gte_0"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    acq_run_id: Mapped[str] = mapped_column(String, ForeignKey("acquisition_runs.id"), nullable=False, index=True)
    retry_failed_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_filter_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ai_filter_warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    total_documents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parsed_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunked_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class ParsedDocument(Base):
    __tablename__ = "parsed_documents"
    __table_args__ = (
        CheckConstraint("status IN ('queued','parsed','failed','skipped')", name="ck_parsed_documents_status_values"),
        CheckConstraint(
            "decision IS NULL OR decision IN ('auto_accept','needs_review','auto_reject')",
            name="ck_parsed_documents_decision_values",
        ),
        CheckConstraint("char_count >= 0", name="ck_parsed_documents_char_count_gte_0"),
        CheckConstraint("section_count >= 0", name="ck_parsed_documents_section_count_gte_0"),
        Index("ix_parsed_documents_parse_run_id_status", "parse_run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    parse_run_id: Mapped[str] = mapped_column(String, ForeignKey("parse_runs.id"), nullable=False, index=True)
    source_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    artifact_id: Mapped[str] = mapped_column(String, ForeignKey("artifacts.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parser_used: Mapped[str | None] = mapped_column(String, nullable=True)
    relevance_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    decision: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    section_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        CheckConstraint("chunk_index >= 0", name="ck_document_chunks_chunk_index_gte_0"),
        CheckConstraint("start_char >= 0", name="ck_document_chunks_start_char_gte_0"),
        CheckConstraint("end_char >= 0", name="ck_document_chunks_end_char_gte_0"),
        CheckConstraint("end_char >= start_char", name="ck_document_chunks_end_char_gte_start_char"),
        CheckConstraint(
            "decision IS NULL OR decision IN ('auto_accept','needs_review','auto_reject')",
            name="ck_document_chunks_decision_values",
        ),
        Index("ix_document_chunks_document_chunk_index", "parsed_document_id", "chunk_index"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    parse_run_id: Mapped[str] = mapped_column(String, ForeignKey("parse_runs.id"), nullable=False, index=True)
    parsed_document_id: Mapped[str] = mapped_column(String, ForeignKey("parsed_documents.id"), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    relevance_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    decision: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
