from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class RunCreateRequest(BaseModel):
    seed_queries: list[str] = Field(min_length=1)
    selected_queries: list[str] | None = None
    max_iterations: int = Field(default=6, ge=1, le=6)
    ai_filter_enabled: bool | None = None


class CitationIterationRequest(BaseModel):
    selected_queries: list[str] | None = None
    ai_filter_enabled: bool | None = None


class RunCreateResponse(BaseModel):
    run_id: str
    status: str


class RunStatusResponse(BaseModel):
    run_id: str
    status: str
    seed_queries: list[str]
    current_iteration: int
    accepted_total: int
    expanded_candidates_total: int
    citation_edges_total: int
    ai_filter_active: bool
    ai_filter_warning: str | None
    ai_filter_effective_enabled: bool
    ai_filter_config_source: str
    new_accept_rate: float | None
    current_stage: str
    stage_status: str
    completed: int
    total: int
    percent: float | None
    message: str
    started_at: str | None
    updated_at: str | None


class DiscoveryRunQueryOut(BaseModel):
    query: str
    position: int
    status: str
    discovered_count: int
    openalex_count: int = 0
    brave_count: int = 0
    semantic_scholar_count: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    pending_count: int = 0
    processing_count: int = 0
    scope_total_parents: int = 0
    scope_processed_parents: int = 0
    checkpoint_state: str = "none"
    error_message: str | None


class DiscoveryRunQueriesResponse(BaseModel):
    run_id: str
    queries: list[DiscoveryRunQueryOut]


class SourceReviewRequest(BaseModel):
    decision: str
    run_id: str | None = Field(default=None, min_length=1)
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
    doi_url: str | None
    abstract: str | None
    journal: str | None
    authors: list[str] = Field(default_factory=list)
    citation_count: int | None
    type: str
    source: str
    iteration: int
    discovery_method: str
    relevance_score: float
    accepted: bool
    review_status: str
    final_decision: str
    decision_source: str
    heuristic_recommendation: str
    heuristic_score: float
    parent_source: str | None


class SourcesListResponse(BaseModel):
    items: list[SourceOut]
    total: int
    limit: int
    offset: int


class AcquisitionRunCreateRequest(BaseModel):
    run_id: str = Field(min_length=1)
    retry_failed_only: bool = False
    selected_source_ids: list[str] | None = None
    internal_repository_base_url: str | None = None


class AcquisitionRunCreateResponse(BaseModel):
    acq_run_id: str
    status: str


class AcquisitionRunStatusResponse(BaseModel):
    acq_run_id: str
    discovery_run_id: str
    retry_failed_only: bool
    status: str
    total_sources: int
    downloaded_total: int
    partial_total: int
    failed_total: int
    skipped_total: int
    error_message: str | None
    current_stage: str
    stage_status: str
    completed: int
    total: int
    percent: float | None
    message: str
    started_at: str | None
    updated_at: str | None


class AcquisitionItemOut(BaseModel):
    item_id: str
    source_id: str
    status: str
    attempt_count: int
    selected_url: str | None
    last_error: str | None


class AcquisitionItemsListResponse(BaseModel):
    items: list[AcquisitionItemOut]
    total: int
    limit: int
    offset: int


class ArtifactOut(BaseModel):
    artifact_id: str
    acq_run_id: str
    source_id: str
    item_id: str | None
    kind: str
    path: str
    checksum_sha256: str | None
    size_bytes: int | None
    mime_type: str | None


class AcquisitionManifestResponse(BaseModel):
    acq_run_id: str
    discovery_run_id: str
    status: str
    generated_at: str
    totals: dict
    items: list[dict]
    artifacts: list[dict]


class ManualDownloadItemOut(BaseModel):
    item_id: str
    source_id: str
    status: str
    attempt_count: int
    last_error: str | None
    title: str
    doi: str | None
    source_url: str | None
    selected_url: str | None
    manual_url_candidates: list[str]
    legal_candidates: list[dict] = Field(default_factory=list)
    reason_code: str | None = None


class ManualDownloadsListResponse(BaseModel):
    acq_run_id: str
    items: list[ManualDownloadItemOut]
    total: int
    limit: int
    offset: int


class ManualUploadResponse(BaseModel):
    artifact_id: str
    acq_run_id: str
    source_id: str
    kind: str
    path: str
    checksum_sha256: str | None
    size_bytes: int | None
    mime_type: str | None


class ManualUploadRequest(BaseModel):
    source_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    content_base64: str = Field(min_length=1)
    content_type: str | None = None


class ManualCompleteRequest(BaseModel):
    source_id: str = Field(min_length=1)


class BatchUploadMatchOut(BaseModel):
    filename: str
    status: str
    source_id: str | None = None
    score: float | None = None
    reason: str | None = None


class BatchUploadResponse(BaseModel):
    acq_run_id: str
    matched: int
    unmatched: int
    ambiguous: int
    items: list[BatchUploadMatchOut]


