from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class DiscoveryProviderLimits(BaseModel):
    openalex: int | None = Field(default=None, ge=1, le=200)
    semantic_scholar: int | None = Field(default=None, ge=1, le=100)
    brave: int | None = Field(default=None, ge=1, le=20)


class RunCreateRequest(BaseModel):
    seed_queries: list[str] = Field(min_length=1)
    selected_queries: list[str] | None = None
    session_id: str = Field(min_length=1, max_length=120)
    session_context: str = Field(min_length=1, max_length=4096)
    max_iterations: int = Field(default=6, ge=1, le=6)
    ai_filter_enabled: bool | None = None
    provider_limits: DiscoveryProviderLimits | None = None


class CitationIterationRequest(BaseModel):
    selected_queries: list[str] | None = None
    ai_filter_enabled: bool | None = None
    provider_limits: DiscoveryProviderLimits | None = None


class QuerySuggestionsRequest(BaseModel):
    session_context: str = Field(min_length=1, max_length=4096)
    existing_queries: list[str] = Field(default_factory=list)
    max_suggestions: int = Field(default=8, ge=1, le=12)


class QuerySuggestionsResponse(BaseModel):
    suggestions: list[str]
    source: str
    warning: str | None = None


class RunCreateResponse(BaseModel):
    run_id: str
    status: str


class RunStatusResponse(BaseModel):
    run_id: str
    session_id: str | None = None
    status: str
    seed_queries: list[str]
    current_iteration: int
    accepted_total: int
    citation_unexpanded_parent_count: int = 0
    citation_expansion_available: bool = False
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
    run_id: str | None = None
    run_number: int | None = None
    query_step_number: int | None = None
    query_lineage_number: str | None = None
    query: str
    position: int
    status: str
    discovered_count: int
    openalex_count: int = 0
    brave_count: int = 0
    semantic_scholar_count: int = 0
    openalex_status: str = "pending"
    semantic_scholar_status: str = "pending"
    brave_status: str = "pending"
    openalex_error_message: str | None = None
    semantic_scholar_error_message: str | None = None
    brave_error_message: str | None = None
    accepted_count: int = 0
    rejected_count: int = 0
    pending_count: int = 0
    processing_count: int = 0
    scope_total_parents: int = 0
    scope_processed_parents: int = 0
    checkpoint_state: str = "none"
    has_session_context: bool = False
    session_context_preview: str | None = None
    error_message: str | None


class DiscoveryRunQueriesResponse(BaseModel):
    run_id: str
    queries: list[DiscoveryRunQueryOut]


class SessionDiscoveryQueriesResponse(BaseModel):
    session_id: str
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
    run_id: str | None = None
    run_number: int | None = None
    run_source_number: int | None = None
    query_step_number: int | None = None
    query_source_number: int | None = None
    lineage_number: str | None = None
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
    previewable_pdf_artifact_id: str | None = None
    artifact_kind: str | None = None
    artifact_quality_status: str | None = None
    parse_scope_status: str | None = None


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
    acq_run_id: str | None = None
    artifact_id: str | None = None
    artifact_kind: str | None = None
    artifact_mime_type: str | None = None
    artifact_quality_status: str | None = None
    artifact_quality_reason: str | None = None
    status: str
    attempt_count: int
    selected_url: str | None
    last_error: str | None
    artifact_source_session_id: str | None = None
    parse_scope_status: str | None = None
    parse_status_detail: str | None = None
    parse_run_id: str | None = None


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


class ParseAllDownloadedRequest(BaseModel):
    session_id: str = Field(min_length=1)


class ParseAllDownloadedResponse(BaseModel):
    queued_runs: int
    parse_run_ids: list[str]
    acquisition_run_ids: list[str]
    queued_summary: list[dict] = Field(default_factory=list)


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
    query_suggestions_available: bool
    query_suggestions_reason: str | None
    provider_readiness: dict
    db_ready: bool
    db_missing_tables: list[str]
    db_error: str | None
    database_target: str
    db_target_url: str
    db_target_resolved_path: str | None
    db_target_kind: str
    db_target_warning: str | None
    db_target_expected_for_role: str
    db_target_matches_server_default: bool
    db_schema_ready: bool
    db_run_count: int | None
    process_pid: int
    hot_read_metrics: dict


