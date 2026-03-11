from __future__ import annotations

from pydantic import BaseModel, Field


class RunCreateRequest(BaseModel):
    seed_queries: list[str] = Field(min_length=1)
    max_iterations: int = Field(default=6, ge=1, le=6)
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