class ParseRunCreateRequest(BaseModel):
    acq_run_id: str = Field(min_length=1)
    retry_failed_only: bool = False


class ParseRunCreateResponse(BaseModel):
    parse_run_id: str
    status: str


class ParseRunStatusResponse(BaseModel):
    parse_run_id: str
    acq_run_id: str
    retry_failed_only: bool
    ai_filter_active: bool
    ai_filter_warning: str | None
    status: str
    total_documents: int
    parsed_total: int
    failed_total: int
    chunked_total: int
    error_message: str | None
    current_stage: str
    stage_status: str
    completed: int
    total: int
    percent: float | None
    message: str
    started_at: str | None
    updated_at: str | None


class ParsedDocumentOut(BaseModel):
    document_id: str
    source_id: str
    artifact_id: str
    status: str
    title: str | None
    publication_year: int | None
    language: str | None
    parser_used: str | None
    relevance_score: float | None
    decision: str | None
    confidence: float | None
    reason: str | None
    char_count: int
    section_count: int
    last_error: str | None


class ParsedDocumentsListResponse(BaseModel):
    items: list[ParsedDocumentOut]
    total: int
    limit: int
    offset: int


class DocumentChunkOut(BaseModel):
    chunk_id: str
    document_id: str
    chunk_index: int
    relevance_score: float | None
    decision: str | None
    confidence: float | None
    reason: str | None
    start_char: int
    end_char: int
    text: str


class DocumentChunksListResponse(BaseModel):
    items: list[DocumentChunkOut]
    total: int
    limit: int
    offset: int


class ParsedDocumentTextResponse(BaseModel):
    document_id: str
    text: str


class SearchRequest(BaseModel):
    parse_run_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    limit: int = Field(default=20, ge=1, le=100)


class SearchResultOut(BaseModel):
    document_id: str
    chunk_id: str
    source_id: str
    score: float
    snippet: str


class SearchResponse(BaseModel):
    items: list[SearchResultOut]
    total: int


class WorkQueueItemOut(BaseModel):
    item_type: str
    phase: str
    run_id: str
    source_id: str | None = None
    item_id: str | None = None
    status: str
    title: str | None = None
    reason_code: str | None = None
    reason_text: str | None = None
    context: dict = Field(default_factory=dict)


class WorkQueueResponse(BaseModel):
    items: list[WorkQueueItemOut]
    total: int
    limit: int
    offset: int


class GlobalSearchResultOut(BaseModel):
    result_type: str
    id: str
    label: str
    snippet: str | None = None
    context: dict = Field(default_factory=dict)


class GlobalSearchResponse(BaseModel):
    query: str
    items: list[GlobalSearchResultOut]
    total: int


class SystemStatusResponse(BaseModel):
    auth_enabled: bool
    auth_mode: str
    ai_filter_active: bool
    ai_filter_warning: str | None
    provider_readiness: dict
    db_ready: bool
    db_missing_tables: list[str]
    db_error: str | None
    database_target: str
    db_target_url: str
    db_target_resolved_path: str | None
    db_schema_ready: bool
    db_run_count: int | None
    process_pid: int
    hot_read_metrics: dict


class AISettingsUpdateRequest(BaseModel):
    use_ai_filter: bool | None = None
    ai_api_key: str | None = None
    ai_model: str | None = None
    ai_base_url: str | None = None


class AISettingsResponse(BaseModel):
    use_ai_filter: bool
    ai_filter_active: bool
    ai_filter_warning: str | None
    has_api_key: bool
    api_key_masked: str | None
    ai_model: str
    ai_base_url: str


class ProviderSettingsUpdateRequest(BaseModel):
    openalex_search_limit: int | None = Field(default=None, ge=1, le=200)
    brave_search_count: int | None = Field(default=None, ge=1, le=20)
    brave_require_allowlist: bool | None = None


class ProviderSettingsResponse(BaseModel):
    openalex_search_limit: int
    brave_search_count: int
    brave_require_allowlist: bool


class HMIEventIn(BaseModel):
    event_type: Literal["click", "change", "input", "submit", "navigate"]
    control_id: str = Field(min_length=1, max_length=120)
    control_label: str | None = Field(default=None, max_length=160)
    page: str = Field(min_length=1, max_length=64)
    section: str | None = Field(default=None, max_length=64)
    session_id: str = Field(min_length=1, max_length=120)
    run_id: str | None = Field(default=None, max_length=120)
    acq_run_id: str | None = Field(default=None, max_length=120)
    parse_run_id: str | None = Field(default=None, max_length=120)
    value_preview: str | None = Field(default=None, max_length=256)
    timestamp_ms: int | None = Field(default=None, ge=0)


class HMIEventsIngestRequest(BaseModel):
    events: list[HMIEventIn] = Field(min_length=1, max_length=100)


class HMIEventsIngestResponse(BaseModel):
    accepted: int