class DatabaseBackupCandidateOut(BaseModel):
    name: str
    path: str
    size_bytes: int
    mtime: int
    kind: str
    managed: bool


class DatabaseBackupListResponse(BaseModel):
    items: list[DatabaseBackupCandidateOut]
    total: int
    database_target: str
    backup_dir: str | None = None
    retention_count: int


class DatabaseRestoreRequest(BaseModel):
    backup_name: str
    confirm_backup_name: str


class DatabaseBackupCreateResponse(BaseModel):
    ok: bool
    backup: DatabaseBackupCandidateOut
    database_target: str
    backup_dir: str | None = None
    pruned_auto_backups: int = 0


class DatabaseRestoreResponse(BaseModel):
    ok: bool
    restored_backup_name: str
    restored_from: str
    snapshot_path: str
    database_target: str
    database_inode: int | None = None
    database_mtime: int | None = None
    snapshot_kind: str | None = None
    repaired_query_rows: int
    superseded_runs: int
    superseded_query_rows: int


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


class SessionProfileUpsertRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    session_context: str | None = Field(default=None, max_length=4096)


class SessionProfileResponse(BaseModel):
    session_id: str
    name: str | None
    session_context: str | None
    updated_at: str | None


class SessionProfilesListResponse(BaseModel):
    items: list[SessionProfileResponse]
    total: int


class BookmarkCreateRequest(BaseModel):
    source_id: str = Field(min_length=1)


class BookmarkRead(BaseModel):
    id: str
    source_id: str
    title: str
    abstract: str | None
    year: int | None
    doi: str | None
    doi_url: str | None
    source_url: str | None
    source_session_id: str | None
    source_session_name: str | None
    source_run_id: str | None
    created_at: str | None


class BookmarksListResponse(BaseModel):
    items: list[BookmarkRead]
    total: int
    limit: int
    offset: int


class BookmarkCreateSessionResponse(BaseModel):
    session_id: str
    session_name: str
    discovery_run_id: str
    status: str
    bookmarked_parent_count: int


class PaperAnnotationOut(BaseModel):
    session_id: str
    source_id: str
    freeform_tags: list[str] = Field(default_factory=list)
    approved_tags: list[str] = Field(default_factory=list)
    freeform_tags_by_category: dict[str, list[str]] = Field(default_factory=dict)
    approved_tags_by_category: dict[str, list[str]] = Field(default_factory=dict)
    ai_suggested_tags: list[str] = Field(default_factory=list)
    ai_summary: str | None = None
    ai_summary_json: dict | None = None
    summary_prompt_snapshot: str | None = None
    summary_model: str | None = None
    summary_status: str = "none"
    tag_suggestion_status: str = "none"
    summary_generated_at: str | None = None
    tag_suggestion_generated_at: str | None = None
    summary_error: str | None = None
    tag_suggestion_error: str | None = None
    can_generate_summary: bool = False
    summary_block_reason: str | None = None
    can_generate_tags: bool = False
    tag_suggestion_block_reason: str | None = None


class PaperAnnotationsListResponse(BaseModel):
    items: list[PaperAnnotationOut]
    total: int
    limit: int
    offset: int


class PaperAnnotationUpdateRequest(BaseModel):
    freeform_tags: list[str] | None = None
    approved_tags: list[str] | None = None
    freeform_tags_by_category: dict[str, list[str]] | None = None
    approved_tags_by_category: dict[str, list[str]] | None = None


class SessionTagCatalogOut(BaseModel):
    session_id: str
    tags: list[str] = Field(default_factory=list)


class SessionTagCatalogUpdateRequest(BaseModel):
    tags: list[str] = Field(default_factory=list)


