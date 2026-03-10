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
            "review_status IN ('auto_accept','auto_reject','needs_review','human_accept','human_reject')",
            name="ck_sources_review_status_values",
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
