from __future__ import annotations

from pydantic import BaseModel, Field


class RunCreateRequest(BaseModel):
    seed_queries: list[str] = Field(min_length=1)
    max_iterations: int = Field(default=6, ge=1, le=6)


class RunCreateResponse(BaseModel):
    run_id: str
    status: str


class RunStatusResponse(BaseModel):
    run_id: str
    status: str
    current_iteration: int
    accepted_total: int
    expanded_candidates_total: int
    citation_edges_total: int
    ai_filter_active: bool
    ai_filter_warning: str | None
    new_accept_rate: float | None


class SourceReviewRequest(BaseModel):
    decision: str
    note: str | None = None


class SourceReviewResponse(BaseModel):
    source_id: str
    accepted: bool
    decision_source: str


class SourceOut(BaseModel):
    id: str
    title: str
    year: int | None
    url: str | None
    doi: str | None
    abstract: str | None
    type: str
    source: str
    iteration: int
    discovery_method: str
    relevance_score: float
    accepted: bool
    review_status: str
    parent_source: str | None


class SourcesListResponse(BaseModel):
    items: list[SourceOut]
    total: int
    limit: int
    offset: int