class TagSpecCategoryConfig(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=160)
    guidance: str = ""
    allowed_tags: list[str] = Field(default_factory=list)
    allow_free_text: bool = False


class SessionTagSpecConfig(BaseModel):
    categories: list[TagSpecCategoryConfig] = Field(default_factory=list)


class SessionTagSpecOut(BaseModel):
    session_id: str
    category_config: SessionTagSpecConfig
    prompt_template: str


class SessionTagSpecUpdateRequest(BaseModel):
    category_config: SessionTagSpecConfig


class SummaryFieldConfig(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=240)
    label: str = Field(min_length=1, max_length=160)
    description: str = ""
    field_type: Literal["string", "boolean", "string_list", "object_list"]
    enabled: bool = True
    object_item_fields: list[str] = Field(default_factory=list)


class SummaryControlledValueConfig(BaseModel):
    field_path: str = Field(min_length=1, max_length=240)
    allowed_values: list[str] = Field(default_factory=list)
    fallback_policy: Literal["allow_free_text", "prefer_enum_only"] = "allow_free_text"


class SessionSummaryEditorConfig(BaseModel):
    summary_focus: str = ""
    schema_fields: list[SummaryFieldConfig] = Field(default_factory=list)
    controlled_values: list[SummaryControlledValueConfig] = Field(default_factory=list)


class SessionSummarySettingsOut(BaseModel):
    session_id: str
    prompt_template: str
    editor_config: SessionSummaryEditorConfig
    current_global_summary_model: str


class SessionSummarySettingsUpdateRequest(BaseModel):
    prompt_template: str | None = Field(default=None, min_length=1, max_length=12000)
    editor_config: SessionSummaryEditorConfig | None = None


class SummaryGenerationRequest(BaseModel):
    source_ids: list[str] = Field(min_length=1)
    force_regenerate: bool = False


class SummaryGenerationBlockedOut(BaseModel):
    source_id: str
    reason: str


class SummaryGenerationResponse(BaseModel):
    session_id: str
    queued_count: int
    blocked_count: int
    blocked: list[SummaryGenerationBlockedOut] = Field(default_factory=list)


class TagGenerationRequest(BaseModel):
    source_ids: list[str] = Field(min_length=1)
    force_regenerate: bool = False


class TagGenerationBlockedOut(BaseModel):
    source_id: str
    reason: str


class TagGenerationResponse(BaseModel):
    session_id: str
    queued_count: int
    blocked_count: int
    blocked: list[TagGenerationBlockedOut] = Field(default_factory=list)


class SuggestedTagPromoteRequest(BaseModel):
    tag: str = Field(min_length=1, max_length=64)
    target: str = Field(pattern="^(freeform|approved)$")


class SuggestedTagDismissRequest(BaseModel):
    tag: str = Field(min_length=1, max_length=64)


class SessionTagCandidateOut(BaseModel):
    id: str
    session_id: str
    category_key: str
    category_label: str | None = None
    tag: str
    status: str
    source_count: int = 0
    updated_at: str | None = None


class SessionTagCandidateGroupOut(BaseModel):
    category_key: str
    category_label: str
    pending_count: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    candidates: list[SessionTagCandidateOut] = Field(default_factory=list)


class SessionTagReviewOut(BaseModel):
    session_id: str
    candidate_generation_status: str = "none"
    candidate_generation_generated_at: str | None = None
    candidate_generation_error: str | None = None
    tag_assignment_status: str = "none"
    tag_assignment_generated_at: str | None = None
    tag_assignment_error: str | None = None
    pending_count: int = 0
    approved_count: int = 0
    rejected_count: int = 0
    candidates: list[SessionTagCandidateOut] = Field(default_factory=list)
    groups: list[SessionTagCandidateGroupOut] = Field(default_factory=list)


class SessionTagWorkflowRequest(BaseModel):
    force_regenerate: bool = False


class SessionTagWorkflowResponse(BaseModel):
    session_id: str
    status: str
