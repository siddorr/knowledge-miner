const AUTH_STORAGE_KEY = "km_hmi2_api_key";
const SESSION_STORAGE_KEY = "km_hmi2_sessions";
const ACTIVE_SESSION_KEY = "km_hmi2_active_session";
const INTERNAL_REPO_URL_KEY = "km_hmi2_internal_repo_url";
const SESSION_CONTEXT_MAX = 4096;
const DEFAULT_PROVIDER_LIMITS = Object.freeze({ openalex: 25, semantic_scholar: 25, brave: 20 });
const AI_MODEL_PRESETS = Object.freeze(["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"]);
const OFFLINE_HEALTH_POLL_MS = 4000;
const OFFLINE_FAILURE_THRESHOLD = 2;
const API_FETCH_PAGE_SIZE = 100;
const REVIEW_FETCH_LIMIT = 100;
const LIBRARY_ANNOTATION_BATCH_SIZE = 25;
const ACQUISITION_FETCH_PAGE_SIZE = 100;
const DOCUMENTS_PAGE_SIZE = 50;
const DEFAULT_SUMMARY_PROMPT = `You are a strict JSON extraction engine.

Extract data from the provided full scientific paper text about wastewater treatment in semiconductor fabrication facilities.

Return exactly one valid JSON object and nothing else.

Use exactly this schema and no other keys:
{"summary":"string","wastewater_source":{"fab_area":null,"process_step":null,"tool_or_equipment":null,"waste_stream_name":null,"real_or_synthetic_water":null,"water_source_details":null},"water_composition":{"components":[],"water_quality_parameters":[]},"treatment_target":{"target_contaminants_or_parameters":[]},"treatment_technology":{"technology_name":null,"technology_category":null},"experiments":{"used_real_wastewater":null,"used_synthetic_wastewater":null,"experimental_scale":null},"performance":{"removal_results":[],"key_findings":[],"limitations":[]}}

Rules:
- Extract only facts explicitly stated in the text.
- Do not use outside knowledge.
- Keep values short and factual.
- If a field is missing, use null or [].
- "summary" must be a concise human-readable summary of the paper for this research session.
- Return JSON only.`;
const REVIEW_STATUS_TO_API = {
  pending: "needs_review",
  accepted: "accepted",
  rejected: "rejected",
  later: "later",
  all: "all",
  latest_auto_approved: "latest_auto_approved",
  latest_auto_rejected: "latest_auto_rejected",
};

const state = {
  authEnabled: Boolean(window.__KM_HMI2_AUTH_ENABLED__),
  token: window.__KM_HMI2_DEFAULT_TOKEN__ || localStorage.getItem(AUTH_STORAGE_KEY) || "",
  sessions: [],
  activeSessionId: localStorage.getItem(ACTIVE_SESSION_KEY) || "",
  pendingSessionId: "",
  activePage: window.__KM_HMI2_LAUNCH_SECTION__ || "discover",
  fileMenuOpen: false,
  showNewSessionForm: false,
  reviewQueue: "pending",
  reviewSort: { key: "lineage", dir: "desc" },
  documentsSort: { key: "lineage", dir: "asc" },
  documentsPage: 0,
  librarySort: { key: "lineage", dir: "asc" },
  libraryDetailTab: "details",
  latest: { discovery: "", discoverySession: "", acquisition: "", parse: "" },
  reviewItems: [],
  reviewIndex: -1,
  documentRows: [],
  libraryRows: [],
  libraryFilteredRows: [],
  selectedLibrarySourceId: "",
  selectedReviewSourceId: "",
  selectedDocumentSourceId: "",
  discoverRunQueries: [],
  suggestedQueries: [],
  eventSource: null,
  inFlight: 0,
  busyLabel: "",
  currentDiscoveryStatus: null,
  currentAcquisitionStatus: null,
  currentParseStatus: null,
  liveRefreshTimer: null,
  discoverPollTimer: null,
  internalRepositoryBaseUrl: localStorage.getItem(INTERNAL_REPO_URL_KEY) || "",
  aiSettings: null,
  advancedEventsPaused: false,
  advancedEventsAutoscroll: true,
  advancedEventRows: [],
  advancedEventGroupedCounts: [],
  advancedEventPollTimer: null,
  advancedDatabaseBackups: [],
  advancedDatabaseTarget: "",
  advancedDatabaseBackupDir: "",
  advancedDatabaseRetentionCount: 0,
  databaseTargetWarning: "",
  healthPollTimer: null,
  healthFailureCount: 0,
  serverOffline: false,
  offlineMessage: "",
  systemStatus: null,
  bookmarks: [],
  selectedBookmarkId: "",
  paperAnnotations: {},
  sessionApprovedTags: [],
  sessionTagSpec: null,
  sessionSummaryPrompt: "",
  sessionSummaryEditorConfig: null,
  currentGlobalSummaryModel: "",
  sessionTagReview: null,
  summaryPromptRawMode: false,
  tagPromptRawMode: false,
  summaryPollTimer: null,
  reviewLoadInFlight: false,
  reviewReloadQueued: false,
  libraryStatusStickyUntil: 0,
  advancedParseStatusStickyUntil: 0,
  newSessionDraftName: "",
  newSessionDraftContext: "",
  suggestionStateSticky: false,
};

const els = {};

function $(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function lineageKeyParts(item) {
  return [
    Number(item?.run_number ?? 0),
    Number(item?.query_step_number ?? 0),
    Number(item?.query_source_number ?? 0),
  ];
}

function compareLineageValues(left, right, dir = "asc") {
  const factor = dir === "asc" ? 1 : -1;
  const leftParts = lineageKeyParts(left);
  const rightParts = lineageKeyParts(right);
  for (let index = 0; index < leftParts.length; index += 1) {
    if (leftParts[index] !== rightParts[index]) {
      return (leftParts[index] - rightParts[index]) * factor;
    }
  }
  return 0;
}

function formatLineageNumber(item) {
  return item?.lineage_number || "-";
}

async function copyTextToClipboard(text) {
  if (!text) {
    return false;
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to legacy copy.
  }
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "absolute";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(textarea);
    return ok;
  } catch {
    return false;
  }
}

function readDom() {
  const ids = [
    "activityLine", "activityIndicator", "authStatus", "aiStatus", "dbStatus", "headerProgress", "headerProgressLabel",
    "fileMenuBtn", "fileMenuPanel", "newSessionBtn", "deleteSessionBtn", "stopRunningBtn", "sessionSelect", "sessionState", "activeSessionLabel",
    "newSessionForm", "newSessionNameInput", "newSessionContextInput", "createSessionConfirmBtn", "cancelNewSessionBtn", "newSessionFormState",
    "discoverQueryInput", "addQueryBtn", "generateQuerySuggestionsBtn", "runDiscoveryBtn", "runNextCitationBtn", "resumeCitationBtn", "discoverIterationLine",
    "discoverQueryList", "discoverSuggestedQueryList", "discoverSuggestionsState", "discoverSelectedCount", "discoverRunQueries", "discoverRunQueriesState", "discoverCitationHint",
    "discoverOpenalexLimitInput", "discoverSemanticScholarLimitInput", "discoverBraveLimitInput", "discoverProviderLimitsState",
    "discoverSummaryDiscovered", "discoverSummaryApproved", "discoverSummaryRejected", "discoverSummaryReviewed", "discoverSummaryPending", "discoverState",
    "sessionContextInput", "saveSessionContextBtn", "sessionContextCounter", "sessionContextState", "sessionContextUpdated",
    "reviewHeading", "reviewRows", "reviewTitle", "reviewAbstract", "reviewCopyAbstractBtn", "reviewMetadata", "reviewSignals",
    "reviewAcceptBtn", "reviewRejectBtn", "reviewLaterBtn", "reviewBookmarkBtn", "reviewState", "reviewBadge", "reviewQueueHelp", "reviewFilterChips",
    "documentsDownloaded", "documentsFailed", "documentsManual", "documentsPending", "documentsRows", "documentsPrevBtn", "documentsNextBtn", "documentsPageState",
    "downloadMissingBtn", "retryFailedBtn", "documentsExportCsvBtn", "batchUploadForm", "batchUploadFiles",
    "batchUploadResults", "documentsState", "documentsBadge", "documentsDetailTitle", "documentsDetailSummary",
    "documentsDetailMetadata", "documentsRowActionBtn", "documentsOpenPdfBtn", "documentsBookmarkBtn", "internalRepoUrlInput", "saveInternalRepoUrlBtn", "internalRepoUrlState",
    "libraryMatches", "libraryHighest", "libraryLowest", "libraryQuery", "libraryParsedOnlyCheckbox", "libraryPdfOnlyCheckbox", "librarySummaryCurrentOnlyCheckbox", "libraryExportSize", "libraryRows",
    "libraryTitle", "libraryAbstract", "libraryMetadata", "libraryAddBtn", "libraryRemoveBtn", "libraryBookmarkBtn", "libraryZipBtn",
    "libraryMetadataBtn", "libraryState", "libraryGenerateVisibleSummariesBtn", "libraryGenerateVisibleTagsBtn", "libraryPromptToggleBtn", "libraryApprovedTagsToggleBtn",
    "librarySummaryPromptPanel", "librarySummaryFocusInput", "librarySummaryFieldsList", "libraryAddSummaryFieldBtn",
    "librarySummaryControlledValuesList", "libraryAddSummaryControlledValueBtn", "librarySummaryLockedRulesText", "librarySummaryLockedConstraintsText",
    "libraryCurrentSummaryModel", "libraryToggleRawSummaryPromptBtn",
    "libraryRawSummaryPromptWrap", "librarySummaryPromptInput", "librarySaveSummaryPromptBtn", "libraryResetSummaryPromptBtn", "librarySummaryPromptState",
    "libraryApprovedTagsPanel", "libraryApprovedTagsInput", "librarySaveApprovedTagsBtn", "libraryApprovedTagsState",
    "libraryFilterHelp", "libraryFreeformTags", "libraryFreeformTagInput", "libraryAddFreeformTagBtn",
    "libraryApprovedTags", "libraryApprovedTagSelect", "libraryAddApprovedTagBtn",
    "libraryApplyApprovedTagsOneBtn", "libraryReapplyApprovedTagsOneBtn",
    "libraryDetailsTabBtn", "librarySummaryPreviewTabBtn", "libraryTagSpecTabBtn", "libraryTagReviewTabBtn", "libraryDetailsPanel", "librarySummaryPreviewPanel", "libraryTagSpecPanel", "libraryTagReviewPanel",
    "librarySummaryStatus", "librarySummaryText", "libraryGenerateSummaryBtn", "libraryRegenerateSummaryBtn",
    "librarySummaryPreviewStatus", "librarySummaryCurrentModelValue", "librarySummaryLastModelValue", "librarySummaryPreviewText",
    "librarySummaryStructuredText", "librarySummaryPreviewOpenPdfBtn", "libraryCopySummaryBtn", "librarySummaryPreviewGenerateBtn", "librarySummaryPreviewRegenerateBtn",
    "libraryTagSpecCategories", "libraryAddTagSpecCategoryBtn", "librarySaveTagSpecBtn", "libraryResetTagSpecBtn", "libraryToggleRawTagPromptBtn", "libraryRawTagPromptWrap", "libraryTagPromptInput", "libraryTagSpecState",
    "libraryTagCandidateStatus", "libraryTagAssignmentStatus", "libraryTagPendingCount", "libraryTagApprovedCount", "libraryTagRejectedCount",
    "libraryGenerateCandidateTagsBtn", "libraryRegenerateCandidateTagsBtn", "libraryResetRejectedTagsBtn", "libraryTagCandidatesList",
    "libraryApplyApprovedTagsBtn", "libraryReapplyApprovedTagsBtn",
    "apiKeyInput", "saveApiKeyBtn", "apiKeyState", "aiModelSelect", "saveAiSettingsBtn", "aiSettingsState", "latestDiscoveryId", "latestAcquisitionId", "latestParseId", "startParseBtn", "advancedParseState",
    "openalexLimitInput", "braveCountInput", "braveAllowlistCheckbox", "saveProviderSettingsBtn", "providerSettingsState",
    "globalSearchInput", "globalSearchBtn", "globalSearchResults", "runLookupInput", "runLookupBtn", "runLookupResult",
    "createDatabaseBackupBtn", "refreshDatabaseBackupsBtn", "databaseRestoreState", "databaseTargetWarning", "databaseRestoreTarget", "databaseBackupDir", "databaseBackupPolicy", "databaseBackupSelect", "databaseRestoreConfirmInput", "restoreDatabaseBtn", "databaseBackupList",
    "advancedEventsPauseBtn", "advancedEventsAutoscrollBtn", "advancedEventsState", "advancedEventCounters", "advancedEventsLog",
    "footerSystem", "footerAi", "footerDb", "footerUpdated",
    "bookmarksRows", "bookmarksTitle", "bookmarksAbstract", "bookmarksMetadata", "bookmarksCreateSessionBtn", "bookmarksRemoveBtn", "bookmarksState",
  ];
  for (const id of ids) {
    els[id] = $(id);
  }
  els.pages = {
    discover: $("page-discover"),
    review: $("page-review"),
    documents: $("page-documents"),
    library: $("page-library"),
    bookmarks: $("page-bookmarks"),
    advanced: $("page-advanced"),
  };
  els.navButtons = Array.from(document.querySelectorAll(".nav-btn"));
  els.reviewFilterButtons = Array.from(document.querySelectorAll("[data-review-filter]"));
  els.reviewSortButtons = Array.from(document.querySelectorAll("[data-review-sort]"));
  els.documentsSortButtons = Array.from(document.querySelectorAll("[data-documents-sort]"));
  els.librarySortButtons = Array.from(document.querySelectorAll("[data-library-sort]"));
}

function normalizeSession(raw) {
  const queries = Array.isArray(raw?.queries) ? raw.queries : [];
  const normalizedQueries = queries
    .map((entry) => {
      if (typeof entry === "string") {
        return { id: `query_${Math.random().toString(36).slice(2, 10)}`, text: entry, selected: true };
      }
      const text = String(entry?.text || "").trim();
      if (!text) {
        return null;
      }
      return {
        id: entry.id || `query_${Math.random().toString(36).slice(2, 10)}`,
        text,
        selected: entry.selected !== false,
      };
    })
    .filter(Boolean);
  const sessionContext = typeof raw?.sessionContext === "string" ? raw.sessionContext : "";
  const sessionContextUpdatedAt = typeof raw?.sessionContextUpdatedAt === "string" ? raw.sessionContextUpdatedAt : "";
  return {
    id: raw?.id || `session_${Math.random().toString(36).slice(2, 10)}`,
    name: raw?.name || "New Session",
    queries: normalizedQueries,
    discoveryRunId: raw?.discoveryRunId || "",
    resultsRunId: raw?.resultsRunId || raw?.discoveryRunId || "",
    acquisitionRunId: raw?.acquisitionRunId || "",
    exportSourceIds: Array.isArray(raw?.exportSourceIds) ? raw.exportSourceIds : [],
    sessionContext,
    sessionContextUpdatedAt,
    savedSessionContext: typeof raw?.savedSessionContext === "string" ? raw.savedSessionContext : sessionContext,
    savedSessionContextUpdatedAt: typeof raw?.savedSessionContextUpdatedAt === "string"
      ? raw.savedSessionContextUpdatedAt
      : sessionContextUpdatedAt,
    providerLimits: normalizeProviderLimits(raw?.providerLimits),
  };
}

function createBlankSession() {
  return normalizeSession({});
}

function isLocalDraftSession(session) {
  if (!session) {
    return false;
  }
  const hasBoundRun = Boolean(discoverRunId(session) || resultsRunId(session) || (session.acquisitionRunId || "").trim());
  const hasSavedContext = Boolean(
    normalizeSessionContext(session.savedSessionContext || "") || String(session.savedSessionContextUpdatedAt || "").trim(),
  );
  return !hasBoundRun && !hasSavedContext;
}

function loadSessions() {
  try {
    state.sessions = JSON.parse(localStorage.getItem(SESSION_STORAGE_KEY) || "[]").map(normalizeSession);
  } catch {
    state.sessions = [];
  }
  if (!state.sessions.length) {
    const session = createBlankSession();
    state.sessions = [session];
    state.activeSessionId = session.id;
  }
  if (!state.sessions.some((session) => session.id === state.activeSessionId)) {
    state.activeSessionId = state.sessions[0].id;
  }
  state.pendingSessionId = state.activeSessionId;
  persistSessions();
}

async function syncSessionsFromServer() {
  try {
    const result = await api("/v1/sessions");
    const items = Array.isArray(result.data?.items) ? result.data.items : [];
    const serverSessionIds = new Set(items.map((item) => item.session_id));
    const merged = new Map(
      state.sessions
        .map((session) => normalizeSession(session))
        .filter((session) => serverSessionIds.has(session.id) || isLocalDraftSession(session))
        .map((session) => [session.id, session]),
    );
    items.forEach((item) => {
      const existing = merged.get(item.session_id);
      const normalized = normalizeSession({
        ...(existing || {}),
        id: item.session_id,
        name: item.name || existing?.name || "New Session",
        sessionContext: item.session_context || existing?.sessionContext || "",
        savedSessionContext: item.session_context || existing?.savedSessionContext || existing?.sessionContext || "",
      });
      merged.set(item.session_id, normalized);
    });
    state.sessions = Array.from(merged.values());
    if (!state.sessions.length) {
      const session = createBlankSession();
      state.sessions = [session];
      state.activeSessionId = session.id;
    } else if (!state.sessions.some((session) => session.id === state.activeSessionId)) {
      state.activeSessionId = state.sessions[0].id;
    }
    state.pendingSessionId = state.activeSessionId;
    persistSessions();
    renderSessions();
  } catch (error) {
    if (els.sessionState) {
      els.sessionState.textContent = `Unable to sync sessions from server: ${errorDetail(error)}`;
    }
  }
}

function persistSessions() {
  localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(state.sessions));
  localStorage.setItem(ACTIVE_SESSION_KEY, state.activeSessionId);
}

function resetNewSessionDraft() {
  state.newSessionDraftName = "";
  state.newSessionDraftContext = "";
}

function hasUnsavedSessionContext(session = activeSession()) {
  return normalizeSessionContext(session?.sessionContext || "") !== normalizeSessionContext(session?.savedSessionContext || "");
}

function defaultSessionContextState(session = activeSession()) {
  const context = normalizeSessionContext(session?.sessionContext || "");
  if (!context) {
    return "Session context is required before running discovery.";
  }
  if (hasUnsavedSessionContext(session)) {
    return "Context has unsaved local changes. Press Save Context to persist it.";
  }
  return "Session context saved.";
}

function updateSessionContextControls(session = activeSession()) {
  if (!els.saveSessionContextBtn || !els.sessionContextInput) {
    return;
  }
  const context = normalizeSessionContext(els.sessionContextInput.value);
  if (state.serverOffline) {
    els.saveSessionContextBtn.disabled = true;
    els.saveSessionContextBtn.textContent = "Offline";
    return;
  }
  if (!context) {
    els.saveSessionContextBtn.disabled = true;
    els.saveSessionContextBtn.textContent = "Save Context";
    return;
  }
  if (hasUnsavedSessionContext(session)) {
    els.saveSessionContextBtn.disabled = false;
    els.saveSessionContextBtn.textContent = "Save Context";
    return;
  }
  els.saveSessionContextBtn.disabled = true;
  els.saveSessionContextBtn.textContent = "Saved";
}

function querySuggestionsUnavailableText(reason) {
  if (reason === "ai_api_key_missing") {
    return "AI query suggestions are unavailable: AI API key is not configured.";
  }
  if (reason === "ai_base_url_missing") {
    return "AI query suggestions are unavailable: AI base URL is not configured.";
  }
  return "AI query suggestions are unavailable in current settings.";
}

function querySuggestionErrorText(error) {
  const detail = errorDetail(error);
  if (detail.includes("ai_query_suggestions_missing_api_key")) {
    return "AI query suggestions are unavailable: AI API key is not configured.";
  }
  if (detail.includes("ai_query_suggestions_http_429")) {
    return "AI query suggestions are temporarily rate limited. Retry shortly.";
  }
  if (detail.includes("ai_query_suggestions_timeout")) {
    return "AI query suggestion request timed out. Retry.";
  }
  if (detail.includes("invalid_query_suggestions_payload")) {
    return "AI query suggestion response was invalid. Retry.";
  }
  if (detail.includes("ai_query_suggestions_http_") || detail.includes("ai_query_suggestions_base_url_missing")) {
    return "AI query suggestion provider returned an error.";
  }
  return `Unable to generate suggestions: ${detail}`;
}

function updateSuggestionAvailability() {
  if (!els.generateQuerySuggestionsBtn || !els.discoverSuggestionsState) {
    return;
  }
  const context = normalizeSessionContext(activeSession()?.sessionContext || els.sessionContextInput?.value || "");
  const systemStatus = state.systemStatus || {};
  const hasExplicitSuggestionAvailability = typeof systemStatus.query_suggestions_available === "boolean";
  const available = hasExplicitSuggestionAvailability
    ? systemStatus.query_suggestions_available
    : Boolean(systemStatus.ai_filter_active);
  let disabled = false;
  let message = "";
  if (!context) {
    disabled = true;
    message = "Session context is required before generating suggestions.";
  } else if (!available) {
    disabled = true;
    message = hasExplicitSuggestionAvailability
      ? querySuggestionsUnavailableText(systemStatus.query_suggestions_reason)
      : "AI query suggestions are unavailable in current settings.";
  }
  if (state.serverOffline) {
    disabled = true;
  }
  els.generateQuerySuggestionsBtn.disabled = disabled;
  if (!state.suggestionStateSticky) {
    els.discoverSuggestionsState.textContent = message || (state.suggestedQueries.length ? "" : "No suggestions generated yet.");
  }
}

function activeSession() {
  return state.sessions.find((session) => session.id === state.activeSessionId) || state.sessions[0];
}

function activeQueries(session = activeSession()) {
  return session.queries.filter((query) => query.selected).map((query) => query.text.trim()).filter(Boolean);
}

function bookmarkForSource(sourceId) {
  return state.bookmarks.find((item) => item.source_id === sourceId) || null;
}

function isBookmarked(sourceId) {
  return Boolean(bookmarkForSource(sourceId));
}

function annotationForSource(sourceId) {
  return state.paperAnnotations[sourceId] || {
    session_id: activeSession().id,
    source_id: sourceId,
    freeform_tags: [],
    approved_tags: [],
    freeform_tags_by_category: {},
    approved_tags_by_category: {},
    ai_suggested_tags: [],
    ai_summary: null,
    ai_summary_json: null,
    summary_prompt_snapshot: null,
    summary_model: null,
    summary_status: "none",
    tag_suggestion_status: "none",
    summary_generated_at: null,
    tag_suggestion_generated_at: null,
    summary_error: null,
    tag_suggestion_error: null,
    can_generate_summary: false,
    summary_block_reason: "parsed_text_required",
    can_generate_tags: false,
    tag_suggestion_block_reason: "parsed_text_required",
  };
}

function defaultSummaryEditorConfig() {
  return {
    summary_focus: "Write a concise 1-3 sentence factual summary of the wastewater treatment topic, studied process, and explicit result using only paper-supported facts.",
    schema_fields: [
      { id: "summary", path: "summary", label: "Summary", description: "Concise human-readable summary.", field_type: "string", enabled: true, object_item_fields: [] },
      { id: "fab_area", path: "wastewater_source.fab_area", label: "Fab Area", description: "Fab section or wastewater origin area.", field_type: "string", enabled: true, object_item_fields: [] },
      { id: "process_step", path: "wastewater_source.process_step", label: "Process Step", description: "Process generating the wastewater.", field_type: "string", enabled: true, object_item_fields: [] },
      { id: "tool_or_equipment", path: "wastewater_source.tool_or_equipment", label: "Tool or Equipment", description: "Specific fab tool or equipment.", field_type: "string", enabled: true, object_item_fields: [] },
      { id: "waste_stream_name", path: "wastewater_source.waste_stream_name", label: "Waste Stream Name", description: "Named waste stream if given.", field_type: "string", enabled: true, object_item_fields: [] },
      { id: "real_or_synthetic_water", path: "wastewater_source.real_or_synthetic_water", label: "Real or Synthetic Water", description: "Water source type.", field_type: "string", enabled: true, object_item_fields: [] },
      { id: "water_source_details", path: "wastewater_source.water_source_details", label: "Water Source Details", description: "Short factual origin description.", field_type: "string", enabled: true, object_item_fields: [] },
      { id: "components", path: "water_composition.components", label: "Components", description: "Reported composition components.", field_type: "object_list", enabled: true, object_item_fields: ["component", "value", "unit", "context"] },
      { id: "water_quality_parameters", path: "water_composition.water_quality_parameters", label: "Water Quality Parameters", description: "Reported water-quality parameters.", field_type: "object_list", enabled: true, object_item_fields: ["parameter", "value", "unit", "context"] },
      { id: "target_contaminants", path: "treatment_target.target_contaminants_or_parameters", label: "Target Contaminants or Parameters", description: "Treatment targets.", field_type: "string_list", enabled: true, object_item_fields: [] },
      { id: "technology_name", path: "treatment_technology.technology_name", label: "Technology Name", description: "Named treatment process.", field_type: "string", enabled: true, object_item_fields: [] },
      { id: "technology_category", path: "treatment_technology.technology_category", label: "Technology Category", description: "Technology category.", field_type: "string", enabled: true, object_item_fields: [] },
      { id: "used_real_wastewater", path: "experiments.used_real_wastewater", label: "Used Real Wastewater", description: "Whether real wastewater was used.", field_type: "boolean", enabled: true, object_item_fields: [] },
      { id: "used_synthetic_wastewater", path: "experiments.used_synthetic_wastewater", label: "Used Synthetic Wastewater", description: "Whether synthetic wastewater was used.", field_type: "boolean", enabled: true, object_item_fields: [] },
      { id: "experimental_scale", path: "experiments.experimental_scale", label: "Experimental Scale", description: "Experimental scale.", field_type: "string", enabled: true, object_item_fields: [] },
      { id: "removal_results", path: "performance.removal_results", label: "Removal Results", description: "Numeric removal results.", field_type: "object_list", enabled: true, object_item_fields: ["target", "metric", "value", "unit", "conditions"] },
      { id: "key_findings", path: "performance.key_findings", label: "Key Findings", description: "Short factual findings.", field_type: "string_list", enabled: true, object_item_fields: [] },
      { id: "limitations", path: "performance.limitations", label: "Limitations", description: "Explicit author-stated limitations.", field_type: "string_list", enabled: true, object_item_fields: [] },
    ],
    controlled_values: [
      { field_path: "wastewater_source.real_or_synthetic_water", allowed_values: ["real", "synthetic", "both", "unclear"], fallback_policy: "allow_free_text" },
      { field_path: "treatment_technology.technology_category", allowed_values: ["physical", "chemical", "physicochemical", "biological", "membrane", "electrochemical", "adsorption", "hybrid", "other", "unclear"], fallback_policy: "allow_free_text" },
      { field_path: "experiments.experimental_scale", allowed_values: ["lab", "pilot", "full-scale", "unclear"], fallback_policy: "allow_free_text" },
    ],
  };
}

function cloneSummaryEditorConfig(config) {
  return JSON.parse(JSON.stringify(config || defaultSummaryEditorConfig()));
}

function defaultTagSpecConfig() {
  return {
    categories: [
      { key: "material_or_product_tags", label: "Material or Product Tags", guidance: "Use for precipitates, products, or key media.", allowed_tags: ["CaF2 precipitation", "calcium fluoride", "ion exchange resin", "struvite", "sulfuric acid concentration"], allow_free_text: true },
      { key: "recovery_tags", label: "Recovery Tags", guidance: "Use for recovery, reuse, reclaim, or ZLD-related goals.", allowed_tags: ["IPA recovery", "KI solution recovery", "cobalt recovery", "metal recovery", "phosphate recovery", "water reclamation", "wastewater reuse", "ultrapure water", "zero liquid discharge"], allow_free_text: true },
      { key: "source_tags", label: "Source Tags", guidance: "Use for wastewater origin, fab area, or source stream.", allowed_tags: ["semiconductor wastewater", "CMP wastewater", "hydrofluoric acid wastewater", "photolithography wastewater", "wafer cleaning wastewater", "plasma wet scrubber wastewater"], allow_free_text: true },
      { key: "study_tags", label: "Study Tags", guidance: "Use for scale, modeling, or optimization study type.", allowed_tags: ["pilot-scale study", "process simulation", "thermodynamic modeling", "treatment optimization"], allow_free_text: true },
      { key: "target_tags", label: "Target Tags", guidance: "Use for contaminant, parameter, or removal objective.", allowed_tags: ["fluoride removal", "PFOS removal", "SDS removal", "TOC removal", "TDS removal", "silica removal", "copper removal", "heavy metals removal", "nitrogen removal", "total nitrogen removal", "ammonium nitrogen removal", "orthophosphate removal", "organic removal", "photoresist removal", "contaminant degradation", "turbidity reduction"], allow_free_text: true },
      { key: "technology_tags", label: "Technology Tags", guidance: "Use for treatment method or reactor/process used.", allowed_tags: ["reverse osmosis", "ultrafiltration", "membrane bioreactor", "MBR-RO", "advanced oxidation process", "UV oxidation", "adsorption", "ion exchange", "electrochemical treatment", "electrocoagulation", "electrodeionization", "chemical precipitation", "coagulation", "flocculation", "dissolved air flotation", "air stripping", "vacuum evaporation", "crystallization", "sequence batch reactor", "fluidized bed reactor", "aerobic treatment", "aerobic denitrification", "biological nutrient removal", "biological treatment", "biosorption"], allow_free_text: true },
    ],
  };
}

function cloneTagSpecConfig(config) {
  return JSON.parse(JSON.stringify(config || defaultTagSpecConfig()));
}

function summaryFieldTypeOptions(selected) {
  return ["string", "boolean", "string_list", "object_list"]
    .map((value) => `<option value="${value}"${value === selected ? " selected" : ""}>${value}</option>`)
    .join("");
}

function summaryFallbackPolicyOptions(selected) {
  return ["allow_free_text", "prefer_enum_only"]
    .map((value) => `<option value="${value}"${value === selected ? " selected" : ""}>${value}</option>`)
    .join("");
}

function renderSummarySchemaFieldsEditor() {
  if (!els.librarySummaryFieldsList) {
    return;
  }
  const fields = Array.isArray(state.sessionSummaryEditorConfig?.schema_fields)
    ? state.sessionSummaryEditorConfig.schema_fields
    : [];
  els.librarySummaryFieldsList.innerHTML = fields.map((field, index) => {
    const isSummary = field.path === "summary";
    return `
      <div class="card">
        <div class="row">
          <label><input type="checkbox" data-summary-field-enabled="${index}"${field.enabled ? " checked" : ""}${isSummary ? " disabled" : ""}> Enabled</label>
          <button type="button" data-summary-field-remove="${index}"${isSummary ? " disabled" : ""}>Remove</button>
        </div>
        <label>Label<input type="text" data-summary-field-label="${index}" value="${escapeHtml(field.label || "")}" placeholder="Field label"></label>
        <label>Path<input type="text" data-summary-field-path="${index}" value="${escapeHtml(field.path || "")}" placeholder="root.nested_field"${isSummary ? " readonly" : ""}></label>
        <label>Description<input type="text" data-summary-field-description="${index}" value="${escapeHtml(field.description || "")}" placeholder="Short extraction hint"></label>
        <label>Type<select data-summary-field-type="${index}"${isSummary ? " disabled" : ""}>${summaryFieldTypeOptions(field.field_type || "string")}</select></label>
        <label>Object Item Fields<input type="text" data-summary-field-items="${index}" value="${escapeHtml((field.object_item_fields || []).join(", "))}" placeholder="comma, separated, keys"${field.field_type === "object_list" ? "" : " disabled"}></label>
      </div>`;
  }).join("");
}

function renderSummaryControlledValuesEditor() {
  if (!els.librarySummaryControlledValuesList) {
    return;
  }
  const rows = Array.isArray(state.sessionSummaryEditorConfig?.controlled_values)
    ? state.sessionSummaryEditorConfig.controlled_values
    : [];
  els.librarySummaryControlledValuesList.innerHTML = rows.map((row, index) => `
    <div class="card">
      <div class="row">
        <strong>Controlled Values ${index + 1}</strong>
        <button type="button" data-summary-controlled-remove="${index}">Remove</button>
      </div>
      <label>Field Path<input type="text" data-summary-controlled-path="${index}" value="${escapeHtml(row.field_path || "")}" placeholder="field.path"></label>
      <label>Allowed Values<input type="text" data-summary-controlled-values="${index}" value="${escapeHtml((row.allowed_values || []).join(", "))}" placeholder="value1, value2, value3"></label>
      <label>Fallback Policy<select data-summary-controlled-policy="${index}">${summaryFallbackPolicyOptions(row.fallback_policy || "allow_free_text")}</select></label>
    </div>`).join("");
}

function updateSummaryPromptBuilderView() {
  renderSummarySchemaFieldsEditor();
  renderSummaryControlledValuesEditor();
}

function collectSummaryEditorConfigFromForm() {
  const fields = Array.from(els.librarySummaryFieldsList?.querySelectorAll("[data-summary-field-label]") || []).map((input) => {
    const index = Number.parseInt(input.getAttribute("data-summary-field-label") || "-1", 10);
    const label = String(input.value || "").trim();
    const path = String(els.librarySummaryFieldsList?.querySelector(`[data-summary-field-path="${index}"]`)?.value || "").trim();
    const description = String(els.librarySummaryFieldsList?.querySelector(`[data-summary-field-description="${index}"]`)?.value || "").trim();
    const type = String(els.librarySummaryFieldsList?.querySelector(`[data-summary-field-type="${index}"]`)?.value || "string").trim();
    const enabledInput = els.librarySummaryFieldsList?.querySelector(`[data-summary-field-enabled="${index}"]`);
    const itemsRaw = String(els.librarySummaryFieldsList?.querySelector(`[data-summary-field-items="${index}"]`)?.value || "").trim();
    const objectItemFields = itemsRaw ? itemsRaw.split(",").map((value) => value.trim()).filter(Boolean) : [];
    return {
      id: state.sessionSummaryEditorConfig?.schema_fields?.[index]?.id || `field_${index + 1}`,
      label,
      path,
      description,
      field_type: type,
      enabled: enabledInput ? Boolean(enabledInput.checked) : true,
      object_item_fields: type === "object_list" ? objectItemFields : [],
    };
  });
  const controlledValues = Array.from(els.librarySummaryControlledValuesList?.querySelectorAll("[data-summary-controlled-path]") || []).map((input) => {
    const index = Number.parseInt(input.getAttribute("data-summary-controlled-path") || "-1", 10);
    const fieldPath = String(input.value || "").trim();
    const valuesRaw = String(els.librarySummaryControlledValuesList?.querySelector(`[data-summary-controlled-values="${index}"]`)?.value || "").trim();
    return {
      field_path: fieldPath,
      allowed_values: valuesRaw ? valuesRaw.split(",").map((value) => value.trim()).filter(Boolean) : [],
      fallback_policy: String(els.librarySummaryControlledValuesList?.querySelector(`[data-summary-controlled-policy="${index}"]`)?.value || "allow_free_text"),
    };
  });
  return {
    summary_focus: String(els.librarySummaryFocusInput?.value || "").trim(),
    schema_fields: fields,
    controlled_values: controlledValues,
  };
}

function isParsedReady(sourceId) {
  const annotation = annotationForSource(sourceId);
  return annotation.summary_block_reason !== "parsed_text_required"
    || annotation.tag_suggestion_block_reason !== "parsed_text_required";
}

function tagSearchBlob(sourceId) {
  const annotation = annotationForSource(sourceId);
  return `${(annotation.freeform_tags || []).join(" ")} ${(annotation.approved_tags || []).join(" ")}`.toLowerCase();
}

function discoverRunId(session = activeSession()) {
  return (session?.discoveryRunId || "").trim();
}

function resultsRunId(session = activeSession()) {
  return (session?.resultsRunId || session?.discoveryRunId || "").trim();
}

function sessionSourcesPath(sessionId, status, limit, offset = 0) {
  return `/v1/sessions/${encodeURIComponent(sessionId)}/sources?status=${encodeURIComponent(status)}&limit=${limit}&offset=${offset}`;
}

function saveToken() {
  if (state.token) {
    localStorage.setItem(AUTH_STORAGE_KEY, state.token);
  } else {
    localStorage.removeItem(AUTH_STORAGE_KEY);
  }
}

function normalizeHttpUrl(value) {
  const normalized = String(value || "").trim();
  if (!normalized) {
    return "";
  }
  try {
    const url = new URL(normalized);
    if (!["http:", "https:"].includes(url.protocol)) {
      return "";
    }
    return url.href.replace(/\/$/, "");
  } catch {
    return "";
  }
}

function normalizeSessionContext(value) {
  return String(value || "").trim();
}

function normalizeProviderLimits(raw) {
  const source = raw && typeof raw === "object" ? raw : {};
  const bounded = (value, fallback, max) => {
    const parsed = Number.parseInt(String(value ?? ""), 10);
    if (!Number.isFinite(parsed) || parsed < 1) {
      return fallback;
    }
    return Math.min(parsed, max);
  };
  return {
    openalex: bounded(source.openalex, DEFAULT_PROVIDER_LIMITS.openalex, 200),
    semantic_scholar: bounded(source.semantic_scholar, DEFAULT_PROVIDER_LIMITS.semantic_scholar, 100),
    brave: bounded(source.brave, DEFAULT_PROVIDER_LIMITS.brave, 20),
  };
}

function formatIsoTime(value) {
  if (!value) {
    return "-";
  }
  const ts = Date.parse(value);
  if (Number.isNaN(ts)) {
    return value;
  }
  return new Date(ts).toLocaleString();
}

function beginBusy(label) {
  state.inFlight += 1;
  state.busyLabel = label || state.busyLabel;
  renderActivity();
}

function endBusy() {
  state.inFlight = Math.max(0, state.inFlight - 1);
  if (state.inFlight === 0) {
    state.busyLabel = "";
  }
  renderActivity();
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.authEnabled && state.token) {
    headers.set("Authorization", `Bearer ${state.token}`);
  }
  let response;
  try {
    response = await fetch(path, { ...options, headers });
    clearOfflineState();
  } catch (error) {
    setOfflineState(errorDetail(error) || "server_unreachable");
    throw error;
  }
  if (response.status === 304) {
    return { ok: true, status: 304, data: null, response };
  }
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `request_failed:${response.status}`);
  }
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return { ok: true, status: response.status, data: await response.json(), response };
  }
  return { ok: true, status: response.status, data: await response.blob(), response };
}

async function fetchAllPages(fetchPage, pageSize = 1000) {
  const items = [];
  let offset = 0;
  while (true) {
    const page = await fetchPage(offset, pageSize);
    const pageItems = Array.isArray(page.items) ? page.items : [];
    items.push(...pageItems);
    const total = Number(page.total ?? pageItems.length);
    if (!pageItems.length || items.length >= total) {
      break;
    }
    offset += pageSize;
  }
  return items;
}

function pagedRows(rows, page, pageSize) {
  const safePage = Math.max(page, 0);
  const start = safePage * pageSize;
  return rows.slice(start, start + pageSize);
}

function uniqueIds(values) {
  return Array.from(new Set((values || []).filter(Boolean)));
}

async function openPdfArtifactPreview(artifactId) {
  if (!artifactId) {
    return { ok: false, reason: "missing_artifact" };
  }
  const previewWindow = window.open("", "_blank");
  if (!previewWindow) {
    return { ok: false, reason: "popup_blocked" };
  }
  try {
    previewWindow.document.title = "Loading PDF...";
    previewWindow.document.body.innerHTML = `
      <main style="font-family: sans-serif; padding: 24px; line-height: 1.5;">
        <h1 style="font-size: 1.1rem; margin: 0 0 12px;">Loading PDF preview...</h1>
        <p style="margin: 0; color: #555;">The document is being fetched now.</p>
      </main>
    `;
  } catch {
    // Ignore cross-window DOM write issues and continue with fetch/load.
  }
  let result;
  try {
    result = await api(`/v1/acquisition/artifacts/${encodeURIComponent(artifactId)}/content`);
  } catch (error) {
    const message = errorDetail(error) || "request_failed";
    try {
      previewWindow.document.title = "PDF Preview Failed";
      previewWindow.document.body.innerHTML = `
        <main style="font-family: sans-serif; padding: 24px; line-height: 1.5;">
          <h1 style="font-size: 1.1rem; margin: 0 0 12px;">Unable to load PDF preview.</h1>
          <p style="margin: 0; color: #555;">${escapeHtml(message)}</p>
        </main>
      `;
    } catch {
      // Ignore and still return the fetch error state.
    }
    return { ok: false, reason: "fetch_failed", message };
  }
  const blob = result.data;
  if (!(blob instanceof Blob)) {
    try {
      previewWindow.document.title = "PDF Preview Failed";
      previewWindow.document.body.innerHTML = `
        <main style="font-family: sans-serif; padding: 24px; line-height: 1.5;">
          <h1 style="font-size: 1.1rem; margin: 0 0 12px;">Unable to load PDF preview.</h1>
          <p style="margin: 0; color: #555;">Preview content was not a valid PDF response.</p>
        </main>
      `;
    } catch {
      // Ignore and still return invalid content state.
    }
    return { ok: false, reason: "invalid_blob" };
  }
  const blobUrl = URL.createObjectURL(blob);
  previewWindow.location.replace(blobUrl);
  setTimeout(() => URL.revokeObjectURL(blobUrl), 60_000);
  return { ok: true };
}

function pdfPreviewStatusMessage(result, title) {
  if (result?.ok) {
    return `Opened PDF preview: ${title}`;
  }
  if (result?.reason === "popup_blocked") {
    return "Browser blocked the PDF preview tab. Allow popups for this site and try again.";
  }
  if (result?.reason === "fetch_failed") {
    return `Unable to load PDF preview: ${result.message || "request_failed"}`;
  }
  if (result?.reason === "invalid_blob") {
    return "Unable to load PDF preview: invalid PDF response.";
  }
  return "Unable to open PDF preview.";
}

function libraryAnnotationTargetIds(rows = state.libraryRows) {
  const candidates = state.libraryFilteredRows.length ? state.libraryFilteredRows : rows;
  const visible = candidates.slice(0, LIBRARY_ANNOTATION_BATCH_SIZE).map((item) => item.id);
  return uniqueIds([state.selectedLibrarySourceId, ...visible]);
}

function errorDetail(error) {
  if (!(error instanceof Error)) {
    return String(error || "");
  }
  return error.message || "";
}

function setLibraryState(message, stickyMs = 0) {
  if (!els.libraryState) {
    return;
  }
  els.libraryState.textContent = message;
  state.libraryStatusStickyUntil = stickyMs > 0 ? Date.now() + stickyMs : 0;
}

function canOverwriteLibraryState() {
  return Date.now() >= Number(state.libraryStatusStickyUntil || 0);
}

function setAdvancedParseState(message, stickyMs = 0) {
  if (!els.advancedParseState) {
    return;
  }
  els.advancedParseState.textContent = message;
  state.advancedParseStatusStickyUntil = stickyMs > 0 ? Date.now() + stickyMs : 0;
}

function canOverwriteAdvancedParseState() {
  return Date.now() >= Number(state.advancedParseStatusStickyUntil || 0);
}

function isRunNotFoundError(error) {
  const detail = errorDetail(error).toLowerCase();
  return detail.includes("run_not_found") || detail.includes("\"run_not_found\"");
}

async function rebindSessionToLatestRun(reasonText) {
  const session = activeSession();
  if (!session) {
    return false;
  }
  if (!state.latest.discovery) {
    try {
      await loadLatestIds();
    } catch {
      // best effort
    }
  }
  const latestRunId = state.latest.discovery || "";
  const latestSessionId = (state.latest.discoverySession || "").trim();
  if (!latestRunId) {
    els.sessionState.textContent = "No discovery runs found yet. Start discovery.";
    if (els.discoverState) {
      els.discoverState.textContent = "No discovery runs found yet. Start discovery.";
    }
    return false;
  }
  const previous = session.discoveryRunId || "";
  if (latestSessionId) {
    session.id = latestSessionId;
    state.activeSessionId = latestSessionId;
    state.pendingSessionId = latestSessionId;
  }
  session.discoveryRunId = latestRunId;
  session.resultsRunId = latestRunId;
  persistSessions();
  renderSessions();
  if (reasonText) {
    els.sessionState.textContent = `${reasonText} Switched to latest run: ${latestRunId}.`;
  }
  if (!previous || previous !== latestRunId) {
    if (els.discoverState) {
      els.discoverState.textContent = `Recovered session binding to latest run: ${latestRunId}.`;
    }
  }
  return true;
}

function backendSessionIdForRun(runPayload) {
  return String(runPayload?.session_id || "").trim();
}

async function recoverMissingActiveSession(reasonText) {
  const session = activeSession();
  if (!session) {
    return false;
  }
  const runId = discoverRunId(session) || state.latest.discovery || "";
  if (!runId) {
    return rebindSessionToLatestRun(reasonText);
  }
  let backendSessionId = "";
  try {
    const result = await api(`/v1/discovery/runs/${encodeURIComponent(runId)}`);
    backendSessionId = backendSessionIdForRun(result.data);
  } catch {
    // Fall back to the latest binding if the run status call is unavailable.
  }
  backendSessionId = backendSessionId || (state.latest.discoverySession || "").trim();
  if (!backendSessionId) {
    return rebindSessionToLatestRun(reasonText);
  }
  session.id = backendSessionId;
  state.activeSessionId = backendSessionId;
  state.pendingSessionId = backendSessionId;
  session.discoveryRunId = runId;
  session.resultsRunId = runId;
  persistSessions();
  renderSessions();
  els.sessionState.textContent = `${reasonText} Reattached to backend session: ${backendSessionId}.`;
  return true;
}

async function ensureBoundDiscoveryRun() {
  const session = activeSession();
  if (!session) {
    return "";
  }
  const runId = discoverRunId(session);
  if (!runId) {
    await rebindSessionToLatestRun("Session had no discovery run.");
    return discoverRunId(activeSession());
  }
  try {
    await api(`/v1/discovery/runs/${encodeURIComponent(runId)}`);
    return runId;
  } catch (error) {
    if (!isRunNotFoundError(error)) {
      throw error;
    }
    await rebindSessionToLatestRun("Saved discovery run was not found.");
    return discoverRunId(activeSession());
  }
}

function setProgress(percent, label) {
  const value = Number.isFinite(percent) ? percent : 0;
  els.headerProgress.value = value;
  els.headerProgressLabel.textContent = label || `${Math.round(value)}%`;
}

function setOfflineState(detail) {
  state.serverOffline = true;
  state.offlineMessage = detail || "server unavailable";
  els.authStatus.textContent = "Auth: offline";
  els.aiStatus.textContent = "AI: offline";
  els.dbStatus.textContent = "DB: offline";
  els.footerSystem.textContent = "System: offline";
  els.footerAi.textContent = "AI: offline";
  els.footerDb.textContent = "DB: offline";
  els.footerUpdated.textContent = "Last update: offline";
  const message = `Offline: ${state.offlineMessage}`;
  els.discoverState.textContent = message;
  els.reviewState.textContent = message;
  els.documentsState.textContent = message;
  els.libraryState.textContent = message;
  if (els.advancedEventsState) {
    els.advancedEventsState.textContent = message;
  }
  renderShell();
  renderActivity();
}

function clearOfflineState() {
  const wasOffline = state.serverOffline;
  if (!wasOffline) {
    state.healthFailureCount = 0;
    return;
  }
  state.serverOffline = false;
  state.offlineMessage = "";
  state.healthFailureCount = 0;
  renderShell();
  renderActivity();
  window.setTimeout(() => {
    refreshAll().catch(() => {
      // best effort recovery refresh
    });
  }, 0);
}

function renderActivity() {
  let text = "Idle";
  let active = false;
  const annotationJobs = Object.values(state.paperAnnotations || {});
  const summaryActive = annotationJobs.some((item) => item.summary_status === "queued" || item.summary_status === "running");
  const tagActive = annotationJobs.some((item) => item.tag_suggestion_status === "queued" || item.tag_suggestion_status === "running");
  const candidateActive = ["queued", "running"].includes(String(state.sessionTagReview?.candidate_generation_status || ""));
  const assignmentActive = ["queued", "running"].includes(String(state.sessionTagReview?.tag_assignment_status || ""));
  if (state.serverOffline) {
    text = `Offline: ${state.offlineMessage || "server unavailable"}`;
  } else if (state.inFlight > 0) {
    text = state.busyLabel || "Refreshing session state";
    active = true;
  } else if (state.currentAcquisitionStatus?.stage_status === "running") {
    text = state.currentAcquisitionStatus.message || "Downloading documents";
    active = true;
  } else if (state.currentDiscoveryStatus?.stage_status === "running") {
    text = state.currentDiscoveryStatus.message || "Searching providers";
    active = true;
  } else if (summaryActive) {
    text = "Generating summaries";
    active = true;
  } else if (candidateActive) {
    text = "Generating candidate tags";
    active = true;
  } else if (assignmentActive || tagActive) {
    text = "Applying approved tags";
    active = true;
  } else if (state.currentDiscoveryStatus?.stage_status === "waiting_user") {
    text = state.currentDiscoveryStatus.message || "Waiting for review";
  }
  els.activityLine.textContent = text;
  els.activityIndicator.hidden = !active;
}

function renderShell() {
  els.navButtons.forEach((button) => {
    const page = button.dataset.page;
    button.classList.toggle("active", page === state.activePage);
  });
  Object.entries(els.pages).forEach(([page, node]) => {
    node.hidden = page !== state.activePage;
  });
  els.apiKeyInput.value = state.token;
  if (els.fileMenuPanel) {
    els.fileMenuPanel.hidden = !state.fileMenuOpen;
  }
  if (els.fileMenuBtn) {
    els.fileMenuBtn.setAttribute("aria-expanded", state.fileMenuOpen ? "true" : "false");
  }
  if (els.newSessionForm) {
    els.newSessionForm.hidden = !state.showNewSessionForm;
  }
  if (els.newSessionNameInput) {
    els.newSessionNameInput.value = state.newSessionDraftName;
  }
  if (els.newSessionContextInput) {
    els.newSessionContextInput.value = state.newSessionDraftContext;
  }
  if (els.internalRepoUrlInput) {
    els.internalRepoUrlInput.value = state.internalRepositoryBaseUrl;
  }
  renderReviewFilterChips();
  renderReviewSortButtons();
  renderStopButton();
  applyOfflineActionState();
  updateSuggestionAvailability();
  renderAdvancedOperationalEvents();
  renderActivity();
}

function applyOfflineActionState() {
  const controls = [
    "addQueryBtn",
    "generateQuerySuggestionsBtn",
    "runDiscoveryBtn",
    "runNextCitationBtn",
    "resumeCitationBtn",
    "saveSessionContextBtn",
    "reviewAcceptBtn",
    "reviewRejectBtn",
    "reviewLaterBtn",
    "reviewBookmarkBtn",
    "downloadMissingBtn",
    "retryFailedBtn",
    "documentsExportCsvBtn",
    "documentsRowActionBtn",
    "documentsBookmarkBtn",
    "saveInternalRepoUrlBtn",
    "libraryAddBtn",
    "libraryRemoveBtn",
    "libraryBookmarkBtn",
    "libraryZipBtn",
    "libraryMetadataBtn",
    "bookmarksCreateSessionBtn",
    "bookmarksRemoveBtn",
    "saveAiSettingsBtn",
    "stopRunningBtn",
    "saveProviderSettingsBtn",
    "globalSearchBtn",
    "runLookupBtn",
  ];
  controls.forEach((id) => {
    if (els[id]) {
      if (state.serverOffline) {
        els[id].disabled = true;
      }
    }
  });
  const hasSelectedReviewItem = state.reviewIndex >= 0 && state.reviewIndex < state.reviewItems.length;
  if (els.reviewAcceptBtn) {
    els.reviewAcceptBtn.disabled = state.serverOffline || !hasSelectedReviewItem;
  }
  if (els.reviewRejectBtn) {
    els.reviewRejectBtn.disabled = state.serverOffline || !hasSelectedReviewItem;
  }
  if (els.reviewLaterBtn) {
    els.reviewLaterBtn.disabled = state.serverOffline || !hasSelectedReviewItem;
  }
  const uploadButton = els.batchUploadForm?.querySelector('button[type="submit"]');
  if (uploadButton && state.serverOffline) {
    uploadButton.disabled = true;
  }
  updateSessionContextControls(activeSession());
}

function renderReviewFilterChips() {
  if (!els.reviewFilterButtons) {
    return;
  }
  els.reviewFilterButtons.forEach((button) => {
    button.classList.toggle("active", button.dataset.reviewFilter === state.reviewQueue);
  });
}

function resetReviewSort() {
  state.reviewSort = { key: "lineage", dir: "desc" };
}

function defaultReviewSortDir(key) {
  if (key === "lineage") {
    return "desc";
  }
  if (key === "title" || key === "review_status") {
    return "asc";
  }
  return "desc";
}

function defaultDocumentsSortDir(key) {
  if (key === "rank" || key === "title" || key === "status" || key === "lineage" || key === "doi") {
    return "asc";
  }
  return "desc";
}

function defaultLibrarySortDir(key) {
  if (key === "rank" || key === "title" || key === "lineage") {
    return "asc";
  }
  return "desc";
}

function compareReviewItems(a, b) {
  const { key, dir } = state.reviewSort;
  if (key === "lineage") {
    const lineageCompare = compareLineageValues(a, b, dir);
    if (lineageCompare !== 0) {
      return lineageCompare;
    }
  } else if (key === "title" || key === "review_status") {
    const factor = dir === "asc" ? 1 : -1;
    const textA = key === "review_status" ? String(a.review_status || "") : String(a.title || "");
    const textB = key === "review_status" ? String(b.review_status || "") : String(b.title || "");
    const textCompare = textA.localeCompare(textB) * factor;
    if (textCompare !== 0) {
      return textCompare;
    }
  } else {
    const factor = dir === "asc" ? 1 : -1;
    const valueA = Number(a[key] ?? 0);
    const valueB = Number(b[key] ?? 0);
    if (valueA !== valueB) {
      return (valueA - valueB) * factor;
    }
  }
  return String(a.title || "").localeCompare(String(b.title || ""));
}

function renderReviewSortButtons() {
  if (!els.reviewSortButtons) {
    return;
  }
  els.reviewSortButtons.forEach((button) => {
    const key = button.dataset.reviewSort;
    const active = key === state.reviewSort.key;
    button.classList.toggle("active", active);
    if (button.classList.contains("icon-sort-btn")) {
      button.dataset.sortDir = active ? state.reviewSort.dir : "";
      return;
    }
    const suffix = active ? (state.reviewSort.dir === "asc" ? " ▲" : " ▼") : "";
    const labels = {
      review_status: "Status",
      title: "Title",
    };
    const base = labels[key] || "Sort";
    button.textContent = `${base}${suffix}`;
  });
}

function renderDocumentsSortButtons() {
  if (!els.documentsSortButtons) {
    return;
  }
  const labels = {
    lineage: "#",
    score: "Score",
    year: "Year",
    citations: "Cit",
    title: "Title",
    doi: "DOI",
    status: "Status",
  };
  els.documentsSortButtons.forEach((button) => {
    const key = button.dataset.documentsSort;
    const active = key === state.documentsSort.key;
    button.classList.toggle("active", active);
    if (button.classList.contains("icon-sort-btn")) {
      button.dataset.sortDir = active ? state.documentsSort.dir : "";
      return;
    }
    const suffix = active ? (state.documentsSort.dir === "asc" ? " ▲" : " ▼") : "";
    button.textContent = `${labels[key] || "Sort"}${suffix}`;
  });
}

function renderDocumentsPager(totalRows) {
  if (!els.documentsPageState) {
    return;
  }
  const total = totalRows;
  const start = total === 0 ? 0 : state.documentsPage * DOCUMENTS_PAGE_SIZE + 1;
  const end = Math.min((state.documentsPage + 1) * DOCUMENTS_PAGE_SIZE, total);
  els.documentsPageState.textContent = `${start}-${end} of ${total}`;
  if (els.documentsPrevBtn) {
    els.documentsPrevBtn.disabled = state.documentsPage <= 0;
  }
  if (els.documentsNextBtn) {
    els.documentsNextBtn.disabled = end >= total;
  }
}

function renderLibrarySortButtons() {
  if (!els.librarySortButtons) {
    return;
  }
  const labels = {
    title: "Title",
  };
  els.librarySortButtons.forEach((button) => {
    const key = button.dataset.librarySort;
    const active = key === state.librarySort.key;
    button.classList.toggle("active", active);
    if (button.classList.contains("icon-sort-btn")) {
      button.dataset.sortDir = active ? state.librarySort.dir : "";
      return;
    }
    const suffix = active ? (state.librarySort.dir === "asc" ? " ▲" : " ▼") : "";
    button.textContent = `${labels[key] || "Sort"}${suffix}`;
  });
}

function sortedDocumentRows() {
  const rows = [...state.documentRows];
  const { key, dir } = state.documentsSort;
  const factor = dir === "asc" ? 1 : -1;
  rows.sort((left, right) => {
    if (key === "lineage") {
      const lineageCompare = compareLineageValues(left.source, right.source, dir);
      if (lineageCompare !== 0) {
        return lineageCompare;
      }
      return String(left.title || "").localeCompare(String(right.title || ""));
    }
    if (key === "title" || key === "status") {
      return String(left[key] || "").localeCompare(String(right[key] || "")) * factor;
    }
    if (key === "doi") {
      return String(left.source?.doi || "").localeCompare(String(right.source?.doi || "")) * factor;
    }
    const leftValue = left[key] === "-" ? -Infinity : Number(left[key] ?? 0);
    const rightValue = right[key] === "-" ? -Infinity : Number(right[key] ?? 0);
    if (leftValue !== rightValue) {
      return (leftValue - rightValue) * factor;
    }
    return String(left.title || "").localeCompare(String(right.title || ""));
  });
  return rows;
}

function sortedLibraryRows(items) {
  const rows = [...items];
  const { key, dir } = state.librarySort;
  const factor = dir === "asc" ? 1 : -1;
  rows.sort((left, right) => {
    if (key === "lineage") {
      const lineageCompare = compareLineageValues(left, right, dir);
      if (lineageCompare !== 0) {
        return lineageCompare;
      }
      return String(left.title || "").localeCompare(String(right.title || ""));
    }
    if (key === "rank") {
      const leftRank = state.libraryRows.findIndex((item) => item.id === left.id);
      const rightRank = state.libraryRows.findIndex((item) => item.id === right.id);
      return (leftRank - rightRank) * factor;
    }
    if (key === "title") {
      return String(left.title || "").localeCompare(String(right.title || "")) * factor;
    }
    const leftValue = left[key] == null ? -Infinity : Number(left[key]);
    const rightValue = right[key] == null ? -Infinity : Number(right[key]);
    if (leftValue !== rightValue) {
      return (leftValue - rightValue) * factor;
    }
    return String(left.title || "").localeCompare(String(right.title || ""));
  });
  return rows;
}

function currentStoppableTask() {
  const session = activeSession();
  if (session.acquisitionRunId && ["queued", "running"].includes(state.currentAcquisitionStatus?.stage_status || "")) {
    return {
      kind: "acquisition",
      runId: session.acquisitionRunId,
      label: state.currentAcquisitionStatus?.stage_status === "queued" ? "Stop Queued Acquisition" : "Stop Acquisition",
    };
  }
  if (session.discoveryRunId && ["queued", "running"].includes(state.currentDiscoveryStatus?.stage_status || "")) {
    return {
      kind: "discovery",
      runId: session.discoveryRunId,
      label: state.currentDiscoveryStatus?.stage_status === "queued" ? "Stop Queued Discovery" : "Stop Discovery",
    };
  }
  return null;
}

function renderStopButton() {
  if (!els.stopRunningBtn) {
    return;
  }
  const task = currentStoppableTask();
  els.stopRunningBtn.disabled = !task;
  els.stopRunningBtn.textContent = task ? task.label : "Stop Running Task";
}

function renderSessionQueries() {
  const session = activeSession();
  els.discoverQueryList.innerHTML = "";
  session.queries.forEach((query) => {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = query.text;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      session.queries = session.queries.filter((entry) => entry.id !== query.id);
      persistSessions();
      renderSessions();
    });
    li.appendChild(label);
    li.appendChild(remove);
    els.discoverQueryList.appendChild(li);
  });
  updateQuerySelectionState();
}

function renderSessions() {
  const current = activeSession();
  const selectedId = state.pendingSessionId || current.id;
  els.sessionSelect.innerHTML = "";
  state.sessions.forEach((session) => {
    const option = document.createElement("option");
    option.value = session.id;
    option.textContent = session.name;
    option.selected = session.id === selectedId;
    els.sessionSelect.appendChild(option);
  });
  els.sessionState.textContent = `Active: ${current.name} | ${state.sessions.length} saved session(s).`;
  if (els.activeSessionLabel) {
    els.activeSessionLabel.textContent = `Active: ${current.name}`;
  }
  els.discoverQueryInput.value = "";
  els.sessionContextInput.value = current.sessionContext || "";
  if (els.discoverOpenalexLimitInput) {
    const providerLimits = normalizeProviderLimits(current.providerLimits);
    els.discoverOpenalexLimitInput.value = String(providerLimits.openalex);
    els.discoverSemanticScholarLimitInput.value = String(providerLimits.semantic_scholar);
    els.discoverBraveLimitInput.value = String(providerLimits.brave);
  }
  els.sessionContextCounter.textContent = `${normalizeSessionContext(current.sessionContext).length} / ${SESSION_CONTEXT_MAX}`;
  els.sessionContextUpdated.textContent = `Updated: ${formatIsoTime(current.sessionContextUpdatedAt)}`;
  els.sessionContextState.textContent = defaultSessionContextState(current);
  updateSessionContextControls(current);
  renderSessionQueries();
  renderSuggestedQueries();
}

function updateSessionProviderLimits() {
  const session = activeSession();
  if (!session || !els.discoverOpenalexLimitInput) {
    return;
  }
  session.providerLimits = normalizeProviderLimits({
    openalex: els.discoverOpenalexLimitInput.value,
    semantic_scholar: els.discoverSemanticScholarLimitInput.value,
    brave: els.discoverBraveLimitInput.value,
  });
  els.discoverOpenalexLimitInput.value = String(session.providerLimits.openalex);
  els.discoverSemanticScholarLimitInput.value = String(session.providerLimits.semantic_scholar);
  els.discoverBraveLimitInput.value = String(session.providerLimits.brave);
  persistSessions();
  els.discoverProviderLimitsState.textContent = "Provider limits saved with the active session.";
}

function updateQuerySelectionState() {
  const count = activeQueries().length;
  els.discoverSelectedCount.textContent = `Selected queries: ${count}`;
  const contextLength = normalizeSessionContext(activeSession()?.sessionContext || "").length;
  els.runDiscoveryBtn.disabled = count === 0 || contextLength === 0;
  if (contextLength === 0) {
    els.sessionContextState.textContent = "Session context is required before running discovery.";
  }
  applyOfflineActionState();
}

function renderSuggestedQueries() {
  if (!els.discoverSuggestedQueryList) {
    return;
  }
  els.discoverSuggestedQueryList.innerHTML = "";
  state.suggestedQueries.forEach((text) => {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = text;
    button.addEventListener("click", () => {
      const session = activeSession();
      if (!session.queries.some((query) => query.text.toLowerCase() === text.toLowerCase())) {
        session.queries.push({ id: `query_${Math.random().toString(36).slice(2, 10)}`, text, selected: true });
        persistSessions();
        renderSessions();
        state.suggestionStateSticky = true;
        els.discoverSuggestionsState.textContent = `Added suggested query: ${text}`;
      } else {
        state.suggestionStateSticky = true;
        els.discoverSuggestionsState.textContent = `Query already selected: ${text}`;
      }
    });
    li.appendChild(button);
    els.discoverSuggestedQueryList.appendChild(li);
  });
  if (!state.suggestedQueries.length) {
    if (!state.suggestionStateSticky) {
      els.discoverSuggestionsState.textContent = "No suggestions generated yet.";
    }
  }
}

function selectedBookmark() {
  return state.bookmarks.find((item) => item.id === state.selectedBookmarkId) || null;
}

function bookmarkMetadataHtml(bookmark) {
  const doiLink = bookmark?.doi_url
    ? `<a class="linkish" href="${escapeHtml(bookmark.doi_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(bookmark.doi || "Open DOI")}</a>`
    : "-";
  const sourceLink = bookmark?.source_url
    ? `<a class="linkish" href="${escapeHtml(bookmark.source_url)}" target="_blank" rel="noopener noreferrer">Open source</a>`
    : "-";
  return [
    `<span>Source session: ${escapeHtml(bookmark?.source_session_name || bookmark?.source_session_id || "-")}</span>`,
    `<span>Year: ${escapeHtml(bookmark?.year ?? "-")}</span>`,
    `<span>DOI: ${doiLink}</span>`,
    `<span>Link: ${sourceLink}</span>`,
  ].join(" ");
}

function renderBookmarkDetail() {
  const bookmark = selectedBookmark();
  if (!bookmark) {
    els.bookmarksTitle.textContent = "No bookmark selected.";
    els.bookmarksAbstract.textContent = "Select a bookmark to inspect its context and create a new session.";
    els.bookmarksMetadata.innerHTML = "Source session: - | Year: - | DOI: - | Link: -";
    els.bookmarksCreateSessionBtn.disabled = true;
    els.bookmarksRemoveBtn.disabled = true;
    return;
  }
  els.bookmarksTitle.textContent = bookmark.title || "Untitled bookmark";
  els.bookmarksAbstract.textContent = bookmark.abstract || "No abstract available.";
  els.bookmarksMetadata.innerHTML = bookmarkMetadataHtml(bookmark);
  els.bookmarksCreateSessionBtn.disabled = false;
  els.bookmarksRemoveBtn.disabled = false;
}

function renderBookmarksRows() {
  if (!els.bookmarksRows) {
    return;
  }
  els.bookmarksRows.innerHTML = "";
  if (!state.bookmarks.length) {
    els.bookmarksState.textContent = "No bookmarks saved yet.";
    state.selectedBookmarkId = "";
    renderBookmarkDetail();
    return;
  }
  els.bookmarksState.textContent = `${state.bookmarks.length} bookmark${state.bookmarks.length === 1 ? "" : "s"} loaded.`;
  if (!state.bookmarks.some((item) => item.id === state.selectedBookmarkId)) {
    state.selectedBookmarkId = state.bookmarks[0]?.id || "";
  }
  state.bookmarks.forEach((bookmark) => {
    const tr = document.createElement("tr");
    tr.classList.toggle("active", bookmark.id === state.selectedBookmarkId);
    const doiCell = bookmark.doi_url
      ? `<a href="${escapeHtml(bookmark.doi_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(bookmark.doi || "Open DOI")}</a>`
      : "-";
    tr.innerHTML = `
      <td><div class="bookmark-title">${escapeHtml(bookmark.title)}</div></td>
      <td>${escapeHtml(bookmark.source_session_name || bookmark.source_session_id || "-")}</td>
      <td>${escapeHtml(bookmark.year ?? "-")}</td>
      <td>${doiCell}</td>
    `;
    tr.addEventListener("click", () => {
      state.selectedBookmarkId = bookmark.id;
      renderBookmarksRows();
    });
    els.bookmarksRows.appendChild(tr);
  });
  renderBookmarkDetail();
}

async function loadBookmarks() {
  try {
    const result = await api("/v1/bookmarks?limit=500");
    state.bookmarks = Array.isArray(result.data?.items) ? result.data.items : [];
    if (!state.bookmarks.some((item) => item.id === state.selectedBookmarkId)) {
      state.selectedBookmarkId = state.bookmarks[0]?.id || "";
    }
    renderBookmarksRows();
  } catch (error) {
    state.bookmarks = [];
    state.selectedBookmarkId = "";
    if (els.bookmarksState) {
      els.bookmarksState.textContent = `Unable to load bookmarks: ${errorDetail(error)}`;
    }
    renderBookmarkDetail();
  }
}

async function addBookmark(sourceId) {
  const result = await api("/v1/bookmarks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_id: sourceId }),
  });
  await loadBookmarks();
  return result.data;
}

async function removeBookmark(bookmarkId) {
  beginBusy("Refreshing session state");
  try {
    await api(`/v1/bookmarks/${encodeURIComponent(bookmarkId)}`, { method: "DELETE" });
    await loadBookmarks();
    renderReviewRows();
    renderDocuments();
    renderLibraryRows();
    if (els.reviewState && state.activePage === "review") {
      els.reviewState.textContent = "Bookmark removed.";
    }
    if (els.documentsState && state.activePage === "documents") {
      els.documentsState.textContent = "Bookmark removed.";
    }
    if (els.libraryState && state.activePage === "library") {
      els.libraryState.textContent = "Bookmark removed.";
    }
  } finally {
    endBusy();
  }
}

async function toggleBookmarkForSource(sourceId) {
  const existing = bookmarkForSource(sourceId);
  if (existing) {
    await removeBookmark(existing.id);
    return false;
  }
  beginBusy("Refreshing session state");
  try {
    await addBookmark(sourceId);
    renderReviewRows();
    renderDocuments();
    renderLibraryRows();
    return true;
  } finally {
    endBusy();
  }
}

async function createSessionFromBookmark(bookmarkId) {
  beginBusy("Searching providers");
  try {
    const result = await api(`/v1/bookmarks/${encodeURIComponent(bookmarkId)}/create-session`, {
      method: "POST",
    });
    const payload = result.data || {};
    let session = state.sessions.find((entry) => entry.id === payload.session_id);
    if (!session) {
      session = normalizeSession({
        id: payload.session_id,
        name: payload.session_name,
        discoveryRunId: payload.discovery_run_id,
        resultsRunId: payload.discovery_run_id,
        queries: [],
      });
      state.sessions.push(session);
    } else {
      session.name = payload.session_name || session.name;
      session.discoveryRunId = payload.discovery_run_id || session.discoveryRunId;
      session.resultsRunId = payload.discovery_run_id || session.resultsRunId;
      session.queries = [];
    }
    state.activeSessionId = session.id;
    state.pendingSessionId = session.id;
    state.activePage = "discover";
    resetReviewSort();
    persistSessions();
    renderSessions();
    renderShell();
    resetSessionBoundPaneState();
    await loadSessionProfile(session.id);
    await refreshAll();
    els.sessionState.textContent = `Created session from bookmark: ${session.name}`;
  } catch (error) {
    if (els.bookmarksState) {
      els.bookmarksState.textContent = `Unable to create session: ${errorDetail(error)}`;
    }
  } finally {
    endBusy();
  }
}

function formatOperationalEvent(row) {
  const payload = row.payload || {};
  const prefix = `${row.timestamp} ${row.event}`;
  if (row.event === "provider_call") {
    return `${prefix} run=${payload.run_id || "-"} provider=${payload.provider || "-"} op=${payload.operation || "-"} ok=${payload.ok} latency_ms=${payload.latency_ms ?? "-"}${payload.error ? ` error=${payload.error}` : ""}`;
  }
  if (row.event === "run_summary") {
    return `${prefix} run=${payload.run_id || "-"} status=${payload.status || "-"} iter=${payload.current_iteration ?? "-"} counters=${JSON.stringify(payload.counters || {})}`;
  }
  if (row.event === "acquisition_download") {
    return `${prefix} acq=${payload.acq_run_id || "-"} source=${payload.source_id || "-"} domain=${payload.domain || "-"} status=${payload.status || "-"} latency_ms=${payload.latency_ms ?? "-"}${payload.error ? ` error=${payload.error}` : ""}`;
  }
  if (row.event === "acquisition_summary") {
    return `${prefix} acq=${payload.acq_run_id || "-"} status=${payload.status || "-"} counters=${JSON.stringify(payload.counters || {})}`;
  }
  if (row.event === "parse_document") {
    return `${prefix} parse=${payload.parse_run_id || "-"} doc=${payload.document_id || "-"} status=${payload.status || "-"} chunks=${payload.chunks ?? "-"}${payload.error ? ` error=${payload.error}` : ""}`;
  }
  if (row.event === "parse_index") {
    return `${prefix} parse=${payload.parse_run_id || "-"} status=${payload.status || "-"} docs=${payload.indexed_documents ?? "-"} chunks=${payload.indexed_chunks ?? "-"}${payload.error ? ` error=${payload.error}` : ""}`;
  }
  if (row.event === "parse_summary") {
    return `${prefix} parse=${payload.parse_run_id || "-"} status=${payload.status || "-"} counters=${JSON.stringify(payload.counters || {})}`;
  }
  if (row.event === "acquisition_http_call") {
    return `${prefix} acq=${payload.acq_run_id || "-"} method=${payload.method || "-"} domain=${payload.domain || "-"} status=${payload.status_code ?? "-"} latency_ms=${payload.latency_ms ?? "-"}${payload.error ? ` error=${payload.error}` : ""}`;
  }
  if (row.event === "acquisition_http_trace") {
    return `${prefix} acq=${payload.acq_run_id || "-"} source=${payload.source_id || "-"} attempts=${payload.attempt_count ?? "-"} selected=${payload.selected_url_source || "-"} final=${payload.final_status || "-"}${payload.error ? ` error=${payload.error}` : ""}`;
  }
  return `${prefix} ${JSON.stringify(payload)}`;
}

function renderAdvancedOperationalEvents() {
  if (!els.advancedEventsPauseBtn) {
    return;
  }
  els.advancedEventsPauseBtn.textContent = state.advancedEventsPaused ? "Resume" : "Pause";
  els.advancedEventsAutoscrollBtn.textContent = `Autoscroll: ${state.advancedEventsAutoscroll ? "On" : "Off"}`;
  els.advancedEventCounters.innerHTML = "";
  state.advancedEventGroupedCounts.forEach((row) => {
    const li = document.createElement("li");
    li.textContent = `${row.group}: ${row.count}`;
    els.advancedEventCounters.appendChild(li);
  });
  if (!state.advancedEventGroupedCounts.length) {
    const li = document.createElement("li");
    li.textContent = "No grouped counters yet.";
    els.advancedEventCounters.appendChild(li);
  }
  const lines = state.advancedEventRows.map(formatOperationalEvent);
  els.advancedEventsLog.textContent = lines.length ? lines.join("\n") : "No operational events loaded yet.";
  if (state.advancedEventsAutoscroll) {
    els.advancedEventsLog.scrollTop = els.advancedEventsLog.scrollHeight;
  }
}

function formatUnixTimestampSeconds(value) {
  if (!Number.isFinite(Number(value))) {
    return "-";
  }
  return new Date(Number(value) * 1000).toLocaleString();
}

function renderAdvancedDatabaseBackups() {
  if (!els.databaseBackupSelect || !els.databaseBackupList || !els.databaseRestoreTarget) {
    return;
  }
  const selectedValue = String(els.databaseBackupSelect.value || "");
  els.databaseRestoreTarget.textContent = state.advancedDatabaseTarget || "-";
  if (els.databaseBackupDir) {
    els.databaseBackupDir.textContent = state.advancedDatabaseBackupDir || "-";
  }
  if (els.databaseBackupPolicy) {
    const retention = Number(state.advancedDatabaseRetentionCount || 0);
    els.databaseBackupPolicy.textContent = `Hourly automatic backups, keeping latest ${retention || 0} automatic snapshots. Manual and pre-restore backups are kept until removed manually.`;
  }
  const activeDbKind = String(state.systemStatus?.db_target_kind || "");
  const unsafeDb = Boolean(activeDbKind && activeDbKind !== "live_app_db");
  if (els.databaseTargetWarning) {
    if (state.databaseTargetWarning) {
      els.databaseTargetWarning.textContent = `Warning: ${state.databaseTargetWarning}. Backup and restore should only be used on the live app database.`;
    } else if (activeDbKind) {
      els.databaseTargetWarning.textContent = unsafeDb
        ? `Warning: active DB kind is ${activeDbKind}.`
        : "Active database target matches the live app database.";
    } else {
      els.databaseTargetWarning.textContent = "DB target status unknown.";
    }
  }
  if (els.createDatabaseBackupBtn) {
    els.createDatabaseBackupBtn.disabled = unsafeDb;
  }
  if (els.restoreDatabaseBtn) {
    els.restoreDatabaseBtn.disabled = unsafeDb;
  }
  els.databaseBackupSelect.innerHTML = "";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = state.advancedDatabaseBackups.length ? "Select a backup" : "No backups found";
  els.databaseBackupSelect.appendChild(placeholder);
  state.advancedDatabaseBackups.forEach((item) => {
    const option = document.createElement("option");
    option.value = item.name;
    option.textContent = item.name;
    els.databaseBackupSelect.appendChild(option);
  });
  if (selectedValue && state.advancedDatabaseBackups.some((item) => item.name === selectedValue)) {
    els.databaseBackupSelect.value = selectedValue;
  }
  els.databaseBackupList.innerHTML = "";
  state.advancedDatabaseBackups.forEach((item) => {
    const li = document.createElement("li");
    const label = item.managed ? item.kind : "legacy";
    li.textContent = `[${label}] ${item.name} | ${formatUnixTimestampSeconds(item.mtime)} | ${Number(item.size_bytes || 0).toLocaleString()} bytes`;
    els.databaseBackupList.appendChild(li);
  });
  if (!state.advancedDatabaseBackups.length) {
    const li = document.createElement("li");
    li.textContent = "No local SQLite backups found.";
    els.databaseBackupList.appendChild(li);
  }
}

async function loadSessionProfile(sessionId) {
  if (!sessionId) {
    return;
  }
  const session = state.sessions.find((entry) => entry.id === sessionId);
  if (!session) {
    return;
  }
  try {
    const result = await api(`/v1/sessions/${encodeURIComponent(sessionId)}`);
    const profile = result.data || {};
    session.sessionContext = String(profile.session_context || "");
    session.sessionContextUpdatedAt = String(profile.updated_at || "");
    session.savedSessionContext = session.sessionContext;
    session.savedSessionContextUpdatedAt = session.sessionContextUpdatedAt;
    if (typeof profile.name === "string" && profile.name.trim()) {
      session.name = profile.name.trim();
    }
    persistSessions();
    if (state.activeSessionId === sessionId) {
      renderSessions();
      els.sessionContextState.textContent = defaultSessionContextState(session);
      updateSessionContextControls(session);
    }
  } catch (error) {
    const detail = errorDetail(error);
    if (detail.includes("session_not_found")) {
      session.sessionContext = "";
      session.sessionContextUpdatedAt = "";
      session.savedSessionContext = "";
      session.savedSessionContextUpdatedAt = "";
      if (state.activeSessionId === sessionId) {
        const recovered = await recoverMissingActiveSession("Saved session was not found.");
        if (recovered) {
          return loadSessionProfile(activeSession()?.id || "");
        }
        renderSessions();
        els.sessionContextState.textContent = "No context saved for this session yet.";
        updateSessionContextControls(activeSession());
      }
      persistSessions();
      return;
    }
    if (state.activeSessionId === sessionId) {
      els.sessionContextState.textContent = `Unable to load context: ${detail}`;
      updateSessionContextControls(activeSession());
    }
  }
}

async function saveSessionContext() {
  const session = activeSession();
  const context = normalizeSessionContext(els.sessionContextInput.value);
  if (!context) {
    els.sessionContextState.textContent = "Session context is required.";
    updateQuerySelectionState();
    updateSessionContextControls(session);
    return false;
  }
  if (context.length > SESSION_CONTEXT_MAX) {
    els.sessionContextState.textContent = `Session context must be <= ${SESSION_CONTEXT_MAX} characters.`;
    updateSessionContextControls(session);
    return false;
  }
  const payload = {
    name: session.name,
    session_context: context,
  };
  try {
    const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const profile = result.data || {};
    session.sessionContext = String(profile.session_context || context);
    session.sessionContextUpdatedAt = String(profile.updated_at || new Date().toISOString());
    session.savedSessionContext = session.sessionContext;
    session.savedSessionContextUpdatedAt = session.sessionContextUpdatedAt;
    if (typeof profile.name === "string" && profile.name.trim()) {
      session.name = profile.name.trim();
    }
    persistSessions();
    renderSessions();
    els.sessionContextState.textContent = "Session context saved.";
    updateQuerySelectionState();
    updateSessionContextControls(session);
    return true;
  } catch (error) {
    els.sessionContextState.textContent = `Unable to save context: ${errorDetail(error)}`;
    updateSessionContextControls(session);
    return false;
  }
}

async function switchActiveSession(nextId) {
  if (!nextId || nextId === state.activeSessionId) {
    state.pendingSessionId = state.activeSessionId;
    renderSessions();
    return;
  }
  state.pendingSessionId = nextId;
  state.activeSessionId = nextId;
  resetReviewSort();
  persistSessions();
  renderSessions();
  resetSessionBoundPaneState();
  await refreshAll();
  els.sessionState.textContent = `Loaded session: ${activeSession().name}`;
  state.fileMenuOpen = false;
  state.showNewSessionForm = false;
  renderShell();
}

async function generateQuerySuggestions() {
  const session = activeSession();
  const context = normalizeSessionContext(session.sessionContext || els.sessionContextInput.value);
  if (!context) {
    state.suggestionStateSticky = false;
    els.discoverSuggestionsState.textContent = "Session context is required before generating suggestions.";
    updateSuggestionAvailability();
    return;
  }
  beginBusy("Generating query suggestions");
  try {
    const result = await api("/v1/discovery/query-suggestions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_context: context,
        existing_queries: session.queries.map((query) => query.text),
        max_suggestions: 10,
      }),
    });
    state.suggestedQueries = Array.isArray(result.data?.suggestions) ? result.data.suggestions : [];
    state.suggestionStateSticky = true;
    renderSuggestedQueries();
    els.discoverSuggestionsState.textContent = state.suggestedQueries.length
      ? `Generated ${state.suggestedQueries.length} suggestion(s). Click one to add it to the selected query list.`
      : "No new suggestions returned for this context.";
  } catch (error) {
    state.suggestionStateSticky = true;
    els.discoverSuggestionsState.textContent = querySuggestionErrorText(error);
  } finally {
    endBusy();
  }
}

function bindLatestIdsToSession() {
  const session = activeSession();
  if (!session.discoveryRunId && !session.resultsRunId && state.latest.discovery) {
    session.discoveryRunId = state.latest.discovery;
    session.resultsRunId = state.latest.discovery;
  }
}

async function loadLatestIds() {
  const result = await api("/v1/runs/latest");
  state.latest.discovery = result.data.discovery_run_id || "";
  state.latest.discoverySession = result.data.discovery_session_id || "";
  state.latest.acquisition = result.data.acquisition_run_id || "";
  state.latest.parse = result.data.parse_run_id || "";
  els.latestDiscoveryId.textContent = state.latest.discovery || "-";
  els.latestAcquisitionId.textContent = state.latest.acquisition || "-";
  els.latestParseId.textContent = state.latest.parse || "-";
  bindLatestIdsToSession();
  await loadCurrentParseStatus();
  updateParseControls();
  persistSessions();
}

async function loadCurrentParseStatus() {
  if (!state.latest.parse) {
    state.currentParseStatus = null;
    return;
  }
  try {
    const result = await api(`/v1/parse/runs/${encodeURIComponent(state.latest.parse)}`);
    state.currentParseStatus = result.data || null;
  } catch {
    state.currentParseStatus = null;
  }
}

function updateParseControls() {
  if (!els.startParseBtn || !els.advancedParseState) {
    return;
  }
  const sessionId = activeSession()?.id || "";
  const parseStatus = state.currentParseStatus;
  const parseStage = parseStatus?.stage_status || "";
  const parseActive = ["queued", "running"].includes(parseStage);
  els.startParseBtn.disabled = state.serverOffline || !sessionId;
  if (state.serverOffline) {
    if (canOverwriteAdvancedParseState()) {
      setAdvancedParseState("Parsing unavailable while the server is offline.");
    }
    return;
  }
  if (!sessionId) {
    if (canOverwriteAdvancedParseState()) {
      setAdvancedParseState("No active session is available yet. Discover and download documents first.");
    }
    return;
  }
  if (parseActive) {
    if (canOverwriteAdvancedParseState()) {
      setAdvancedParseState(parseStatus.message || `Parse ${parseStatus.parse_run_id} is already ${parseStage}.`);
    }
    return;
  }
  if (canOverwriteAdvancedParseState()) {
    setAdvancedParseState(`Ready to parse all downloaded documents for session ${sessionId}. Latest parse: ${state.latest.parse || "-"}.`);
  }
}

async function loadSystemStatus() {
  try {
    const result = await api("/v1/system/status");
    const data = result.data;
    state.systemStatus = data;
    els.authStatus.textContent = `Auth: ${data.auth_mode}`;
    els.aiStatus.textContent = `AI: ${data.ai_filter_active ? "ready" : "inactive"}`;
    state.databaseTargetWarning = String(data.db_target_warning || "");
    const dbTarget = String(data.database_target || data.db_target_resolved_path || data.db_target_url || "unknown");
    const dbLabel = dbTarget.split("/").pop();
    const dbDisplay = data.db_ready ? dbLabel : "not ready";
    els.dbStatus.textContent = `DB: ${dbDisplay}`;
    els.footerSystem.textContent = `System: ${data.auth_mode}`;
    els.footerAi.textContent = `AI: ${data.ai_filter_active ? "ready" : "inactive"}`;
    els.footerDb.textContent = `DB: ${dbDisplay}`;
    els.footerUpdated.textContent = `Last update: ${new Date().toLocaleTimeString()}`;
    if (els.databaseTargetWarning) {
      if (state.databaseTargetWarning) {
        els.databaseTargetWarning.textContent = `Warning: ${state.databaseTargetWarning}.`;
      } else if (data.db_target_kind) {
        els.databaseTargetWarning.textContent = `Active DB kind: ${data.db_target_kind}.`;
      } else {
        els.databaseTargetWarning.textContent = "DB target status unknown.";
      }
    }
    updateSuggestionAvailability();
    renderAdvancedDatabaseBackups();
  } catch {
    state.systemStatus = null;
    state.databaseTargetWarning = "";
    els.footerSystem.textContent = "System: error";
    els.footerDb.textContent = "DB: error";
    els.footerUpdated.textContent = "Last update: error";
    if (els.databaseTargetWarning) {
      els.databaseTargetWarning.textContent = "DB target status unknown.";
    }
    updateSuggestionAvailability();
  }
}

async function loadProviderSettings() {
  try {
    const result = await api("/v1/settings/providers");
    const data = result.data || {};
    els.openalexLimitInput.value = String(data.openalex_search_limit ?? 25);
    els.braveCountInput.value = String(data.brave_search_count ?? 20);
    els.braveAllowlistCheckbox.checked = Boolean(data.brave_require_allowlist);
    els.providerSettingsState.textContent = "Provider settings loaded.";
  } catch {
    els.providerSettingsState.textContent = "Unable to load provider settings.";
  }
}

function formatLink(source) {
  return source.doi_url || source.url || "";
}

function buildMetadataHtml(item) {
  const authors = Array.isArray(item.authors) && item.authors.length
    ? `${escapeHtml(item.authors.slice(0, 3).join(", "))}${item.authors.length > 3 ? ` +${item.authors.length - 3} more` : ""}`
    : "-";
  const link = formatLink(item);
  const lineage = formatLineageNumber(item);
  return [
    `<span>#: ${escapeHtml(lineage)}</span>`,
    `<span>Year: ${escapeHtml(item.year ?? "-")}</span>`,
    `<span>Journal: ${escapeHtml(item.journal || "-")}</span>`,
    `<span>Citations: ${escapeHtml(item.citation_count ?? "-")}</span>`,
    `<span>Authors: ${authors}</span>`,
    `<span>Link: ${link ? `<a class="linkish" href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">Open source</a>` : "-"}</span>`,
  ].join(" ");
}

function displayQueryStatus(status) {
  return status === "ranking_relevance" ? "ranking" : String(status || "waiting");
}

function providerStatusLabel(status) {
  const value = String(status || "pending");
  if (value === "ok") return "ok";
  if (value === "empty") return "0 results";
  if (value === "rate_limited") return "rate limited";
  if (value === "timeout") return "timeout";
  if (value === "failed") return "failed";
  if (value === "disabled") return "disabled";
  if (value === "skipped") return "skipped";
  if (value === "running") return "running";
  return "pending";
}

function providerSummaryLine(label, count, status, errorMessage) {
  const suffix = `[${providerStatusLabel(status)}]`;
  const detail = errorMessage ? ` - ${errorMessage}` : "";
  return `${label}: ${count ?? 0} ${suffix}${detail}`;
}

function blockedReasonText(reason) {
  const value = String(reason || "").trim();
  if (value === "parsed_text_required") {
    return "parsed text required";
  }
  if (value === "already_generated") {
    return "already generated";
  }
  if (value === "paper_not_annotatable_in_session") {
    return "paper not annotatable in session";
  }
  return value || "unknown reason";
}

function blockedSummaryText(blocked) {
  const rows = Array.isArray(blocked) ? blocked : [];
  if (!rows.length) {
    return "";
  }
  const counts = new Map();
  rows.forEach((item) => {
    const reason = blockedReasonText(item?.reason);
    counts.set(reason, Number(counts.get(reason) || 0) + 1);
  });
  return Array.from(counts.entries())
    .map(([reason, count]) => `${count} ${reason}`)
    .join(", ");
}

function activeDiscoverQueryTotals(session = activeSession()) {
  const runId = discoverRunId(session);
  const activeQueries = state.discoverRunQueries.filter((item) => item.run_id === runId);
  return activeQueries.reduce(
    (acc, item) => {
      acc.discovered += Number(item.discovered_count || 0);
      acc.accepted += Number(item.accepted_count || 0);
      acc.rejected += Number(item.rejected_count || 0);
      acc.pending += Number(item.pending_count || 0);
      return acc;
    },
    { discovered: 0, accepted: 0, rejected: 0, pending: 0 },
  );
}

function activeCitationScopeProgress(session = activeSession()) {
  const runId = discoverRunId(session);
  const citationRow = state.discoverRunQueries.find(
    (item) => item.run_id === runId && item.query === "citation expansion",
  );
  if (!citationRow) {
    return null;
  }
  return {
    processed: Number(citationRow.scope_processed_parents ?? 0),
    total: Number(citationRow.scope_total_parents ?? 0),
    checkpoint: citationRow.checkpoint_state || "none",
  };
}

function renderDiscoverRunQueries() {
  els.discoverRunQueries.innerHTML = "";
  if (!state.discoverRunQueries.length) {
    if (state.currentDiscoveryStatus?.run_id && Array.isArray(state.currentDiscoveryStatus.seed_queries) && state.currentDiscoveryStatus.seed_queries.length === 0) {
      els.discoverRunQueriesState.textContent = "This run was started from a bookmarked paper and has no text queries.";
    } else {
      els.discoverRunQueriesState.textContent = "No executed queries in the active session yet.";
    }
    return;
  }
  els.discoverRunQueriesState.textContent = `${state.discoverRunQueries.length} executed quer${state.discoverRunQueries.length === 1 ? "y" : "ies"} loaded for the active session.`;
  state.discoverRunQueries.forEach((item) => {
    const tr = document.createElement("tr");
    const providers = [
      providerSummaryLine("OpenAlex", item.openalex_count, item.openalex_status, item.openalex_error_message),
      providerSummaryLine("Brave", item.brave_count, item.brave_status, item.brave_error_message),
      providerSummaryLine(
        "Semantic Scholar",
        item.semantic_scholar_count,
        item.semantic_scholar_status,
        item.semantic_scholar_error_message,
      ),
    ].join(" | ");
    const reviewCounts = [
      `Accepted: ${item.accepted_count ?? 0}`,
      `Rejected: ${item.rejected_count ?? 0}`,
      `Pending: ${item.pending_count ?? 0}`,
      `Processing: ${item.processing_count ?? 0}`,
    ].join(" | ");
    const scopeProgress = item.query === "citation expansion"
      ? `Parents: ${item.scope_processed_parents ?? 0}/${item.scope_total_parents ?? 0} (${item.checkpoint_state || "none"})`
      : "";
    const displayStatus = displayQueryStatus(item.status);
    const countText = String(item.discovered_count ?? 0);
    const lineageLabel = item.query_lineage_number || "-";
    tr.innerHTML = `
      <td>${escapeHtml(lineageLabel)}</td>
      <td>${escapeHtml(item.query)}</td>
      <td><span class="status-chip ${escapeHtml(item.status)}">${escapeHtml(displayStatus)}</span></td>
      <td>${escapeHtml(providers)}</td>
      <td>${escapeHtml(reviewCounts)}${scopeProgress ? `<div class="muted">${escapeHtml(scopeProgress)}</div>` : ""}</td>
      <td>${countText}</td>
    `;
    els.discoverRunQueries.appendChild(tr);
  });
}

function updateCitationAvailability(acceptedCount, remainingParentCount = 0) {
  const session = activeSession();
  const runId = session?.discoveryRunId || "";
  const activeRunQueries = state.discoverRunQueries.filter((item) => item.run_id === runId);
  const hasResumableCitation = activeRunQueries.some(
    (item) => item.query === "citation expansion" && item.checkpoint_state === "resumable",
  );
  const disabled = state.serverOffline || !runId || acceptedCount <= 0 || remainingParentCount <= 0 || hasResumableCitation;
  els.runNextCitationBtn.disabled = disabled;
  if (!runId) {
    els.discoverCitationHint.textContent = "Run discovery before starting citation expansion.";
    return;
  }
  if (acceptedCount <= 0) {
    els.discoverCitationHint.textContent = "Need at least 1 accepted paper before running citation expansion.";
    return;
  }
  if (state.serverOffline) {
    els.discoverCitationHint.textContent = "Server is offline. Citation expansion is unavailable.";
    return;
  }
  if (hasResumableCitation) {
    els.discoverCitationHint.textContent = "A stopped citation expansion can be resumed. Use Resume Citation Expansion.";
    return;
  }
  els.discoverCitationHint.textContent = disabled
    ? "No accepted papers are newly eligible for citation expansion in the current context."
    : `Citation expansion is available for ${remainingParentCount} accepted paper${remainingParentCount === 1 ? "" : "s"} in the current context.`;
}

function citationIterationErrorText(error) {
  const detail = errorDetail(error);
  if (detail.includes("run_not_found")) {
    return "The bound discovery run no longer exists. Refresh session state.";
  }
  if (detail.includes("run_already_running")) {
    return "Citation expansion cannot start while the discovery run is already running.";
  }
  if (detail.includes("citation_iteration_resumable_exists_use_resume")) {
    return "A stopped citation expansion already exists. Use Resume Citation Expansion.";
  }
  if (detail.includes("Need at least 1 accepted paper before running citation expansion.")) {
    return "Need at least 1 accepted paper before running citation expansion.";
  }
  if (detail.includes("No new accepted papers are available for citation expansion.")) {
    return "No new accepted papers are available for citation expansion.";
  }
  return detail || "Unable to start citation expansion.";
}

async function loadDiscover(recoverOnNotFound = true) {
  const session = activeSession();
  renderSessions();
  state.currentDiscoveryStatus = null;
  state.discoverRunQueries = [];
  if (!session.discoveryRunId) {
    els.discoverIterationLine.textContent = "Run: -";
    ["discoverSummaryDiscovered", "discoverSummaryApproved", "discoverSummaryRejected", "discoverSummaryReviewed", "discoverSummaryPending"].forEach((id) => {
      els[id].textContent = "0";
    });
    renderDiscoverRunQueries();
    updateCitationAvailability(0, 0);
    els.resumeCitationBtn.disabled = true;
    return;
  }
  let runResult;
  let queryResult;
  try {
    [runResult, queryResult] = await Promise.all([
      api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}`),
      api(`/v1/sessions/${encodeURIComponent(session.id)}/queries`),
    ]);
  } catch (error) {
    if (recoverOnNotFound && isRunNotFoundError(error)) {
      const rebound = await rebindSessionToLatestRun("Saved discovery run was not found.");
      if (rebound) {
        return loadDiscover(false);
      }
    }
    throw error;
  }
  const run = runResult.data;
  state.currentDiscoveryStatus = run;
  state.discoverRunQueries = queryResult.data.queries || [];
  renderDiscoverRunQueries();
  const activeRunQueries = state.discoverRunQueries.filter((item) => item.run_id === session.discoveryRunId);
  const hasResumableCitation = activeRunQueries.some(
    (item) => item.query === "citation expansion" && item.checkpoint_state === "resumable",
  );
  els.resumeCitationBtn.disabled = !hasResumableCitation;
  const liveTotals = activeDiscoverQueryTotals(session);
  const citationScope = activeCitationScopeProgress(session);
  const liveDiscovered = liveTotals.discovered;
  const liveApproved = liveTotals.accepted;
  const liveRejected = liveTotals.rejected;
  const livePending = liveTotals.pending;
  const liveReviewed = liveApproved + liveRejected;
  const activeRunNumber = activeRunQueries[0]?.run_number ?? "-";
  els.discoverIterationLine.textContent = `Run: ${activeRunNumber}`;
  els.discoverSummaryDiscovered.textContent = String(liveDiscovered);
  els.discoverSummaryApproved.textContent = String(liveApproved);
  els.discoverSummaryRejected.textContent = String(liveRejected);
  els.discoverSummaryReviewed.textContent = String(liveReviewed);
  els.discoverSummaryPending.textContent = String(livePending);
  if (run.stage_status === "running" && citationScope && citationScope.total > 0) {
    els.discoverState.textContent = `Citation expansion running. Found so far: ${liveDiscovered}. Parents processed: ${citationScope.processed}/${citationScope.total}.`;
  } else if (run.stage_status === "running" && resultsRunId(session) && resultsRunId(session) !== discoverRunId(session)) {
    session.resultsRunId = discoverRunId(session);
    persistSessions();
    els.discoverState.textContent = `New discovery run is in progress. Found so far: ${liveDiscovered}. Review/Documents/Library are updating live.`;
  } else if (run.stage_status === "running") {
    els.discoverState.textContent = `Searching. Found so far: ${liveDiscovered}.`;
  } else if (run.status === "completed" && discoverRunId(session) && resultsRunId(session) !== discoverRunId(session)) {
    session.resultsRunId = discoverRunId(session);
    persistSessions();
    els.discoverState.textContent = "New discovery run completed. Session-level results were refreshed.";
  } else {
    els.discoverState.textContent = run.message;
  }
  if (run.stage_status === "running") {
    startDiscoverPolling();
  } else {
    stopDiscoverPolling();
  }
  updateCitationAvailability(liveApproved, Number(run.citation_unexpanded_parent_count ?? 0));
  renderActivity();
}

function reviewSignalText(item) {
  return [
    `Decision: ${item.final_decision}`,
    `Decision source: ${item.decision_source}`,
    `Heuristic recommendation: ${item.heuristic_recommendation}`,
    `Heuristic score: ${Number(item.heuristic_score || 0).toFixed(2)}`,
  ].join(" | ");
}

function renderReviewDetail() {
  const item = state.reviewItems[state.reviewIndex];
  if (!item) {
    els.reviewTitle.textContent = "No source selected.";
    els.reviewAbstract.textContent = "Select a paper to review.";
    els.reviewMetadata.innerHTML = "Year: - | Journal: - | Citations: - | Authors: - | Link: -";
    els.reviewSignals.textContent = "No AI signals available.";
    if (els.reviewAcceptBtn) {
      els.reviewAcceptBtn.disabled = true;
    }
    if (els.reviewRejectBtn) {
      els.reviewRejectBtn.disabled = true;
    }
    if (els.reviewLaterBtn) {
      els.reviewLaterBtn.disabled = true;
    }
    if (els.reviewCopyAbstractBtn) {
      els.reviewCopyAbstractBtn.disabled = true;
    }
    if (els.reviewBookmarkBtn) {
      els.reviewBookmarkBtn.textContent = "Bookmark";
      els.reviewBookmarkBtn.disabled = true;
    }
    return;
  }
  els.reviewTitle.textContent = `${isBookmarked(item.id) ? "[Bookmarked] " : ""}${item.title}`;
  els.reviewAbstract.textContent = item.abstract || "No abstract available.";
  els.reviewMetadata.innerHTML = buildMetadataHtml(item);
  els.reviewSignals.textContent = reviewSignalText(item);
  if (els.reviewAcceptBtn) {
    els.reviewAcceptBtn.disabled = false;
  }
  if (els.reviewRejectBtn) {
    els.reviewRejectBtn.disabled = false;
  }
  if (els.reviewLaterBtn) {
    els.reviewLaterBtn.disabled = false;
  }
  if (els.reviewCopyAbstractBtn) {
    els.reviewCopyAbstractBtn.disabled = !String(item.abstract || "").trim();
  }
  if (els.reviewBookmarkBtn) {
    els.reviewBookmarkBtn.textContent = isBookmarked(item.id) ? "Remove Bookmark" : "Bookmark";
    els.reviewBookmarkBtn.disabled = false;
  }
}

function reviewHeadingForQueue(queue, count) {
  const labels = {
    pending: "Pending",
    accepted: "Accepted",
    rejected: "Rejected",
    later: "Later",
    all: "All",
    latest_auto_approved: "Latest Auto-Approved",
    latest_auto_rejected: "Latest Auto-Rejected",
  };
  const label = labels[queue] || queue;
  return `${label}: ${count}`;
}

function renderReviewRows() {
  els.reviewRows.innerHTML = "";
  const sortedItems = [...state.reviewItems].sort(compareReviewItems);
  state.reviewItems = sortedItems;
  state.reviewItems.forEach((item, index) => {
    const tr = document.createElement("tr");
    tr.classList.toggle("active", index === state.reviewIndex);
    const docCell = renderDocumentBadgeCell(item);
    tr.innerHTML = `<td>${escapeHtml(item.review_status || "-")}</td><td class="col-lineage">${escapeHtml(formatLineageNumber(item))}</td><td class="col-year">${item.year || "-"}</td><td class="col-citations">${item.citation_count ?? "-"}</td><td class="col-score">${Number(item.relevance_score || 0).toFixed(2)}</td><td>${isBookmarked(item.id) ? '<span class="bookmark-chip">B</span> ' : ""}${escapeHtml(item.title)}</td><td class="col-doc">${docCell}</td>`;
    tr.addEventListener("click", () => {
      state.reviewIndex = index;
      state.selectedReviewSourceId = item.id;
      renderReviewRows();
      renderReviewDetail();
    });
    els.reviewRows.appendChild(tr);
  });
  if (state.selectedReviewSourceId) {
    const selectedIndex = state.reviewItems.findIndex((item) => item.id === state.selectedReviewSourceId);
    state.reviewIndex = selectedIndex >= 0 ? selectedIndex : (state.reviewItems.length ? 0 : -1);
  } else {
    state.reviewIndex = state.reviewItems.length ? 0 : -1;
  }
  els.reviewHeading.textContent = reviewHeadingForQueue(state.reviewQueue, state.reviewItems.length);
  els.reviewBadge.textContent = String(state.reviewItems.length);
  renderReviewSortButtons();
  renderReviewDetail();
  applyOfflineActionState();
}

function nextReviewSelectionHint() {
  if (!state.reviewItems.length || state.reviewIndex < 0) {
    return { preferredSourceId: "", fallbackIndex: 0 };
  }
  const nextItem = state.reviewItems[state.reviewIndex + 1] || null;
  if (nextItem) {
    return {
      preferredSourceId: nextItem.id,
      fallbackIndex: state.reviewIndex,
    };
  }
  return {
    preferredSourceId: "",
    fallbackIndex: Math.max(0, state.reviewIndex - 1),
  };
}

function reviewQueueRetainsDecision(queue, decision) {
  if (queue === "all") {
    return true;
  }
  if (decision === "accept") {
    return queue === "accepted";
  }
  if (decision === "reject") {
    return queue === "rejected";
  }
  if (decision === "later") {
    return queue === "later";
  }
  return false;
}

async function loadReview(recoverOnNotFound = true, selectionHint = null) {
  if (state.reviewLoadInFlight) {
    state.reviewReloadQueued = true;
    return;
  }
  state.reviewLoadInFlight = true;
  try {
  const session = activeSession();
  const queue = state.reviewQueue || "pending";
  const status = REVIEW_STATUS_TO_API[queue] || "needs_review";
  const runId = resultsRunId(session);
  if (!session.id || (queue.startsWith("latest_auto_") && !runId)) {
    state.reviewItems = [];
    state.reviewIndex = -1;
    renderReviewRows();
    els.reviewState.textContent = "No discovery results attached to the active session.";
    return;
  }
  let result;
  try {
    const path = queue.startsWith("latest_auto_")
      ? `/v1/discovery/runs/${encodeURIComponent(runId)}/sources?status=${encodeURIComponent(status)}&limit=${REVIEW_FETCH_LIMIT}`
      : sessionSourcesPath(session.id, status, REVIEW_FETCH_LIMIT);
    result = await api(path);
  } catch (error) {
    if (recoverOnNotFound && isRunNotFoundError(error)) {
      const rebound = await rebindSessionToLatestRun("Saved discovery run was not found.");
      if (rebound) {
        return loadReview(false, selectionHint);
      }
    }
    throw error;
  }
  state.reviewItems = result.data.items || [];
  if (selectionHint?.preferredSourceId && state.reviewItems.some((item) => item.id === selectionHint.preferredSourceId)) {
    state.selectedReviewSourceId = selectionHint.preferredSourceId;
  } else if (!state.reviewItems.some((item) => item.id === state.selectedReviewSourceId)) {
    const fallbackIndex = Math.min(
      Math.max(Number(selectionHint?.fallbackIndex ?? 0), 0),
      Math.max(state.reviewItems.length - 1, 0),
    );
    state.selectedReviewSourceId = state.reviewItems[fallbackIndex]?.id || "";
  }
  renderReviewFilterChips();
  renderReviewRows();
  els.reviewState.textContent = state.reviewItems.length ? `Review queue loaded (${queue}).` : `No ${queue} review items.`;
  } finally {
    state.reviewLoadInFlight = false;
    if (state.reviewReloadQueued) {
      state.reviewReloadQueued = false;
      window.setTimeout(() => {
        loadReview().catch((error) => {
          els.reviewState.textContent = `Unable to load review queue: ${errorDetail(error)}`;
        });
      }, 0);
    }
  }
}

async function submitReviewDecision(decision) {
  const item = state.reviewItems[state.reviewIndex];
  if (!item) {
    return;
  }
  const queue = state.reviewQueue || "pending";
  const selectionHint = reviewQueueRetainsDecision(queue, decision) ? null : nextReviewSelectionHint();
  const reviewedTitle = item.title;
  const decisionLabel = decision === "accept" ? "Accepted" : decision === "reject" ? "Rejected" : "Saved for later";
  beginBusy("Waiting for review");
  try {
    await api(`/v1/sources/${encodeURIComponent(item.id)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    await loadReview(true, selectionHint);
    await loadDiscover();
    els.reviewState.textContent = `${decisionLabel}: ${reviewedTitle}`;
  } finally {
    endBusy();
  }
}

function normalizeDocumentRows(acceptedSources, itemsMap) {
  return acceptedSources.map((source, index) => {
    const item = itemsMap.get(source.id);
    const status = item?.status || "pending";
    return {
      rank: index + 1,
      lineage: source.lineage_number || "-",
      source,
      previewablePdfArtifactId: source.previewable_pdf_artifact_id || null,
      acquisitionItem: item || null,
      artifactKind: item?.artifact_kind || null,
      artifactMimeType: item?.artifact_mime_type || null,
      artifactQualityStatus: item?.artifact_quality_status || null,
      artifactQualityReason: item?.artifact_quality_reason || null,
      status,
      parseScopeStatus: item?.parse_scope_status || (isParsedReady(source.id) ? "parsed" : status === "pending" ? "no_artifact" : "unparsed_in_active_session"),
      parseStatusDetail: item?.parse_status_detail || null,
      title: source.title,
      year: source.year || "-",
      score: Number(source.relevance_score || 0).toFixed(2),
      citations: source.citation_count ?? "-",
    };
  });
}

function documentParsePresentation(row) {
  const value = row?.parseScopeStatus || "no_artifact";
  if (value === "parsed") {
    return { label: "Parsed", className: "parsed" };
  }
  if (value === "parse_running") {
    return { label: "Parsing", className: "running" };
  }
  if (value === "parse_failed") {
    return { label: "Failed", className: "failed" };
  }
  if (value === "unparsed_other_session") {
    return { label: "Other session", className: "other-session" };
  }
  if (value === "unparsed_in_active_session") {
    return { label: "Unparsed", className: "waiting" };
  }
  return { label: "No artifact", className: "no-artifact" };
}

function documentBadgeState(row) {
  const sourceId = row?.source?.id || row?.source_id || row?.id || "";
  const annotation = sourceId ? annotationForSource(sourceId) : null;
  const currentPrompt = String(state.sessionSummaryPrompt || DEFAULT_SUMMARY_PROMPT).trim();
  const snapshotPrompt = String(annotation?.summary_prompt_snapshot || "").trim();
  const summaryUpToDate = Boolean(
    annotation
    && annotation.summary_status === "completed"
    && annotation.ai_summary
    && snapshotPrompt
    && snapshotPrompt === currentPrompt,
  );
  return {
    previewablePdfArtifactId: row?.previewablePdfArtifactId || row?.previewable_pdf_artifact_id || null,
    parseScopeStatus: row?.parseScopeStatus || row?.parse_scope_status || null,
    artifactQualityStatus: row?.artifactQualityStatus || row?.artifact_quality_status || null,
    summaryUpToDate,
  };
}

function renderDocumentBadgeCell(row) {
  const badgeState = documentBadgeState(row);
  const badges = [];
  if (badgeState.previewablePdfArtifactId) {
    badges.push('<span class="doc-badge pdf" title="PDF available" aria-label="PDF available">P</span>');
  }
  if (badgeState.parseScopeStatus === "parsed") {
    badges.push('<span class="doc-badge parsed" title="Parsed text available" aria-label="Parsed text available">R</span>');
  } else if (badgeState.parseScopeStatus === "parse_running") {
    badges.push('<span class="doc-badge running" title="Parsing in progress" aria-label="Parsing in progress">G</span>');
  } else if (badgeState.parseScopeStatus === "parse_failed") {
    badges.push('<span class="doc-badge failed" title="Parsing failed" aria-label="Parsing failed">F</span>');
  } else if (badgeState.parseScopeStatus === "unparsed_in_active_session") {
    badges.push('<span class="doc-badge waiting" title="Downloaded but not parsed yet" aria-label="Downloaded but not parsed yet">U</span>');
  }
  if (badgeState.artifactQualityStatus === "html_invalid") {
    badges.push('<span class="doc-badge bad-html" title="Bad HTML content" aria-label="Bad HTML content">H</span>');
  }
  if (badgeState.summaryUpToDate) {
    badges.push('<span class="doc-badge summary-current" title="Summary available and up to date for the current prompt" aria-label="Summary available and up to date for the current prompt">S</span>');
  }
  if (!badges.length) {
    return '<span class="doc-badge-placeholder" title="No document badges">-</span>';
  }
  return `<div class="doc-badge-stack">${badges.join("")}</div>`;
}

function selectedDocumentRow() {
  return state.documentRows.find((row) => row.source.id === state.selectedDocumentSourceId) || null;
}

function renderDocumentsDetail() {
  const row = selectedDocumentRow();
  if (!row) {
    els.documentsDetailTitle.textContent = "No document selected.";
    els.documentsDetailSummary.textContent = "Select a document row to inspect status, links, and available actions.";
    els.documentsDetailMetadata.innerHTML = "Year: - | Journal: - | Citations: - | Authors: - | Link: -";
    els.documentsRowActionBtn.disabled = true;
    if (els.documentsOpenPdfBtn) {
      els.documentsOpenPdfBtn.disabled = true;
    }
    els.documentsRowActionBtn.textContent = "Download Selected";
    if (els.documentsBookmarkBtn) {
      els.documentsBookmarkBtn.textContent = "Bookmark";
      els.documentsBookmarkBtn.disabled = true;
    }
    return;
  }
  els.documentsDetailTitle.textContent = `${isBookmarked(row.source.id) ? "[Bookmarked] " : ""}${row.title}`;
  const parsePresentation = documentParsePresentation(row);
  const detailBits = [`Status: ${row.status}`, `Parse: ${parsePresentation.label}`];
  if (row.artifactKind || row.artifactQualityStatus) {
    const artifactLabel = row.artifactQualityStatus === "pdf"
      ? "PDF"
      : row.artifactQualityStatus === "html_validated"
        ? "HTML full text"
        : row.artifactQualityStatus === "html_invalid"
          ? "Bad HTML"
          : row.artifactKind || "Unknown";
    detailBits.push(`Content: ${artifactLabel}`);
  }
  if (row.acquisitionItem?.acq_run_id) {
    detailBits.push(`Acquisition: ${row.acquisitionItem.acq_run_id}`);
  }
  if (row.acquisitionItem?.artifact_source_session_id && row.acquisitionItem.artifact_source_session_id !== activeSession()?.id) {
    detailBits.push(`Artifact session: ${row.acquisitionItem.artifact_source_session_id}`);
  }
  if (row.acquisitionItem?.parse_run_id) {
    detailBits.push(`Parse run: ${row.acquisitionItem.parse_run_id}`);
  }
  if (row.parseStatusDetail) {
    detailBits.push(`Parse detail: ${row.parseStatusDetail}`);
  }
  if (row.artifactQualityReason) {
    detailBits.push(`Artifact detail: ${row.artifactQualityReason}`);
  }
  if (row.acquisitionItem?.last_error) {
    detailBits.push(`Last error: ${row.acquisitionItem.last_error}`);
  }
  els.documentsDetailSummary.textContent = detailBits.join(" | ");
  els.documentsDetailMetadata.innerHTML = buildMetadataHtml(row.source);
  els.documentsRowActionBtn.disabled = false;
  if (els.documentsOpenPdfBtn) {
    els.documentsOpenPdfBtn.disabled = !row.previewablePdfArtifactId;
  }
  if (row.status === "pending") {
    els.documentsRowActionBtn.textContent = "Download Selected";
  } else if (row.status === "failed" || row.status === "partial") {
    els.documentsRowActionBtn.textContent = "Retry Selected";
  } else if (row.previewablePdfArtifactId) {
    els.documentsRowActionBtn.textContent = "Open PDF";
  } else {
    els.documentsRowActionBtn.textContent = "Open Source";
  }
  if (els.documentsBookmarkBtn) {
    els.documentsBookmarkBtn.textContent = isBookmarked(row.source.id) ? "Remove Bookmark" : "Bookmark";
    els.documentsBookmarkBtn.disabled = false;
  }
  applyOfflineActionState();
}

function renderDocuments() {
  renderDocumentsSortButtons();
  els.documentsRows.innerHTML = "";
  const sortedRows = sortedDocumentRows();
  const maxPage = Math.max(Math.ceil(sortedRows.length / DOCUMENTS_PAGE_SIZE) - 1, 0);
  if (state.documentsPage > maxPage) {
    state.documentsPage = maxPage;
  }
  pagedRows(sortedRows, state.documentsPage, DOCUMENTS_PAGE_SIZE).forEach((row) => {
    const tr = document.createElement("tr");
    tr.classList.toggle("active", row.source.id === state.selectedDocumentSourceId);
    const doiCell = row.source.doi_url
      ? `<a href="${escapeHtml(row.source.doi_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(row.source.doi || "Open DOI")}</a>`
      : "-";
    const docCell = renderDocumentBadgeCell(row);
    tr.innerHTML = `<td class="col-lineage">${escapeHtml(row.lineage)}</td><td class="col-score">${row.score}</td><td class="col-year">${row.year}</td><td class="col-citations">${row.citations}</td><td><div class="title-cell">${isBookmarked(row.source.id) ? '<span class="bookmark-chip">B</span>' : ""}<span class="title-cell-text">${escapeHtml(row.title)}</span><button type="button" class="mini-copy-btn title-cell-copy" title="Copy title" aria-label="Copy title">Copy</button></div></td><td class="col-doc">${docCell}</td><td>${doiCell}</td><td>${row.status}</td>`;
    tr.querySelector(".mini-copy-btn")?.addEventListener("click", async (event) => {
      event.stopPropagation();
      const ok = await copyTextToClipboard(row.title);
      els.documentsState.textContent = ok ? `Copied title: ${row.title}` : "Unable to copy title.";
    });
    tr.addEventListener("click", () => {
      state.selectedDocumentSourceId = row.source.id;
      renderDocuments();
    });
    els.documentsRows.appendChild(tr);
  });
  renderDocumentsPager(sortedRows.length);
  renderDocumentsDetail();
}

async function loadDocuments(recoverOnNotFound = true) {
  const session = activeSession();
  state.currentAcquisitionStatus = null;
  if (!session.id) {
    state.documentRows = [];
    state.selectedDocumentSourceId = "";
    els.documentsDownloaded.textContent = "0";
    els.documentsFailed.textContent = "0";
    els.documentsManual.textContent = "0";
    els.documentsPending.textContent = "0";
    els.documentsBadge.textContent = "0";
    els.documentsState.textContent = "No discovery results attached to the active session.";
    renderDocuments();
    return;
  }
  let acceptedResult;
  try {
    const acceptedItems = await fetchAllPages(async (offset, limit) => {
      const response = await api(sessionSourcesPath(session.id, "accepted", limit, offset));
      return response.data || {};
    }, API_FETCH_PAGE_SIZE);
    acceptedResult = { data: { items: acceptedItems } };
  } catch (error) {
    if (recoverOnNotFound && isRunNotFoundError(error)) {
      const rebound = await rebindSessionToLatestRun("Saved discovery run was not found.");
      if (rebound) {
        return loadDocuments(false);
      }
    }
    throw error;
  }
  const accepted = acceptedResult.data.items || [];
  let latestItems = [];
  let statusData = null;
  let items = [];
  if (session.acquisitionRunId) {
    try {
      statusData = (await api(`/v1/acquisition/runs/${encodeURIComponent(session.acquisitionRunId)}`)).data;
      items = await fetchAllPages(async (offset, limit) => {
        const response = await api(
          `/v1/acquisition/runs/${encodeURIComponent(session.acquisitionRunId)}/items?limit=${limit}&offset=${offset}`,
        );
        return response.data || {};
      }, ACQUISITION_FETCH_PAGE_SIZE);
    } catch {
      session.acquisitionRunId = "";
      persistSessions();
    }
  }
  try {
    latestItems = await fetchAllPages(async (offset, limit) => {
      const response = await api(
        `/v1/sessions/${encodeURIComponent(session.id)}/acquisition-items/latest?limit=${limit}&offset=${offset}`,
      );
      return response.data || {};
    }, ACQUISITION_FETCH_PAGE_SIZE);
  } catch {
    latestItems = items;
  }
  state.currentAcquisitionStatus = statusData;
  const itemMap = new Map(latestItems.map((item) => [item.source_id, item]));
  state.documentRows = normalizeDocumentRows(accepted, itemMap);
  await loadLibraryAnnotations(session.id, state.documentRows.map((row) => row.source.id));
  state.documentsPage = 0;
  if (!state.documentRows.some((row) => row.source.id === state.selectedDocumentSourceId)) {
    state.selectedDocumentSourceId = state.documentRows[0]?.source.id || "";
  }
  renderDocuments();
  els.documentsDownloaded.textContent = String(state.documentRows.filter((row) => row.status === "downloaded").length);
  els.documentsFailed.textContent = String(state.documentRows.filter((row) => row.status === "failed").length);
  els.documentsManual.textContent = String(state.documentRows.filter((row) => row.status === "partial" || row.status === "skipped").length);
  els.documentsPending.textContent = String(state.documentRows.filter((row) => row.status === "pending").length);
  els.documentsBadge.textContent = String(state.documentRows.filter((row) => row.status === "failed" || row.status === "partial").length);
  els.documentsState.textContent = statusData ? statusData.message : `${accepted.length} accepted source(s) ready for acquisition.`;
  renderActivity();
}

function selectedLibraryIds() {
  const session = activeSession();
  if (session.exportSourceIds.length) {
    return session.exportSourceIds;
  }
  const limit = Number(els.libraryExportSize.value || 20);
  return state.libraryFilteredRows.slice(0, limit).map((row) => row.id);
}

async function loadLibraryAnnotations(sessionId, sourceIds) {
  const ids = uniqueIds(sourceIds);
  if (!ids.length) {
    return;
  }
  const params = new URLSearchParams();
  params.set("limit", String(ids.length));
  ids.forEach((sourceId) => params.append("source_id", sourceId));
  const result = await api(`/v1/sessions/${encodeURIComponent(sessionId)}/annotations?${params.toString()}`);
  const items = Array.isArray(result.data?.items) ? result.data.items : [];
  state.paperAnnotations = {
    ...state.paperAnnotations,
    ...Object.fromEntries(items.map((item) => [item.source_id, item])),
  };
}

async function loadLibraryTagCatalog(sessionId) {
  const result = await api(`/v1/sessions/${encodeURIComponent(sessionId)}/tag-catalog`);
  state.sessionApprovedTags = Array.isArray(result.data?.tags) ? result.data.tags : [];
  if (els.libraryApprovedTagsInput) {
    els.libraryApprovedTagsInput.value = state.sessionApprovedTags.join("\n");
  }
  if (els.libraryApprovedTagsState) {
    els.libraryApprovedTagsState.textContent = state.sessionApprovedTags.length
      ? `${state.sessionApprovedTags.length} approved tag${state.sessionApprovedTags.length === 1 ? "" : "s"} configured for this session.`
      : "No approved tags configured for this session.";
  }
}

async function loadLibraryTagReview(sessionId) {
  const result = await api(`/v1/sessions/${encodeURIComponent(sessionId)}/tag-review`);
  state.sessionTagReview = result.data || {
    session_id: sessionId,
    candidate_generation_status: "none",
    tag_assignment_status: "none",
    pending_count: 0,
    approved_count: 0,
    rejected_count: 0,
    candidates: [],
  };
}

async function loadLibraryTagSpec(sessionId) {
  const result = await api(`/v1/sessions/${encodeURIComponent(sessionId)}/tag-spec`);
  state.sessionTagSpec = result.data || {
    session_id: sessionId,
    category_config: cloneTagSpecConfig(),
    prompt_template: "",
  };
  if (els.libraryTagPromptInput) {
    els.libraryTagPromptInput.value = String(state.sessionTagSpec.prompt_template || "");
  }
  if (els.libraryRawTagPromptWrap) {
    els.libraryRawTagPromptWrap.hidden = !state.tagPromptRawMode;
  }
  if (els.libraryToggleRawTagPromptBtn) {
    els.libraryToggleRawTagPromptBtn.textContent = state.tagPromptRawMode ? "Hide Generated Prompt" : "Show Generated Prompt";
  }
  if (els.libraryTagSpecState) {
    const count = Array.isArray(state.sessionTagSpec?.category_config?.categories)
      ? state.sessionTagSpec.category_config.categories.length
      : 0;
    els.libraryTagSpecState.textContent = count ? `${count} tag categories configured.` : "Using the default tag spec.";
  }
  renderTagSpecEditor();
}

async function loadLibrarySummarySettings(sessionId) {
  const result = await api(`/v1/sessions/${encodeURIComponent(sessionId)}/summary-settings`);
  state.sessionSummaryPrompt = String(result.data?.prompt_template || "");
  state.sessionSummaryEditorConfig = cloneSummaryEditorConfig(result.data?.editor_config || defaultSummaryEditorConfig());
  state.currentGlobalSummaryModel = String(result.data?.current_global_summary_model || state.aiSettings?.ai_model || "");
  if (els.librarySummaryPromptInput) {
    els.librarySummaryPromptInput.value = state.sessionSummaryPrompt;
  }
  if (els.librarySummaryFocusInput) {
    els.librarySummaryFocusInput.value = String(state.sessionSummaryEditorConfig?.summary_focus || "");
  }
  updateSummaryPromptBuilderView();
  if (els.libraryCurrentSummaryModel) {
    els.libraryCurrentSummaryModel.textContent = state.currentGlobalSummaryModel || "-";
  }
  if (els.librarySummaryCurrentModelValue) {
    els.librarySummaryCurrentModelValue.textContent = state.currentGlobalSummaryModel || "-";
  }
  if (els.libraryRawSummaryPromptWrap) {
    els.libraryRawSummaryPromptWrap.hidden = !state.summaryPromptRawMode;
  }
  if (els.libraryToggleRawSummaryPromptBtn) {
    els.libraryToggleRawSummaryPromptBtn.textContent = state.summaryPromptRawMode ? "Hide Generated Prompt" : "Show Generated Prompt";
  }
  if (els.librarySummaryPromptState) {
    els.librarySummaryPromptState.textContent = state.sessionSummaryPrompt
      ? "Session summary prompt loaded."
      : "Using the default summary prompt.";
  }
}

function formatStructuredSummary(summaryJson) {
  if (!summaryJson || typeof summaryJson !== "object") {
    return '<div class="structured-summary-empty">No structured summary data available for this paper yet.</div>';
  }
  const humanizeKey = (key) => String(key || "").replace(/_/g, " ").replace(/\b\w/g, (match) => match.toUpperCase());
  const renderScalar = (value) => {
    if (value === null || value === undefined || value === "") {
      return '<span class="muted">-</span>';
    }
    if (typeof value === "boolean") {
      return value ? "true" : "false";
    }
    return escapeHtml(String(value));
  };
  const renderNode = (key, value, depth = 0) => {
    const label = humanizeKey(key);
    if (Array.isArray(value)) {
      if (!value.length) {
        return `<div class="structured-summary-field"><span class="structured-summary-label">${escapeHtml(label)}:</span> <span class="muted">-</span></div>`;
      }
      const items = value.map((item) => {
        if (item && typeof item === "object" && !Array.isArray(item)) {
          const objectParts = Object.entries(item).map(([childKey, childValue]) => {
            return `<span><span class="structured-summary-label">${escapeHtml(humanizeKey(childKey))}:</span> ${renderScalar(childValue)}</span>`;
          });
          return `<li>${objectParts.join(" | ")}</li>`;
        }
        return `<li>${renderScalar(item)}</li>`;
      }).join("");
      return `<div class="structured-summary-field"><span class="structured-summary-label">${escapeHtml(label)}:</span></div><ul class="structured-summary-list">${items}</ul>`;
    }
    if (value && typeof value === "object") {
      const children = Object.entries(value).map(([childKey, childValue]) => renderNode(childKey, childValue, depth + 1)).join("");
      return `<section class="structured-summary-section"><div class="structured-summary-heading">${escapeHtml(label)}</div>${children || '<div class="structured-summary-field"><span class="muted">-</span></div>'}</section>`;
    }
    if (depth === 0 && key === "summary") {
      return `<section class="structured-summary-section"><div class="structured-summary-heading">${escapeHtml(label)}</div><div class="structured-summary-field">${renderScalar(value)}</div></section>`;
    }
    return `<div class="structured-summary-field"><span class="structured-summary-label">${escapeHtml(label)}:</span> ${renderScalar(value)}</div>`;
  };
  return Object.entries(summaryJson).map(([key, value]) => renderNode(key, value, 0)).join("");
}

function renderTagList(container, tags, kind, onRemove) {
  if (!container) {
    return;
  }
  container.innerHTML = "";
  if (!tags.length) {
    container.innerHTML = `<span class="muted">No ${kind} tags.</span>`;
    return;
  }
  tags.forEach((tag) => {
    const chip = document.createElement("span");
    chip.className = `tag-chip${kind === "approved" ? " approved" : ""}`;
    chip.innerHTML = `<span>${escapeHtml(tag)}</span><button type="button" aria-label="Remove ${escapeHtml(tag)}">&times;</button>`;
    chip.querySelector("button")?.addEventListener("click", async () => onRemove(tag));
    container.appendChild(chip);
  });
}

function renderTagGroups(container, groups, kind, onRemove) {
  if (!container) {
    return;
  }
  const entries = Object.entries(groups || {}).filter(([, tags]) => Array.isArray(tags) && tags.length);
  if (!entries.length) {
    container.innerHTML = `<span class="muted">No ${kind} tags.</span>`;
    return;
  }
  container.innerHTML = entries.map(([categoryKey, tags]) => `
    <section class="tag-group-card">
      <div class="tag-group-heading">${escapeHtml(categoryKey.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase()))}</div>
      <div class="tag-chip-list" data-tag-group="${escapeHtml(categoryKey)}"></div>
    </section>
  `).join("");
  entries.forEach(([categoryKey, tags]) => {
    const group = container.querySelector(`[data-tag-group="${CSS.escape(categoryKey)}"]`);
    tags.forEach((tag) => {
      const chip = document.createElement("span");
      chip.className = `tag-chip${kind === "approved" ? " approved" : ""}`;
      chip.innerHTML = `<span>${escapeHtml(tag)}</span><button type="button" aria-label="Remove ${escapeHtml(tag)}">&times;</button>`;
      chip.querySelector("button")?.addEventListener("click", async () => onRemove(categoryKey, tag));
      group?.appendChild(chip);
    });
  });
}

function renderTagSpecEditor() {
  if (!els.libraryTagSpecCategories) {
    return;
  }
  const rows = Array.isArray(state.sessionTagSpec?.category_config?.categories)
    ? state.sessionTagSpec.category_config.categories
    : [];
  els.libraryTagSpecCategories.innerHTML = rows.map((row, index) => `
    <div class="tag-spec-row">
      <label>Key<input type="text" data-tag-spec-key="${index}" value="${escapeHtml(row.key || "")}" placeholder="category_key"></label>
      <label>Label<input type="text" data-tag-spec-label="${index}" value="${escapeHtml(row.label || "")}" placeholder="Category Label"></label>
      <label>Guidance<input type="text" data-tag-spec-guidance="${index}" value="${escapeHtml(row.guidance || "")}" placeholder="Short category guidance"></label>
      <label>Allowed Tags<textarea data-tag-spec-tags="${index}" rows="4" placeholder="One tag per line">${escapeHtml((row.allowed_tags || []).join("\n"))}</textarea></label>
      <label><input type="checkbox" data-tag-spec-free="${index}"${row.allow_free_text ? " checked" : ""}> Allow free text</label>
      <button type="button" data-tag-spec-remove="${index}">Remove</button>
    </div>
  `).join("");
}

function collectTagSpecFromForm() {
  const rows = Array.from(els.libraryTagSpecCategories?.querySelectorAll("[data-tag-spec-key]") || []).map((input) => {
    const index = Number.parseInt(input.getAttribute("data-tag-spec-key") || "-1", 10);
    const tagsRaw = String(els.libraryTagSpecCategories?.querySelector(`[data-tag-spec-tags="${index}"]`)?.value || "");
    return {
      key: String(input.value || "").trim(),
      label: String(els.libraryTagSpecCategories?.querySelector(`[data-tag-spec-label="${index}"]`)?.value || "").trim(),
      guidance: String(els.libraryTagSpecCategories?.querySelector(`[data-tag-spec-guidance="${index}"]`)?.value || "").trim(),
      allowed_tags: tagsRaw.split("\n").map((value) => value.trim()).filter(Boolean),
      allow_free_text: Boolean(els.libraryTagSpecCategories?.querySelector(`[data-tag-spec-free="${index}"]`)?.checked),
    };
  });
  return { category_config: { categories: rows } };
}

function renderTagReviewList() {
  if (!els.libraryTagCandidatesList) {
    return;
  }
  const review = state.sessionTagReview || {};
  const groups = Array.isArray(review.groups) ? review.groups : [];
  const visibleGroups = groups.filter((group) => Array.isArray(group.candidates) && group.candidates.length);
  if (!visibleGroups.length) {
    els.libraryTagCandidatesList.innerHTML = '<p class="muted">No pending candidate tags.</p>';
    return;
  }
  els.libraryTagCandidatesList.innerHTML = visibleGroups.map((group) => `
    <section class="tag-review-group">
      <div class="tag-review-group-heading">${escapeHtml(group.category_label || group.category_key || "Category")}</div>
      <div class="tag-review-group-meta">Pending ${Number(group.pending_count || 0)} | Approved ${Number(group.approved_count || 0)} | Rejected ${Number(group.rejected_count || 0)}</div>
      ${(group.candidates || []).map((item) => `
        <div class="tag-review-row">
          <span class="tag-review-label">${escapeHtml(item.tag)}</span>
          <span class="tag-review-count">${Number(item.source_count || 0)}</span>
          <span class="tag-review-actions">
            <button type="button" data-tag-candidate-approve="${escapeHtml(item.id)}">Approve</button>
            <button type="button" data-tag-candidate-reject="${escapeHtml(item.id)}">Reject</button>
          </span>
        </div>
      `).join("")}
    </section>
  `).join("");
}

function renderSuggestedTagList(container, tags, onApprove, onDismiss) {
  if (!container) {
    return;
  }
  container.innerHTML = "";
  if (!tags.length) {
    container.innerHTML = '<span class="muted">No AI suggested tags.</span>';
    return;
  }
  tags.forEach((tag) => {
    const chip = document.createElement("span");
    chip.className = "tag-chip suggested-tag-chip";
    chip.innerHTML = `<span class="suggested-tag-label">${escapeHtml(tag)}</span>`;
    const actions = document.createElement("span");
    actions.className = "suggested-tag-actions";
    const approveBtn = document.createElement("button");
    approveBtn.type = "button";
    approveBtn.className = "suggested-tag-approve-btn";
    approveBtn.textContent = "v";
    approveBtn.title = `Approve ${tag}`;
    approveBtn.setAttribute("aria-label", `Approve ${tag}`);
    approveBtn.addEventListener("click", async () => onApprove(tag));
    actions.appendChild(approveBtn);
    const dismissBtn = document.createElement("button");
    dismissBtn.type = "button";
    dismissBtn.className = "suggested-tag-dismiss-btn";
    dismissBtn.textContent = "x";
    dismissBtn.title = `Dismiss ${tag}`;
    dismissBtn.setAttribute("aria-label", `Dismiss ${tag}`);
    dismissBtn.addEventListener("click", async () => onDismiss(tag));
    actions.appendChild(dismissBtn);
    chip.appendChild(actions);
    container.appendChild(chip);
  });
}

function renderLibraryDetailTabState() {
  if (els.libraryDetailsTabBtn) {
    els.libraryDetailsTabBtn.classList.toggle("active", state.libraryDetailTab === "details");
  }
  if (els.librarySummaryPreviewTabBtn) {
    els.librarySummaryPreviewTabBtn.classList.toggle("active", state.libraryDetailTab === "summary_preview");
  }
  if (els.libraryTagSpecTabBtn) {
    els.libraryTagSpecTabBtn.classList.toggle("active", state.libraryDetailTab === "tag_spec");
  }
  if (els.libraryTagReviewTabBtn) {
    els.libraryTagReviewTabBtn.classList.toggle("active", state.libraryDetailTab === "tag_review");
  }
  if (els.libraryDetailsPanel) {
    els.libraryDetailsPanel.hidden = state.libraryDetailTab !== "details";
  }
  if (els.librarySummaryPreviewPanel) {
    els.librarySummaryPreviewPanel.hidden = state.libraryDetailTab !== "summary_preview";
  }
  if (els.libraryTagSpecPanel) {
    els.libraryTagSpecPanel.hidden = state.libraryDetailTab !== "tag_spec";
  }
  if (els.libraryTagReviewPanel) {
    els.libraryTagReviewPanel.hidden = state.libraryDetailTab !== "tag_review";
  }
}

function refreshApprovedTagSelect(selectedTag = "") {
  if (!els.libraryApprovedTagSelect) {
    return;
  }
  const options = ['<option value="">Select approved tag</option>']
    .concat(state.sessionApprovedTags.map((tag) => `<option value="${escapeHtml(tag)}">${escapeHtml(tag)}</option>`));
  els.libraryApprovedTagSelect.innerHTML = options.join("");
  if (selectedTag && state.sessionApprovedTags.includes(selectedTag)) {
    els.libraryApprovedTagSelect.value = selectedTag;
  }
}

async function saveAnnotation(sourceId, payload) {
  const session = activeSession();
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/annotations/${encodeURIComponent(sourceId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  state.paperAnnotations[sourceId] = result.data;
  renderLibraryRows();
}

async function saveSummaryPrompt() {
  const session = activeSession();
  const editorConfig = collectSummaryEditorConfigFromForm();
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/summary-settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ editor_config: editorConfig }),
  });
  state.sessionSummaryPrompt = String(result.data?.prompt_template || "");
  state.sessionSummaryEditorConfig = cloneSummaryEditorConfig(result.data?.editor_config || editorConfig);
  state.currentGlobalSummaryModel = String(result.data?.current_global_summary_model || state.currentGlobalSummaryModel || "");
  if (els.librarySummaryPromptInput) {
    els.librarySummaryPromptInput.value = state.sessionSummaryPrompt;
  }
  if (els.librarySummaryFocusInput) {
    els.librarySummaryFocusInput.value = String(state.sessionSummaryEditorConfig?.summary_focus || "");
  }
  updateSummaryPromptBuilderView();
  if (els.libraryCurrentSummaryModel) {
    els.libraryCurrentSummaryModel.textContent = state.currentGlobalSummaryModel || "-";
  }
  if (els.librarySummaryCurrentModelValue) {
    els.librarySummaryCurrentModelValue.textContent = state.currentGlobalSummaryModel || "-";
  }
  els.librarySummaryPromptState.textContent = "Summary prompt builder saved.";
}

async function resetSummaryPrompt() {
  const session = activeSession();
  const editorConfig = cloneSummaryEditorConfig(defaultSummaryEditorConfig());
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/summary-settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ editor_config: editorConfig }),
  });
  state.sessionSummaryPrompt = result.data?.prompt_template || DEFAULT_SUMMARY_PROMPT;
  state.sessionSummaryEditorConfig = cloneSummaryEditorConfig(result.data?.editor_config || editorConfig);
  state.currentGlobalSummaryModel = String(result.data?.current_global_summary_model || state.currentGlobalSummaryModel || "");
  if (els.librarySummaryPromptInput) {
    els.librarySummaryPromptInput.value = state.sessionSummaryPrompt;
  }
  if (els.librarySummaryFocusInput) {
    els.librarySummaryFocusInput.value = String(state.sessionSummaryEditorConfig?.summary_focus || "");
  }
  updateSummaryPromptBuilderView();
  if (els.libraryCurrentSummaryModel) {
    els.libraryCurrentSummaryModel.textContent = state.currentGlobalSummaryModel || "-";
  }
  if (els.librarySummaryCurrentModelValue) {
    els.librarySummaryCurrentModelValue.textContent = state.currentGlobalSummaryModel || "-";
  }
  if (els.librarySummaryPromptState) {
    els.librarySummaryPromptState.textContent = "Summary prompt reset to default.";
  }
}

async function saveTagSpec() {
  const session = activeSession();
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/tag-spec`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(collectTagSpecFromForm()),
  });
  state.sessionTagSpec = result.data || state.sessionTagSpec;
  if (els.libraryTagPromptInput) {
    els.libraryTagPromptInput.value = String(state.sessionTagSpec?.prompt_template || "");
  }
  renderTagSpecEditor();
  if (els.libraryTagSpecState) {
    els.libraryTagSpecState.textContent = "Tag spec saved.";
  }
}

async function resetTagSpec() {
  const session = activeSession();
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/tag-spec`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category_config: cloneTagSpecConfig() }),
  });
  state.sessionTagSpec = result.data || state.sessionTagSpec;
  if (els.libraryTagPromptInput) {
    els.libraryTagPromptInput.value = String(state.sessionTagSpec?.prompt_template || "");
  }
  renderTagSpecEditor();
  if (els.libraryTagSpecState) {
    els.libraryTagSpecState.textContent = "Tag spec reset to default.";
  }
}

async function saveApprovedTags() {
  const session = activeSession();
  const tags = String(els.libraryApprovedTagsInput?.value || "")
    .split("\n")
    .map((value) => value.trim())
    .filter(Boolean);
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/tag-catalog`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tags }),
  });
  state.sessionApprovedTags = Array.isArray(result.data?.tags) ? result.data.tags : [];
  refreshApprovedTagSelect();
  els.libraryApprovedTagsState.textContent = state.sessionApprovedTags.length
    ? `${state.sessionApprovedTags.length} approved tag${state.sessionApprovedTags.length === 1 ? "" : "s"} saved.`
    : "No approved tags configured for this session.";
  renderLibraryRows();
}

function startSummaryPoll() {
  stopSummaryPoll();
  if (!state.activeSessionId || state.activePage !== "library" || state.serverOffline) {
    return;
  }
  state.summaryPollTimer = window.setInterval(async () => {
    if (state.activePage !== "library" || state.serverOffline || document.visibilityState === "hidden") {
      stopSummaryPoll();
      return;
    }
    const annotationActive = Object.values(state.paperAnnotations).some((item) => {
      return item.summary_status === "queued"
        || item.summary_status === "running"
        || item.tag_suggestion_status === "queued"
        || item.tag_suggestion_status === "running";
    });
    const reviewActive = ["queued", "running"].includes(String(state.sessionTagReview?.candidate_generation_status || ""))
      || ["queued", "running"].includes(String(state.sessionTagReview?.tag_assignment_status || ""));
    if (!annotationActive && !reviewActive) {
      stopSummaryPoll();
      return;
    }
    try {
      await loadLibraryAnnotations(activeSession().id, libraryAnnotationTargetIds());
      await loadLibraryTagReview(activeSession().id);
      renderLibraryRows();
    } catch {
      // Best effort.
    }
  }, 3000);
}

function stopSummaryPoll() {
  if (state.summaryPollTimer) {
    clearInterval(state.summaryPollTimer);
    state.summaryPollTimer = null;
  }
}

async function queueSummaryGeneration(sourceIds, forceRegenerate = false) {
  const session = activeSession();
  if (!sourceIds.length) {
    setLibraryState("No papers selected for summary generation.", 5000);
    return;
  }
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/summaries/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_ids: sourceIds, force_regenerate: forceRegenerate }),
  });
  const data = result.data || {};
  const blocked = Array.isArray(data.blocked) ? data.blocked : [];
  const blockedSummary = blockedSummaryText(blocked);
  setLibraryState(
    blockedSummary
      ? `Summary generation queued: ${data.queued_count || 0}. Blocked: ${blocked.length} (${blockedSummary}).`
      : `Summary generation queued: ${data.queued_count || 0}. Blocked: ${blocked.length}.`,
    8000,
  );
  await loadLibraryAnnotations(session.id, uniqueIds([...sourceIds, ...libraryAnnotationTargetIds()]));
  renderLibraryRows();
  if ((data.queued_count || 0) > 0) {
    startSummaryPoll();
  }
}

async function queueTagGeneration(sourceIds, forceRegenerate = false) {
  const session = activeSession();
  if (!sourceIds.length) {
    setLibraryState("No papers selected for tag generation.", 5000);
    return;
  }
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/tags/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_ids: sourceIds, force_regenerate: forceRegenerate }),
  });
  const data = result.data || {};
  const blocked = Array.isArray(data.blocked) ? data.blocked : [];
  const blockedSummary = blockedSummaryText(blocked);
  setLibraryState(
    blockedSummary
      ? `Tag generation queued: ${data.queued_count || 0}. Blocked: ${blocked.length} (${blockedSummary}).`
      : `Tag generation queued: ${data.queued_count || 0}. Blocked: ${blocked.length}.`,
    8000,
  );
  await loadLibraryAnnotations(session.id, uniqueIds([...sourceIds, ...libraryAnnotationTargetIds()]));
  renderLibraryRows();
  if ((data.queued_count || 0) > 0) {
    startSummaryPoll();
  }
}

async function queueSessionTagCandidates(forceRegenerate = false) {
  const session = activeSession();
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/tag-review/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force_regenerate: forceRegenerate }),
  });
  await loadLibraryTagReview(session.id);
  renderLibraryRows();
  setLibraryState(
    result.data?.status === "queued"
      ? `Candidate tag generation queued for all accepted papers.`
      : "Candidate tag generation requested.",
    8000,
  );
  startSummaryPoll();
}

async function applyApprovedTags(forceRegenerate = false) {
  const session = activeSession();
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/tags/apply-approved`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force_regenerate: forceRegenerate }),
  });
  await loadLibraryTagReview(session.id);
  renderLibraryRows();
  setLibraryState(
    result.data?.status === "queued"
      ? "Approved-tag assignment queued for all accepted papers."
      : "Approved-tag assignment requested.",
    8000,
  );
  startSummaryPoll();
}

async function applyApprovedTagsForSelectedPaper(forceRegenerate = false) {
  const session = activeSession();
  const sourceId = state.selectedLibrarySourceId;
  if (!sourceId) {
    setLibraryState("No paper selected for approved-tag assignment.", 5000);
    return;
  }
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/annotations/${encodeURIComponent(sourceId)}/tags/apply-approved`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force_regenerate: forceRegenerate }),
  });
  await loadLibraryAnnotations(session.id, uniqueIds([sourceId, ...libraryAnnotationTargetIds()]));
  await loadLibraryTagReview(session.id);
  renderLibraryRows();
  setLibraryState(
    result.data?.status === "queued"
      ? "Approved-tag assignment queued for the selected paper."
      : "Approved-tag assignment requested for the selected paper.",
    8000,
  );
  startSummaryPoll();
}

async function approveTagCandidate(candidateId) {
  const session = activeSession();
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/tag-candidates/${encodeURIComponent(candidateId)}/approve`, {
    method: "POST",
  });
  state.sessionTagReview = result.data || state.sessionTagReview;
  await loadLibraryTagCatalog(session.id);
  renderLibraryRows();
}

async function rejectTagCandidate(candidateId) {
  const session = activeSession();
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/tag-candidates/${encodeURIComponent(candidateId)}/reject`, {
    method: "POST",
  });
  state.sessionTagReview = result.data || state.sessionTagReview;
  renderLibraryRows();
}

async function resetRejectedTagCandidates() {
  const session = activeSession();
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/tag-candidates/reset-rejections`, {
    method: "POST",
  });
  state.sessionTagReview = result.data || state.sessionTagReview;
  renderLibraryRows();
  setLibraryState("Rejected candidate tags reset.", 5000);
}

async function promoteSuggestedTag(sourceId, tag, target) {
  const session = activeSession();
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/annotations/${encodeURIComponent(sourceId)}/suggested-tags/promote`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tag, target }),
  });
  state.paperAnnotations[sourceId] = result.data;
  if (target === "approved" && !state.sessionApprovedTags.some((value) => value.toLowerCase() === String(tag).toLowerCase())) {
    state.sessionApprovedTags = state.sessionApprovedTags.concat([tag]).sort((left, right) => left.localeCompare(right));
  }
  refreshApprovedTagSelect();
  renderLibraryRows();
}

async function dismissSuggestedTag(sourceId, tag) {
  const session = activeSession();
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/annotations/${encodeURIComponent(sourceId)}/suggested-tags/dismiss`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tag }),
  });
  state.paperAnnotations[sourceId] = result.data;
  renderLibraryRows();
}

function renderLibraryDetail(item) {
  if (!item) {
    els.libraryTitle.textContent = "No paper selected.";
    els.libraryAbstract.textContent = "Select a paper to inspect and export.";
    els.libraryMetadata.innerHTML = "Year: - | Journal: - | Citations: - | Authors: - | Link: -";
    if (els.libraryBookmarkBtn) {
      els.libraryBookmarkBtn.textContent = "Bookmark";
      els.libraryBookmarkBtn.disabled = true;
    }
    if (els.librarySummaryStatus) {
      els.librarySummaryStatus.textContent = "No summary generated.";
    }
    if (els.librarySummaryText) {
      els.librarySummaryText.textContent = "Select a paper to inspect tags and summary.";
    }
    if (els.librarySummaryPreviewStatus) {
      els.librarySummaryPreviewStatus.textContent = "No summary generated.";
    }
    if (els.librarySummaryPreviewText) {
      els.librarySummaryPreviewText.textContent = "Select a paper to inspect generated summary.";
    }
    if (els.librarySummaryCurrentModelValue) {
      els.librarySummaryCurrentModelValue.textContent = state.currentGlobalSummaryModel || "-";
    }
    if (els.librarySummaryLastModelValue) {
      els.librarySummaryLastModelValue.textContent = "-";
    }
    if (els.librarySummaryStructuredText) {
      els.librarySummaryStructuredText.innerHTML = formatStructuredSummary(null);
    }
    if (els.libraryGenerateSummaryBtn) {
      els.libraryGenerateSummaryBtn.disabled = true;
    }
    if (els.libraryRegenerateSummaryBtn) {
      els.libraryRegenerateSummaryBtn.disabled = true;
    }
    if (els.librarySummaryPreviewGenerateBtn) {
      els.librarySummaryPreviewGenerateBtn.disabled = true;
    }
    if (els.librarySummaryPreviewRegenerateBtn) {
      els.librarySummaryPreviewRegenerateBtn.disabled = true;
    }
    if (els.librarySummaryPreviewOpenPdfBtn) {
      els.librarySummaryPreviewOpenPdfBtn.disabled = true;
    }
    if (els.libraryCopySummaryBtn) {
      els.libraryCopySummaryBtn.disabled = true;
    }
    if (els.libraryApplyApprovedTagsOneBtn) {
      els.libraryApplyApprovedTagsOneBtn.disabled = true;
    }
    if (els.libraryReapplyApprovedTagsOneBtn) {
      els.libraryReapplyApprovedTagsOneBtn.disabled = true;
    }
    renderTagGroups(els.libraryFreeformTags, {}, "freeform", async () => {});
    renderTagGroups(els.libraryApprovedTags, {}, "approved", async () => {});
    refreshApprovedTagSelect();
    renderTagSpecEditor();
    renderTagReviewList();
    if (els.libraryTagCandidateStatus) {
      els.libraryTagCandidateStatus.textContent = "No candidate-tag generation has run yet.";
    }
    if (els.libraryTagAssignmentStatus) {
      els.libraryTagAssignmentStatus.textContent = "Approved tags have not been applied to papers yet.";
    }
    if (els.libraryTagPendingCount) {
      els.libraryTagPendingCount.textContent = "0";
    }
    if (els.libraryTagApprovedCount) {
      els.libraryTagApprovedCount.textContent = "0";
    }
    if (els.libraryTagRejectedCount) {
      els.libraryTagRejectedCount.textContent = "0";
    }
    renderLibraryDetailTabState();
    return;
  }
  const annotation = annotationForSource(item.id);
  els.libraryTitle.textContent = `${isBookmarked(item.id) ? "[Bookmarked] " : ""}${item.title}`;
  els.libraryAbstract.textContent = item.abstract || "No abstract available.";
  els.libraryMetadata.innerHTML = buildMetadataHtml(item);
  if (els.libraryBookmarkBtn) {
    els.libraryBookmarkBtn.textContent = isBookmarked(item.id) ? "Remove Bookmark" : "Bookmark";
    els.libraryBookmarkBtn.disabled = false;
  }
  renderTagGroups(els.libraryFreeformTags, annotation.freeform_tags_by_category || {}, "freeform", async (categoryKey, tag) => {
    const nextGroups = JSON.parse(JSON.stringify(annotation.freeform_tags_by_category || {}));
    nextGroups[categoryKey] = (nextGroups[categoryKey] || []).filter((value) => value !== tag);
    if (!nextGroups[categoryKey].length) {
      delete nextGroups[categoryKey];
    }
    await saveAnnotation(item.id, {
      freeform_tags_by_category: nextGroups,
      approved_tags_by_category: annotation.approved_tags_by_category || {},
    });
    els.libraryState.textContent = `Removed tag: ${tag}`;
  });
  renderTagGroups(els.libraryApprovedTags, annotation.approved_tags_by_category || {}, "approved", async (categoryKey, tag) => {
    const nextGroups = JSON.parse(JSON.stringify(annotation.approved_tags_by_category || {}));
    nextGroups[categoryKey] = (nextGroups[categoryKey] || []).filter((value) => value !== tag);
    if (!nextGroups[categoryKey].length) {
      delete nextGroups[categoryKey];
    }
    await saveAnnotation(item.id, {
      freeform_tags_by_category: annotation.freeform_tags_by_category || {},
      approved_tags_by_category: nextGroups,
    });
    els.libraryState.textContent = `Removed approved tag: ${tag}`;
  });
  refreshApprovedTagSelect();
  if (els.librarySummaryText) {
    els.librarySummaryText.textContent = annotation.ai_summary || "No summary generated for this paper yet.";
  }
  if (els.librarySummaryPreviewText) {
    els.librarySummaryPreviewText.textContent = annotation.ai_summary || "No generated summary for this paper yet.";
  }
  if (els.librarySummaryCurrentModelValue) {
    els.librarySummaryCurrentModelValue.textContent = state.currentGlobalSummaryModel || "-";
  }
  if (els.librarySummaryLastModelValue) {
    els.librarySummaryLastModelValue.textContent = annotation.summary_model || "-";
  }
  if (els.librarySummaryStructuredText) {
    els.librarySummaryStructuredText.innerHTML = formatStructuredSummary(annotation.ai_summary_json);
  }
  if (els.librarySummaryStatus) {
    const reason = annotation.summary_block_reason === "parsed_text_required"
      ? "Summary unavailable until the paper is downloaded and parsed."
      : "";
    const error = annotation.summary_error ? ` Error: ${annotation.summary_error}` : "";
    els.librarySummaryStatus.textContent = `Status: ${annotation.summary_status}.${reason ? ` ${reason}` : ""}${error}`;
  }
  if (els.librarySummaryPreviewStatus) {
    const reason = annotation.summary_block_reason === "parsed_text_required"
      ? "Summary unavailable until the paper is downloaded and parsed."
      : "";
    const error = annotation.summary_error ? ` Error: ${annotation.summary_error}` : "";
    els.librarySummaryPreviewStatus.textContent = `Status: ${annotation.summary_status}.${reason ? ` ${reason}` : ""}${error}`;
  }
  if (els.libraryGenerateSummaryBtn) {
    els.libraryGenerateSummaryBtn.disabled = !annotation.can_generate_summary || annotation.summary_status === "completed";
  }
  if (els.libraryRegenerateSummaryBtn) {
    els.libraryRegenerateSummaryBtn.disabled = !annotation.can_generate_summary;
  }
  if (els.librarySummaryPreviewGenerateBtn) {
    els.librarySummaryPreviewGenerateBtn.disabled = !annotation.can_generate_summary || annotation.summary_status === "completed";
  }
  if (els.librarySummaryPreviewRegenerateBtn) {
    els.librarySummaryPreviewRegenerateBtn.disabled = !annotation.can_generate_summary;
  }
  if (els.librarySummaryPreviewOpenPdfBtn) {
    els.librarySummaryPreviewOpenPdfBtn.disabled = !item.previewable_pdf_artifact_id;
  }
  if (els.libraryCopySummaryBtn) {
    els.libraryCopySummaryBtn.disabled = !annotation.ai_summary;
  }
  if (els.libraryApplyApprovedTagsOneBtn) {
    els.libraryApplyApprovedTagsOneBtn.disabled = !annotation.can_generate_tags || annotation.tag_suggestion_status === "completed";
  }
  if (els.libraryReapplyApprovedTagsOneBtn) {
    els.libraryReapplyApprovedTagsOneBtn.disabled = !annotation.can_generate_tags;
  }
  const review = state.sessionTagReview || {};
  if (els.libraryTagCandidateStatus) {
    const error = review.candidate_generation_error ? ` Error: ${review.candidate_generation_error}` : "";
    els.libraryTagCandidateStatus.textContent = `Candidate generation: ${review.candidate_generation_status || "none"}.${error}`;
  }
  if (els.libraryTagAssignmentStatus) {
    const error = review.tag_assignment_error ? ` Error: ${review.tag_assignment_error}` : "";
    els.libraryTagAssignmentStatus.textContent = `Approved tag assignment: ${review.tag_assignment_status || "none"}.${error}`;
  }
  if (els.libraryTagPendingCount) {
    els.libraryTagPendingCount.textContent = String(review.pending_count || 0);
  }
  if (els.libraryTagApprovedCount) {
    els.libraryTagApprovedCount.textContent = String(review.approved_count || 0);
  }
  if (els.libraryTagRejectedCount) {
    els.libraryTagRejectedCount.textContent = String(review.rejected_count || 0);
  }
  renderTagReviewList();
  if (els.libraryGenerateCandidateTagsBtn) {
    els.libraryGenerateCandidateTagsBtn.disabled = review.candidate_generation_status === "queued" || review.candidate_generation_status === "running";
  }
  if (els.libraryRegenerateCandidateTagsBtn) {
    els.libraryRegenerateCandidateTagsBtn.disabled = review.candidate_generation_status === "queued" || review.candidate_generation_status === "running";
  }
  if (els.libraryResetRejectedTagsBtn) {
    els.libraryResetRejectedTagsBtn.disabled = !Number(review.rejected_count || 0);
  }
  if (els.libraryApplyApprovedTagsBtn) {
    els.libraryApplyApprovedTagsBtn.disabled = !Number(review.approved_count || 0) || review.tag_assignment_status === "queued" || review.tag_assignment_status === "running";
  }
  if (els.libraryReapplyApprovedTagsBtn) {
    els.libraryReapplyApprovedTagsBtn.disabled = !Number(review.approved_count || 0) || review.tag_assignment_status === "queued" || review.tag_assignment_status === "running";
  }
  renderLibraryDetailTabState();
}

function renderLibraryRows() {
  const query = els.libraryQuery.value.trim().toLowerCase();
  const parsedOnly = Boolean(els.libraryParsedOnlyCheckbox?.checked);
  const pdfOnly = Boolean(els.libraryPdfOnlyCheckbox?.checked);
  const summaryCurrentOnly = Boolean(els.librarySummaryCurrentOnlyCheckbox?.checked);
  const filtered = !query
    ? [...state.libraryRows]
    : state.libraryRows.filter((item) => `${item.title} ${item.abstract || ""} ${tagSearchBlob(item.id)}`.toLowerCase().includes(query));
  const scoped = filtered.filter((item) => {
    const badgeState = documentBadgeState(item);
    if (parsedOnly && badgeState.parseScopeStatus !== "parsed") {
      return false;
    }
    if (pdfOnly && !badgeState.previewablePdfArtifactId) {
      return false;
    }
    if (summaryCurrentOnly && !badgeState.summaryUpToDate) {
      return false;
    }
    return true;
  });
  state.libraryFilteredRows = sortedLibraryRows(scoped);
  renderLibrarySortButtons();
  els.libraryRows.innerHTML = "";
  state.libraryFilteredRows.forEach((item, index) => {
    const tr = document.createElement("tr");
    tr.classList.toggle("active", item.id === state.selectedLibrarySourceId);
    const annotation = annotationForSource(item.id);
    const tagMarker = (annotation.freeform_tags?.length || annotation.approved_tags?.length)
      ? '<span class="bookmark-chip">T</span> '
      : "";
    const docCell = renderDocumentBadgeCell(item);
    tr.innerHTML = `<td class="col-lineage">${escapeHtml(formatLineageNumber(item))}</td><td class="col-score">${Number(item.relevance_score || 0).toFixed(2)}</td><td class="col-year">${item.year || "-"}</td><td class="col-citations">${item.citation_count ?? "-"}</td><td><div class="title-cell">${tagMarker}${isBookmarked(item.id) ? '<span class="bookmark-chip">B</span>' : ""}<span class="title-cell-text">${escapeHtml(item.title)}</span><button type="button" class="mini-copy-btn title-cell-copy" title="Copy title" aria-label="Copy title">Copy</button></div></td><td class="col-doc">${docCell}</td>`;
    tr.querySelector(".mini-copy-btn")?.addEventListener("click", async (event) => {
      event.stopPropagation();
      const ok = await copyTextToClipboard(item.title);
      els.libraryState.textContent = ok ? `Copied title: ${item.title}` : "Unable to copy title.";
    });
    tr.addEventListener("click", () => {
      state.selectedLibrarySourceId = item.id;
      renderLibraryRows();
      renderLibraryDetail(item);
    });
    els.libraryRows.appendChild(tr);
  });
  const scores = state.libraryFilteredRows.map((item) => Number(item.relevance_score || 0));
  els.libraryMatches.textContent = String(state.libraryFilteredRows.length);
  els.libraryHighest.textContent = scores.length ? Math.max(...scores).toFixed(2) : "-";
  els.libraryLowest.textContent = scores.length ? Math.min(...scores).toFixed(2) : "-";
  const detail = state.libraryFilteredRows.find((item) => item.id === state.selectedLibrarySourceId) || state.libraryFilteredRows[0];
  state.selectedLibrarySourceId = detail?.id || "";
  renderLibraryDetail(detail);
}

function resetSessionBoundPaneState() {
  state.currentDiscoveryStatus = null;
  state.currentAcquisitionStatus = null;
  state.discoverRunQueries = [];
  state.suggestedQueries = [];
  state.suggestionStateSticky = false;
  state.reviewItems = [];
  state.reviewIndex = -1;
  state.documentRows = [];
  state.libraryRows = [];
  state.libraryFilteredRows = [];
  state.paperAnnotations = {};
  state.sessionApprovedTags = [];
  state.sessionSummaryPrompt = "";
  state.sessionTagReview = null;
  state.selectedDocumentSourceId = "";
  state.selectedReviewSourceId = "";
  state.selectedLibrarySourceId = "";

  els.discoverIterationLine.textContent = "Run: -";
  ["discoverSummaryDiscovered", "discoverSummaryApproved", "discoverSummaryRejected", "discoverSummaryReviewed", "discoverSummaryPending"].forEach((id) => {
    els[id].textContent = "0";
  });
  renderDiscoverRunQueries();
  renderSuggestedQueries();
  updateCitationAvailability(0, 0);
  els.resumeCitationBtn.disabled = true;
  els.discoverState.textContent = "No discovery run attached to the active session.";

  renderReviewRows();
  els.reviewState.textContent = "No discovery run attached to the active session.";

  els.documentsDownloaded.textContent = "0";
  els.documentsFailed.textContent = "0";
  els.documentsManual.textContent = "0";
  els.documentsPending.textContent = "0";
  els.documentsBadge.textContent = "0";
  renderDocuments();
  els.documentsState.textContent = "No discovery run attached to the active session.";

  renderLibraryRows();
  els.libraryState.textContent = "No discovery run attached to the active session.";
  stopSummaryPoll();

  renderStopButton();
  renderActivity();
}

async function loadLibrary(recoverOnNotFound = true) {
  const session = activeSession();
  if (!session.id) {
    state.libraryRows = [];
    renderLibraryRows();
    return;
  }
  let result;
  try {
    const acceptedItems = await fetchAllPages(async (offset, limit) => {
      const response = await api(sessionSourcesPath(session.id, "accepted", limit, offset));
      return response.data || {};
    }, API_FETCH_PAGE_SIZE);
    result = { data: { items: acceptedItems } };
  } catch (error) {
    if (recoverOnNotFound && isRunNotFoundError(error)) {
      const rebound = await rebindSessionToLatestRun("Saved discovery run was not found.");
      if (rebound) {
        return loadLibrary(false);
      }
    }
    throw error;
  }
  state.libraryRows = result.data.items || [];
  await loadLibraryAnnotations(session.id, libraryAnnotationTargetIds(state.libraryRows));
  await loadLibraryTagCatalog(session.id);
  await loadLibraryTagSpec(session.id);
  await loadLibrarySummarySettings(session.id);
  await loadLibraryTagReview(session.id);
  renderLibraryRows();
  if (canOverwriteLibraryState()) {
    setLibraryState(state.libraryRows.length ? "Library export data loaded." : "No accepted sources available.");
  }
  if (
    Object.values(state.paperAnnotations).some((item) => {
      return item.summary_status === "queued"
        || item.summary_status === "running"
        || item.tag_suggestion_status === "queued"
        || item.tag_suggestion_status === "running";
    })
    || ["queued", "running"].includes(String(state.sessionTagReview?.candidate_generation_status || ""))
    || ["queued", "running"].includes(String(state.sessionTagReview?.tag_assignment_status || ""))
  ) {
    startSummaryPoll();
  } else {
    stopSummaryPoll();
  }
}

async function createDiscoveryRun() {
  const session = activeSession();
  const queries = activeQueries(session);
  const context = normalizeSessionContext(session.sessionContext || els.sessionContextInput.value);
  const providerLimits = normalizeProviderLimits(session.providerLimits);
  if (!queries.length) {
    els.discoverState.textContent = "Select at least one manual query.";
    return;
  }
  if (!context) {
    els.discoverState.textContent = "Session context is required before running discovery.";
    els.sessionContextState.textContent = "Session context is required before running discovery.";
    return;
  }
  const saved = await saveSessionContext();
  if (!saved) {
    els.discoverState.textContent = "Save session context first.";
    return;
  }
  beginBusy("Searching providers");
  setProgress(10, "Queued");
  try {
    const result = await api("/v1/discovery/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        seed_queries: queries,
        selected_queries: queries,
        session_id: session.id,
        session_context: context,
        max_iterations: 1,
        provider_limits: providerLimits,
      }),
    });
    session.discoveryRunId = result.data.run_id;
    session.resultsRunId = result.data.run_id;
    els.discoverState.textContent = "Discovery started. Review/Documents/Library will update live.";
    session.acquisitionRunId = "";
    session.exportSourceIds = [];
    persistSessions();
    await refreshAll();
  } finally {
    endBusy();
  }
}

async function createNextCitationIteration() {
  const session = activeSession();
  if (!session) {
    els.discoverState.textContent = "No active session is selected.";
    return;
  }
  if (!session.discoveryRunId) {
    els.discoverState.textContent = "Run discovery before starting citation expansion.";
    return;
  }
  beginBusy("Running citation expansion");
  setProgress(10, "Queued");
  els.discoverState.textContent = "Citation expansion request sent. Waiting for worker startup.";
  try {
    const providerLimits = normalizeProviderLimits(session.providerLimits || {});
    const result = await api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/next-citation-iteration`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected_queries: activeQueries(session), provider_limits: providerLimits }),
    });
    session.discoveryRunId = result.data.run_id;
    session.resultsRunId = result.data.run_id;
    els.discoverState.textContent = "Citation expansion started. Review/Documents/Library will update live.";
    persistSessions();
    await refreshDiscoverSessionState();
  } catch (error) {
    els.discoverState.textContent = citationIterationErrorText(error);
  } finally {
    endBusy();
  }
}

async function resumeCitationIteration() {
  const session = activeSession();
  if (!session) {
    els.discoverState.textContent = "No active session is selected.";
    return;
  }
  if (!session.discoveryRunId) {
    els.discoverState.textContent = "Run discovery first.";
    return;
  }
  beginBusy("Resuming citation expansion");
  setProgress(15, "Resuming");
  els.discoverState.textContent = "Citation expansion resume request sent. Waiting for worker startup.";
  try {
    await api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/citation-expansion/resume`, {
      method: "POST",
    });
    await refreshDiscoverSessionState();
  } catch (error) {
    els.discoverState.textContent = citationIterationErrorText(error);
  } finally {
    endBusy();
  }
}

async function startAcquisition(retryFailedOnly, selectedSourceIds = null) {
  const session = activeSession();
  const runId = resultsRunId(session);
  if (!runId) {
    els.documentsState.textContent = "Run discovery first.";
    return;
  }
  beginBusy("Downloading documents");
  setProgress(15, "Queued");
  try {
    const result = await api("/v1/acquisition/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        run_id: runId,
        retry_failed_only: retryFailedOnly,
        selected_source_ids: selectedSourceIds,
        internal_repository_base_url: state.internalRepositoryBaseUrl || null,
      }),
    });
    session.acquisitionRunId = result.data.acq_run_id;
    persistSessions();
    await loadLatestIds();
    await loadDocuments();
  } finally {
    endBusy();
  }
}

async function startParseForLatestAcquisition() {
  const sessionId = activeSession()?.id || "";
  if (!sessionId) {
    setAdvancedParseState("No active session is available yet. Discover and download documents first.", 5000);
    return;
  }
  beginBusy("Starting parse");
  try {
    const result = await api("/v1/parse/runs/queue-all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
      }),
    });
    const queuedRuns = Number(result.data?.queued_runs || 0);
    if (queuedRuns > 0) {
      const targets = Array.isArray(result.data?.queued_summary) ? result.data.queued_summary : [];
      const summary = targets.length
        ? targets.map((row) => `${row.acq_run_id} (${row.total_documents})`).join(", ")
        : (Array.isArray(result.data?.acquisition_run_ids) ? result.data.acquisition_run_ids.join(", ") : "");
      setAdvancedParseState(
        `Queued ${queuedRuns} parse run${queuedRuns === 1 ? "" : "s"} for downloaded documents in session ${sessionId}${summary ? `: ${summary}` : ""}.`,
        8000,
      );
    } else {
      setAdvancedParseState(`No unparsed downloaded documents were found for session ${sessionId}.`, 8000);
    }
    await loadLatestIds();
    await loadDocuments();
    if (state.activePage === "advanced") {
      await loadAdvancedOperationalEvents();
    }
  } catch (error) {
    setAdvancedParseState(`Unable to start parse: ${errorDetail(error)}`, 8000);
  } finally {
    endBusy();
  }
}

async function ensureAcquisitionRunForUpload() {
  const session = activeSession();
  if (session.acquisitionRunId) {
    return session.acquisitionRunId;
  }
  const visibleSourceIds = state.documentRows.map((row) => row.source.id);
  if (!visibleSourceIds.length) {
    throw new Error("No accepted session documents are available for upload.");
  }
  const runId = resultsRunId(session);
  if (!runId) {
    throw new Error("Run discovery first.");
  }
  const result = await api("/v1/acquisition/runs/manual-context", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      run_id: runId,
      selected_source_ids: visibleSourceIds,
      internal_repository_base_url: state.internalRepositoryBaseUrl || null,
    }),
  });
  session.acquisitionRunId = result.data.acq_run_id;
  persistSessions();
  await loadLatestIds();
  return session.acquisitionRunId;
}

function pendingDocumentSourceIds() {
  return state.documentRows
    .filter((row) => row.status === "pending")
    .map((row) => row.source.id);
}

function retryableDocumentSourceIds() {
  return state.documentRows
    .filter((row) => row.status === "failed" || row.status === "partial")
    .map((row) => row.source.id);
}

async function stopRunningTask() {
  const task = currentStoppableTask();
  if (!task) {
    els.activityLine.textContent = "No running task to stop.";
    return;
  }
  beginBusy(`Stopping ${task.kind}`);
  try {
    const path = task.kind === "acquisition"
      ? `/v1/acquisition/runs/${encodeURIComponent(task.runId)}/stop`
      : `/v1/discovery/runs/${encodeURIComponent(task.runId)}/stop`;
    await api(path, { method: "POST" });
    if (task.kind === "acquisition") {
      els.documentsState.textContent = "Stop requested.";
    } else {
      els.discoverState.textContent = "Stop requested.";
    }
    if (task.kind === "acquisition") {
      await refreshAll();
    } else {
      await refreshDiscoverSessionState();
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (task.kind === "acquisition") {
      els.documentsState.textContent = message;
    } else {
      els.discoverState.textContent = message;
    }
  } finally {
    endBusy();
  }
}

async function handleSelectedDocumentAction() {
  const row = selectedDocumentRow();
  if (!row) {
    return;
  }
  if (row.status === "pending") {
    await startAcquisition(false, [row.source.id]);
    return;
  }
  if (row.status === "failed" || row.status === "partial") {
    await startAcquisition(true, [row.source.id]);
    return;
  }
  if (row.previewablePdfArtifactId) {
    const result = await openPdfArtifactPreview(row.previewablePdfArtifactId);
    els.documentsState.textContent = pdfPreviewStatusMessage(result, row.title);
    return;
  }
  const link = formatLink(row.source);
  if (link) {
    window.open(link, "_blank", "noopener,noreferrer");
  }
}

async function uploadBatchFiles(event) {
  event.preventDefault();
  const files = Array.from(els.batchUploadFiles.files || []);
  if (!files.length) {
    els.documentsState.textContent = "Choose at least one file.";
    return;
  }
  beginBusy("Downloading documents");
  try {
    const acqRunId = await ensureAcquisitionRunForUpload();
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    const result = await api(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/manual-upload-batch`, {
      method: "POST",
      body: form,
    });
    const payload = result.data || {};
    const lines = [
      `Matched: ${payload.matched ?? 0}`,
      `Unmatched: ${payload.unmatched ?? 0}`,
      `Ambiguous: ${payload.ambiguous ?? 0}`,
      "",
    ];
    (payload.items || []).forEach((item) => {
      const source = item.source_id ? ` -> ${item.source_id}` : "";
      const score = typeof item.score === "number" ? ` (score ${item.score})` : "";
      const reason = item.reason ? ` [${item.reason}]` : "";
      lines.push(`${item.status}: ${item.filename}${source}${score}${reason}`);
    });
    els.batchUploadResults.textContent = lines.join("\n").trim();
    els.documentsState.textContent = `Batch upload processed: ${payload.matched ?? 0} matched, ${payload.unmatched ?? 0} unmatched, ${payload.ambiguous ?? 0} ambiguous.`;
    await loadDocuments();
  } catch (error) {
    const detail = errorDetail(error) || "batch_upload_failed";
    els.batchUploadResults.textContent = `Upload failed: ${detail}`;
    els.documentsState.textContent = `Upload failed: ${detail}`;
  } finally {
    endBusy();
  }
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function exportDocumentsCsv() {
  const session = activeSession();
  const runId = resultsRunId(session);
  if (!runId) {
    return;
  }
  beginBusy("Preparing export");
  try {
    const allRows = state.documentRows.length
      ? state.documentRows
      : normalizeDocumentRows(
          await fetchAllPages(async (offset, limit) => {
            const response = await api(sessionSourcesPath(session.id, "accepted", limit, offset));
            return response.data || {};
          }, API_FETCH_PAGE_SIZE),
          new Map(),
        );
    const params = new URLSearchParams();
    for (const id of allRows.map((row) => row.source.id)) {
      params.append("source_id", id);
    }
    const result = await api(`/v1/library-export/runs/${encodeURIComponent(runId)}/metadata.csv?${params.toString()}`);
    downloadBlob(result.data, `documents_${runId}.csv`);
  } finally {
    endBusy();
  }
}

async function exportLibraryMetadata() {
  const session = activeSession();
  const runId = resultsRunId(session);
  if (!runId) {
    return;
  }
  beginBusy("Preparing export");
  try {
    const params = new URLSearchParams();
    for (const id of selectedLibraryIds()) {
      params.append("source_id", id);
    }
    const result = await api(`/v1/library-export/runs/${encodeURIComponent(runId)}/metadata.csv?${params.toString()}`);
    downloadBlob(result.data, `library_export_${runId}.csv`);
  } finally {
    endBusy();
  }
}

async function exportLibraryZip() {
  const session = activeSession();
  const runId = resultsRunId(session);
  if (!runId) {
    return;
  }
  beginBusy("Preparing export");
  try {
    const params = new URLSearchParams();
    for (const id of selectedLibraryIds()) {
      params.append("source_id", id);
    }
    const path = `/v1/library-export/runs/${encodeURIComponent(runId)}/pdfs.zip?${params.toString()}`;
    if (state.authEnabled && state.token) {
      const result = await api(path);
      downloadBlob(result.data, `library_export_${runId}.zip`);
      return;
    }
    const anchor = document.createElement("a");
    anchor.href = path;
    anchor.download = `library_export_${runId}.zip`;
    anchor.rel = "noopener noreferrer";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    endBusy();
  }
}

function updateSelectionMembership(sourceId, included) {
  const session = activeSession();
  const set = new Set(session.exportSourceIds);
  if (included) {
    set.add(sourceId);
  } else {
    set.delete(sourceId);
  }
  session.exportSourceIds = Array.from(set);
  persistSessions();
}

async function globalSearch() {
  const query = els.globalSearchInput.value.trim();
  if (!query) {
    return;
  }
  beginBusy("Refreshing session state");
  try {
    const result = await api(`/v1/search/global?q=${encodeURIComponent(query)}&limit=20`);
    els.globalSearchResults.innerHTML = "";
    (result.data.items || []).forEach((item) => {
      const li = document.createElement("li");
      li.textContent = `${item.result_type}: ${item.label}`;
      els.globalSearchResults.appendChild(li);
    });
  } finally {
    endBusy();
  }
}

async function lookupRun() {
  const runId = els.runLookupInput.value.trim();
  if (!runId) {
    return;
  }
  beginBusy("Refreshing session state");
  try {
    const result = await api(`/v1/discovery/runs/${encodeURIComponent(runId)}`);
    els.runLookupResult.textContent = JSON.stringify(result.data, null, 2);
  } finally {
    endBusy();
  }
}

async function saveProviderSettings() {
  const openalexSearchLimit = Number(els.openalexLimitInput.value || "25");
  const braveSearchCount = Number(els.braveCountInput.value || "20");
  const braveRequireAllowlist = Boolean(els.braveAllowlistCheckbox.checked);
  beginBusy("Saving provider settings");
  try {
    await api("/v1/settings/providers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        openalex_search_limit: openalexSearchLimit,
        brave_search_count: braveSearchCount,
        brave_require_allowlist: braveRequireAllowlist,
      }),
    });
    els.providerSettingsState.textContent = "Provider settings saved.";
  } catch (error) {
    els.providerSettingsState.textContent = error instanceof Error ? error.message : String(error);
  } finally {
    endBusy();
  }
}

async function loadAiSettings() {
  try {
    const result = await api("/v1/settings/ai-filter");
    const data = result.data || {};
    state.aiSettings = data;
    if (els.aiModelSelect) {
      const model = AI_MODEL_PRESETS.includes(data.ai_model) ? data.ai_model : AI_MODEL_PRESETS[0];
      els.aiModelSelect.value = model;
      state.currentGlobalSummaryModel = String(data.ai_model || model || "");
      if (els.libraryCurrentSummaryModel) {
        els.libraryCurrentSummaryModel.textContent = state.currentGlobalSummaryModel || "-";
      }
      if (els.librarySummaryCurrentModelValue) {
        els.librarySummaryCurrentModelValue.textContent = state.currentGlobalSummaryModel || "-";
      }
      if (data.ai_model && !AI_MODEL_PRESETS.includes(data.ai_model)) {
        els.aiSettingsState.textContent = `Current model '${data.ai_model}' is custom. Choose a supported preset to replace it.`;
      } else {
        els.aiSettingsState.textContent = `AI settings loaded. Current model for summaries and AI tasks: ${data.ai_model || model}.`;
      }
    }
  } catch {
    if (els.aiSettingsState) {
      els.aiSettingsState.textContent = "Unable to load AI settings.";
    }
  }
}

async function saveAiSettings() {
  const model = els.aiModelSelect?.value || AI_MODEL_PRESETS[0];
  beginBusy("Saving AI settings");
  try {
    const result = await api("/v1/settings/ai-filter", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ai_model: model }),
    });
    state.aiSettings = result.data || null;
    state.currentGlobalSummaryModel = String(result.data?.ai_model || model);
    if (els.libraryCurrentSummaryModel) {
      els.libraryCurrentSummaryModel.textContent = state.currentGlobalSummaryModel || "-";
    }
    if (els.librarySummaryCurrentModelValue) {
      els.librarySummaryCurrentModelValue.textContent = state.currentGlobalSummaryModel || "-";
    }
    els.aiSettingsState.textContent = `AI model saved for summaries and AI tasks: ${result.data?.ai_model || model}.`;
    await loadSystemStatus();
  } finally {
    endBusy();
  }
}

async function loadAdvancedDatabaseBackups() {
  try {
    const result = await api("/v1/advanced/database-backups");
    const data = result.data || {};
    state.advancedDatabaseBackups = Array.isArray(data.items) ? data.items : [];
    state.advancedDatabaseTarget = String(data.database_target || "");
    state.advancedDatabaseBackupDir = String(data.backup_dir || "");
    state.advancedDatabaseRetentionCount = Number(data.retention_count || 0);
    els.databaseRestoreState.textContent = `Loaded ${Number(data.total || 0)} backup candidate(s).`;
    renderAdvancedDatabaseBackups();
  } catch (error) {
    els.databaseRestoreState.textContent = `Unable to load database backups: ${errorDetail(error)}`;
    state.advancedDatabaseBackups = [];
    state.advancedDatabaseTarget = "";
    state.advancedDatabaseBackupDir = "";
    state.advancedDatabaseRetentionCount = 0;
    renderAdvancedDatabaseBackups();
  }
}

async function createAdvancedDatabaseBackup() {
  beginBusy("Creating database backup");
  try {
    const result = await api("/v1/advanced/database-backups", { method: "POST" });
    const data = result.data || {};
    const backupName = String(data.backup?.name || "");
    const pruned = Number(data.pruned_auto_backups || 0);
    els.databaseRestoreState.textContent = pruned > 0
      ? `Created backup ${backupName}. Pruned ${pruned} automatic backup(s).`
      : `Created backup ${backupName}.`;
    await loadAdvancedDatabaseBackups();
  } catch (error) {
    els.databaseRestoreState.textContent = `Backup failed: ${errorDetail(error)}`;
  } finally {
    endBusy();
  }
}

async function restoreAdvancedDatabaseBackup() {
  const backupName = String(els.databaseBackupSelect?.value || "").trim();
  const confirmName = String(els.databaseRestoreConfirmInput?.value || "").trim();
  if (!backupName) {
    els.databaseRestoreState.textContent = "Select a backup file first.";
    return;
  }
  if (!confirmName) {
    els.databaseRestoreState.textContent = "Type the backup file name to confirm restore.";
    return;
  }
  beginBusy("Restoring database");
  try {
    const result = await api("/v1/advanced/database-restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        backup_name: backupName,
        confirm_backup_name: confirmName,
      }),
    });
    const data = result.data || {};
    els.databaseRestoreConfirmInput.value = "";
    els.databaseRestoreState.textContent = `Restored ${data.restored_backup_name || backupName}. Snapshot: ${data.snapshot_path || "-"}.`;
    await syncSessionsFromServer();
    await refreshAll();
    await loadAdvancedDatabaseBackups();
  } catch (error) {
    els.databaseRestoreState.textContent = `Restore failed: ${errorDetail(error)}`;
  } finally {
    endBusy();
  }
}

async function loadAdvancedOperationalEvents() {
  if (state.advancedEventsPaused) {
    els.advancedEventsState.textContent = "Operational event polling paused.";
    renderAdvancedOperationalEvents();
    return;
  }
  try {
    const result = await api("/v1/advanced/operational-events?limit=60");
    const data = result.data || {};
    state.advancedEventRows = Array.isArray(data.items) ? data.items : [];
    state.advancedEventGroupedCounts = Array.isArray(data.grouped_counts) ? data.grouped_counts : [];
    const logPath = data.log_path ? ` Log: ${data.log_path}` : "";
    els.advancedEventsState.textContent = `Loaded ${data.total || 0} operational event(s).${logPath}`;
    renderAdvancedOperationalEvents();
  } catch (error) {
    els.advancedEventsState.textContent = `Unable to load operational events: ${errorDetail(error)}`;
  }
}

async function pollServerHealth() {
  try {
    const response = await fetch("/healthz", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`healthz_${response.status}`);
    }
    state.healthFailureCount = 0;
    clearOfflineState();
  } catch (error) {
    state.healthFailureCount += 1;
    if (state.healthFailureCount >= OFFLINE_FAILURE_THRESHOLD) {
      setOfflineState("server offline");
    }
  }
}

function startAdvancedEventPolling() {
  if (state.advancedEventPollTimer) {
    window.clearInterval(state.advancedEventPollTimer);
  }
  state.advancedEventPollTimer = window.setInterval(() => {
    if (state.activePage === "advanced" && !state.advancedEventsPaused) {
      loadAdvancedOperationalEvents();
    }
  }, 5000);
}

function startHealthPolling() {
  if (state.healthPollTimer) {
    window.clearInterval(state.healthPollTimer);
  }
  state.healthPollTimer = window.setInterval(() => {
    pollServerHealth();
  }, OFFLINE_HEALTH_POLL_MS);
  pollServerHealth();
}

function stopDiscoverPolling() {
  if (state.discoverPollTimer) {
    window.clearInterval(state.discoverPollTimer);
    state.discoverPollTimer = null;
  }
}

function startDiscoverPolling() {
  stopDiscoverPolling();
  state.discoverPollTimer = window.setInterval(async () => {
    const session = activeSession();
    if (!session?.discoveryRunId || state.serverOffline) {
      return;
    }
    if (state.currentDiscoveryStatus?.stage_status !== "running") {
      stopDiscoverPolling();
      return;
    }
    try {
      await loadDiscover();
    } catch {
      // Best-effort live polling; manual refresh or later polls can recover.
    }
  }, 3000);
}

function connectLiveUpdates() {
  if (state.eventSource) {
    state.eventSource.close();
  }
  const tokenParam = state.authEnabled && state.token ? `?api_key=${encodeURIComponent(state.token)}` : "";
  state.eventSource = new EventSource(`/v1/events/stream${tokenParam}`);
  state.eventSource.addEventListener("open", () => {
    clearOfflineState();
  });
  state.eventSource.onerror = () => {
    setOfflineState("live updates disconnected");
  };
  state.eventSource.addEventListener("run_started", async (event) => {
    const payload = JSON.parse(event.data || "{}");
    if (payload.phase === "discovery") {
      state.busyLabel = "Searching providers";
      renderActivity();
      scheduleLiveDiscoverRefresh(payload);
    }
  });
  state.eventSource.addEventListener("run_progress", async (event) => {
    const payload = JSON.parse(event.data || "{}");
    if (payload.phase === "discovery") {
      state.busyLabel = "Searching providers";
      setProgress(50, "Running");
      renderActivity();
      scheduleLiveDiscoverRefresh(payload);
    }
  });
  state.eventSource.addEventListener("run_completed", async () => {
    setProgress(100, "Done");
    await refreshAll();
  });
  state.eventSource.addEventListener("queue_updated", async (event) => {
    const payload = JSON.parse(event.data || "{}");
    els.reviewBadge.textContent = String(payload.pending_review || 0);
    if (state.activePage === "review") {
      await loadReview();
    } else if (state.activePage === "documents") {
      await loadDocuments();
    } else if (state.activePage === "library") {
      await loadLibrary();
    }
  });
}

function scheduleLiveDiscoverRefresh(payload) {
  const session = activeSession();
  const liveRunId = payload.latest_discovery || "";
  if (!session?.discoveryRunId || !liveRunId || session.discoveryRunId !== liveRunId) {
    return;
  }
  if (state.liveRefreshTimer) {
    return;
  }
  state.liveRefreshTimer = window.setTimeout(async () => {
    state.liveRefreshTimer = null;
    try {
      await loadDiscover();
    } catch {
      // Keep live refresh best-effort; the next event or manual refresh will recover.
    }
  }, 700);
}

async function refreshAll() {
  beginBusy("Refreshing session state");
  try {
    await loadBookmarks();
    await loadSessionProfile(activeSession()?.id || "");
    await loadLatestIds();
    try {
      await ensureBoundDiscoveryRun();
    } catch {
      // Keep refresh best-effort; pane loaders will surface actionable state.
    }
    await loadSystemStatus();
    await loadAiSettings();
    await loadProviderSettings();
    try {
      await loadDiscover();
    } catch (error) {
      els.discoverState.textContent = `Unable to load discover data: ${errorDetail(error)}`;
    }
    if (state.activePage === "review") {
      try {
        await loadReview();
      } catch (error) {
        els.reviewState.textContent = `Unable to load review queue: ${errorDetail(error)}`;
      }
    }
    if (state.activePage === "documents") {
      try {
        await loadDocuments();
      } catch (error) {
        els.documentsState.textContent = `Unable to load documents: ${errorDetail(error)}`;
      }
    }
    if (state.activePage === "library") {
      try {
        await loadLibrary();
      } catch (error) {
        els.libraryState.textContent = `Unable to load library: ${errorDetail(error)}`;
      }
    }
    if (state.activePage === "advanced") {
      await loadAdvancedDatabaseBackups();
      await loadAdvancedOperationalEvents();
    }
  } finally {
    endBusy();
  }
}

async function refreshDiscoverSessionState() {
  try {
    await loadLatestIds();
    try {
      await ensureBoundDiscoveryRun();
    } catch {
      // Keep refresh best-effort; discover loader will surface actionable state.
    }
    await loadDiscover();
  } catch (error) {
    els.discoverState.textContent = `Unable to load discover data: ${errorDetail(error)}`;
  }
}

function handleKeyboard(event) {
  if (state.fileMenuOpen && event.key === "Escape") {
    state.fileMenuOpen = false;
    state.showNewSessionForm = false;
    renderShell();
    return;
  }
  if (state.activePage !== "review") {
    return;
  }
  if (event.target && ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) {
    return;
  }
  if (event.key === "ArrowDown" && state.reviewIndex < state.reviewItems.length - 1) {
    state.reviewIndex += 1;
    renderReviewRows();
  } else if (event.key === "ArrowUp" && state.reviewIndex > 0) {
    state.reviewIndex -= 1;
    renderReviewRows();
  } else if (event.key.toLowerCase() === "a") {
    submitReviewDecision("accept");
  } else if (event.key.toLowerCase() === "r") {
    submitReviewDecision("reject");
  } else if (event.key.toLowerCase() === "l") {
    submitReviewDecision("later");
  }
}

function wireEvents() {
  els.navButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      state.activePage = button.dataset.page;
      renderShell();
      if (state.activePage === "review") {
        await loadReview();
      }
      if (state.activePage === "documents") {
        await loadDocuments();
      }
      if (state.activePage === "library") {
        await loadLibrary();
      }
      if (state.activePage === "advanced") {
        await loadAdvancedDatabaseBackups();
        await loadAdvancedOperationalEvents();
      }
    });
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      stopDiscoverPolling();
      stopSummaryPoll();
      return;
    }
    refreshAll().catch(() => {
      // Best effort visibility recovery.
    });
  });

  els.fileMenuBtn?.addEventListener("click", () => {
    state.fileMenuOpen = !state.fileMenuOpen;
    renderShell();
  });
  els.newSessionBtn.addEventListener("click", () => {
    state.showNewSessionForm = true;
    if (!state.newSessionDraftName) {
      state.newSessionDraftName = "";
    }
    if (!state.newSessionDraftContext) {
      state.newSessionDraftContext = "";
    }
    if (els.newSessionFormState) {
      els.newSessionFormState.textContent = "";
    }
    renderShell();
    els.newSessionNameInput?.focus();
  });
  els.createSessionConfirmBtn?.addEventListener("click", async () => {
    const name = String(els.newSessionNameInput?.value || "").trim();
    const context = String(els.newSessionContextInput?.value || "");
    if (!name) {
      els.newSessionFormState.textContent = "Session name is required.";
      return;
    }
    if (normalizeSessionContext(context).length > SESSION_CONTEXT_MAX) {
      els.newSessionFormState.textContent = `Session context must be <= ${SESSION_CONTEXT_MAX} characters.`;
      return;
    }
    const session = normalizeSession({
      name,
      sessionContext: context,
      sessionContextUpdatedAt: "",
      savedSessionContext: "",
      savedSessionContextUpdatedAt: "",
    });
    els.newSessionFormState.textContent = "Creating session...";
    try {
      const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          session_context: context,
        }),
      });
      const profile = result.data || {};
      session.name = typeof profile.name === "string" && profile.name.trim() ? profile.name.trim() : name;
      session.sessionContext = String(profile.session_context || "");
      session.sessionContextUpdatedAt = String(profile.updated_at || "");
      session.savedSessionContext = session.sessionContext;
      session.savedSessionContextUpdatedAt = session.sessionContextUpdatedAt;
      state.sessions.push(session);
      state.activeSessionId = session.id;
      state.pendingSessionId = session.id;
      resetReviewSort();
      persistSessions();
      renderSessions();
      renderShell();
      resetSessionBoundPaneState();
      els.sessionContextState.textContent = defaultSessionContextState(session);
      els.sessionState.textContent = `Created new session: ${session.name}`;
      state.fileMenuOpen = false;
      state.showNewSessionForm = false;
      resetNewSessionDraft();
      if (els.newSessionFormState) {
        els.newSessionFormState.textContent = "";
      }
      renderShell();
    } catch (error) {
      els.newSessionFormState.textContent = `Unable to create session: ${errorDetail(error)}`;
    }
  });
  els.cancelNewSessionBtn?.addEventListener("click", () => {
    state.showNewSessionForm = false;
    resetNewSessionDraft();
    if (els.newSessionFormState) {
      els.newSessionFormState.textContent = "";
    }
    renderShell();
  });
  els.deleteSessionBtn.addEventListener("click", async () => {
    if (state.sessions.length === 1) {
      return;
    }
    state.sessions = state.sessions.filter((session) => session.id !== state.activeSessionId);
    state.activeSessionId = state.sessions[0].id;
    state.pendingSessionId = state.activeSessionId;
    resetReviewSort();
    persistSessions();
    renderSessions();
    resetSessionBoundPaneState();
    await refreshAll();
    els.sessionState.textContent = `Deleted session. Active: ${activeSession().name}`;
    state.fileMenuOpen = false;
    renderShell();
  });
  els.stopRunningBtn.addEventListener("click", stopRunningTask);
  els.sessionSelect.addEventListener("change", async () => {
    const nextId = els.sessionSelect.value;
    const pending = state.sessions.find((session) => session.id === nextId);
    if (!pending || nextId === state.activeSessionId) {
      state.pendingSessionId = state.activeSessionId;
      renderSessions();
      return;
    }
    const message = hasUnsavedSessionContext()
      ? "Current session has unsaved context changes that exist only in this browser. Switch session?"
      : `Switch to session "${pending.name}"?`;
    if (!window.confirm(message)) {
      state.pendingSessionId = state.activeSessionId;
      renderSessions();
      return;
    }
    await switchActiveSession(nextId);
  });
  els.documentsPrevBtn?.addEventListener("click", () => {
    if (state.documentsPage <= 0) {
      return;
    }
    state.documentsPage -= 1;
    renderDocuments();
  });
  els.documentsNextBtn?.addEventListener("click", () => {
    state.documentsPage += 1;
    renderDocuments();
  });
  els.sessionContextInput.addEventListener("input", () => {
    const session = activeSession();
    session.sessionContext = els.sessionContextInput.value;
    state.suggestionStateSticky = false;
    els.sessionContextCounter.textContent = `${normalizeSessionContext(session.sessionContext).length} / ${SESSION_CONTEXT_MAX}`;
    persistSessions();
    updateQuerySelectionState();
    els.sessionContextState.textContent = defaultSessionContextState(session);
    updateSessionContextControls(session);
    updateSuggestionAvailability();
  });
  els.newSessionNameInput?.addEventListener("input", () => {
    state.newSessionDraftName = els.newSessionNameInput.value;
  });
  els.newSessionContextInput?.addEventListener("input", () => {
    state.newSessionDraftContext = els.newSessionContextInput.value;
  });
  ["discoverOpenalexLimitInput", "discoverSemanticScholarLimitInput", "discoverBraveLimitInput"].forEach((id) => {
    els[id].addEventListener("input", updateSessionProviderLimits);
    els[id].addEventListener("change", updateSessionProviderLimits);
  });
  els.saveSessionContextBtn.addEventListener("click", async () => {
    await saveSessionContext();
  });
  els.addQueryBtn.addEventListener("click", () => {
    const value = els.discoverQueryInput.value.trim();
    if (!value) {
      return;
    }
    const session = activeSession();
    if (!session.queries.some((query) => query.text.toLowerCase() === value.toLowerCase())) {
      session.queries.push({ id: `query_${Math.random().toString(36).slice(2, 10)}`, text: value, selected: true });
      persistSessions();
      renderSessions();
      els.discoverSuggestionsState.textContent = `Added manual query: ${value}`;
    }
    els.discoverQueryInput.value = "";
  });
  els.generateQuerySuggestionsBtn.addEventListener("click", generateQuerySuggestions);
  els.runDiscoveryBtn.addEventListener("click", createDiscoveryRun);
  els.runNextCitationBtn.addEventListener("click", async () => {
    try {
      await createNextCitationIteration();
    } catch (error) {
      console.error("runNextCitationBtn failed", error);
      els.discoverState.textContent = citationIterationErrorText(error);
    }
  });
  els.resumeCitationBtn.addEventListener("click", async () => {
    try {
      await resumeCitationIteration();
    } catch (error) {
      console.error("resumeCitationBtn failed", error);
      els.discoverState.textContent = citationIterationErrorText(error);
    }
  });
  els.reviewAcceptBtn.addEventListener("click", () => submitReviewDecision("accept"));
  els.reviewRejectBtn.addEventListener("click", () => submitReviewDecision("reject"));
  els.reviewLaterBtn.addEventListener("click", () => submitReviewDecision("later"));
  els.reviewBookmarkBtn?.addEventListener("click", async () => {
    const item = state.reviewItems[state.reviewIndex];
    if (!item) {
      return;
    }
    const bookmarked = await toggleBookmarkForSource(item.id);
    els.reviewState.textContent = bookmarked ? "Paper bookmarked for later research." : "Bookmark removed.";
    renderReviewRows();
    renderDocuments();
    renderLibraryRows();
  });
  els.reviewCopyAbstractBtn?.addEventListener("click", async () => {
    const item = state.reviewItems[state.reviewIndex];
    const text = String(item?.abstract || "").trim();
    if (!text) {
      els.reviewState.textContent = "No abstract available to copy.";
      return;
    }
    const ok = await copyTextToClipboard(text);
    els.reviewState.textContent = ok ? `Copied abstract: ${item.title}` : "Unable to copy abstract.";
  });
  els.reviewFilterButtons.forEach((button) => {
    button.addEventListener("click", async () => {
      const next = button.dataset.reviewFilter || "pending";
      if (state.reviewQueue === next) {
        return;
      }
      state.reviewQueue = next;
      renderReviewFilterChips();
      beginBusy("Loading review queue");
      try {
        await loadReview();
      } catch (error) {
        els.reviewState.textContent = `Unable to load ${next} review items: ${errorDetail(error) || "request failed"}`;
      } finally {
        endBusy();
      }
    });
  });
  els.reviewSortButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.reviewSort || "lineage";
      if (state.reviewSort.key === key) {
        state.reviewSort.dir = state.reviewSort.dir === "desc" ? "asc" : "desc";
      } else {
        state.reviewSort = { key, dir: defaultReviewSortDir(key) };
      }
      renderReviewRows();
    });
  });
  els.documentsSortButtons?.forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.documentsSort || "lineage";
      if (state.documentsSort.key === key) {
        state.documentsSort.dir = state.documentsSort.dir === "asc" ? "desc" : "asc";
      } else {
        state.documentsSort = { key, dir: defaultDocumentsSortDir(key) };
      }
      renderDocuments();
    });
  });
  els.librarySortButtons?.forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.librarySort || "lineage";
      if (state.librarySort.key === key) {
        state.librarySort.dir = state.librarySort.dir === "asc" ? "desc" : "asc";
      } else {
        state.librarySort = { key, dir: defaultLibrarySortDir(key) };
      }
      renderLibraryRows();
    });
  });
  els.downloadMissingBtn.addEventListener("click", () => {
    const ids = pendingDocumentSourceIds();
    if (!ids.length) {
      els.documentsState.textContent = "No pending documents to download.";
      return;
    }
    startAcquisition(false, ids);
  });
  els.retryFailedBtn.addEventListener("click", () => {
    const ids = retryableDocumentSourceIds();
    if (!ids.length) {
      els.documentsState.textContent = "No failed or partial documents to retry.";
      return;
    }
    startAcquisition(true, ids);
  });
  els.documentsRowActionBtn.addEventListener("click", handleSelectedDocumentAction);
  els.documentsOpenPdfBtn?.addEventListener("click", async () => {
    const row = selectedDocumentRow();
    if (!row?.previewablePdfArtifactId) {
      return;
    }
    const result = await openPdfArtifactPreview(row.previewablePdfArtifactId);
    els.documentsState.textContent = pdfPreviewStatusMessage(result, row.title);
  });
  els.documentsBookmarkBtn?.addEventListener("click", async () => {
    const row = selectedDocumentRow();
    if (!row) {
      return;
    }
    const bookmarked = await toggleBookmarkForSource(row.source.id);
    els.documentsState.textContent = bookmarked ? "Paper bookmarked for later research." : "Bookmark removed.";
    renderDocuments();
    renderReviewRows();
    renderLibraryRows();
  });
  els.saveInternalRepoUrlBtn.addEventListener("click", () => {
    const raw = els.internalRepoUrlInput.value;
    if (raw.trim() && !normalizeHttpUrl(raw)) {
      els.internalRepoUrlState.textContent = "Repository URL must be a valid http/https URL.";
      return;
    }
    state.internalRepositoryBaseUrl = normalizeHttpUrl(raw);
    if (state.internalRepositoryBaseUrl) {
      localStorage.setItem(INTERNAL_REPO_URL_KEY, state.internalRepositoryBaseUrl);
      els.internalRepoUrlState.textContent = "Repository URL saved for this browser.";
    } else {
      localStorage.removeItem(INTERNAL_REPO_URL_KEY);
      els.internalRepoUrlState.textContent = "Repository URL cleared. Downloads will use the normal source chain only.";
    }
    renderShell();
  });
  els.batchUploadForm.addEventListener("submit", uploadBatchFiles);
  els.documentsExportCsvBtn.addEventListener("click", exportDocumentsCsv);
  els.libraryQuery.addEventListener("input", renderLibraryRows);
  els.libraryParsedOnlyCheckbox?.addEventListener("change", renderLibraryRows);
  els.libraryPdfOnlyCheckbox?.addEventListener("change", renderLibraryRows);
  els.librarySummaryCurrentOnlyCheckbox?.addEventListener("change", renderLibraryRows);
  els.libraryExportSize.addEventListener("change", renderLibraryRows);
  els.libraryGenerateVisibleSummariesBtn?.addEventListener("click", async () => {
    await queueSummaryGeneration(state.libraryFilteredRows.map((item) => item.id), false);
  });
  els.libraryGenerateVisibleTagsBtn?.addEventListener("click", async () => {
    state.libraryDetailTab = "tag_review";
    renderLibraryRows();
    await queueSessionTagCandidates(false);
  });
  els.libraryPromptToggleBtn?.addEventListener("click", () => {
    els.librarySummaryPromptPanel.hidden = !els.librarySummaryPromptPanel.hidden;
  });
  els.libraryToggleRawSummaryPromptBtn?.addEventListener("click", () => {
    state.summaryPromptRawMode = !state.summaryPromptRawMode;
    if (els.libraryRawSummaryPromptWrap) {
      els.libraryRawSummaryPromptWrap.hidden = !state.summaryPromptRawMode;
    }
    if (els.libraryToggleRawSummaryPromptBtn) {
      els.libraryToggleRawSummaryPromptBtn.textContent = state.summaryPromptRawMode ? "Hide Generated Prompt" : "Show Generated Prompt";
    }
  });
  els.libraryToggleRawTagPromptBtn?.addEventListener("click", () => {
    state.tagPromptRawMode = !state.tagPromptRawMode;
    if (els.libraryRawTagPromptWrap) {
      els.libraryRawTagPromptWrap.hidden = !state.tagPromptRawMode;
    }
    if (els.libraryToggleRawTagPromptBtn) {
      els.libraryToggleRawTagPromptBtn.textContent = state.tagPromptRawMode ? "Hide Generated Prompt" : "Show Generated Prompt";
    }
  });
  els.libraryAddSummaryFieldBtn?.addEventListener("click", () => {
    state.sessionSummaryEditorConfig = collectSummaryEditorConfigFromForm();
    const nextIndex = (state.sessionSummaryEditorConfig?.schema_fields?.length || 0) + 1;
    state.sessionSummaryEditorConfig.schema_fields.push({
      id: `custom_field_${nextIndex}`,
      path: `custom.field_${nextIndex}`,
      label: `Custom Field ${nextIndex}`,
      description: "",
      field_type: "string",
      enabled: true,
      object_item_fields: [],
    });
    updateSummaryPromptBuilderView();
  });
  els.libraryAddSummaryControlledValueBtn?.addEventListener("click", () => {
    state.sessionSummaryEditorConfig = collectSummaryEditorConfigFromForm();
    state.sessionSummaryEditorConfig.controlled_values.push({
      field_path: "",
      allowed_values: [],
      fallback_policy: "allow_free_text",
    });
    updateSummaryPromptBuilderView();
  });
  els.librarySummaryFieldsList?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-summary-field-remove]");
    if (!button) {
      return;
    }
    const index = Number.parseInt(button.getAttribute("data-summary-field-remove") || "-1", 10);
    if (index < 0) {
      return;
    }
    state.sessionSummaryEditorConfig = collectSummaryEditorConfigFromForm();
    state.sessionSummaryEditorConfig.schema_fields.splice(index, 1);
    updateSummaryPromptBuilderView();
  });
  els.librarySummaryFieldsList?.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLSelectElement)) {
      return;
    }
    const index = Number.parseInt(target.getAttribute("data-summary-field-type") || "-1", 10);
    if (index < 0) {
      return;
    }
    const itemsInput = els.librarySummaryFieldsList?.querySelector(`[data-summary-field-items="${index}"]`);
    if (itemsInput instanceof HTMLInputElement) {
      itemsInput.disabled = target.value !== "object_list";
      if (target.value !== "object_list") {
        itemsInput.value = "";
      }
    }
  });
  els.librarySummaryControlledValuesList?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-summary-controlled-remove]");
    if (!button) {
      return;
    }
    const index = Number.parseInt(button.getAttribute("data-summary-controlled-remove") || "-1", 10);
    if (index < 0) {
      return;
    }
    state.sessionSummaryEditorConfig = collectSummaryEditorConfigFromForm();
    state.sessionSummaryEditorConfig.controlled_values.splice(index, 1);
    updateSummaryPromptBuilderView();
  });
  els.libraryApprovedTagsToggleBtn?.addEventListener("click", () => {
    els.libraryApprovedTagsPanel.hidden = !els.libraryApprovedTagsPanel.hidden;
  });
  els.librarySaveSummaryPromptBtn?.addEventListener("click", saveSummaryPrompt);
  els.libraryResetSummaryPromptBtn?.addEventListener("click", resetSummaryPrompt);
  els.libraryAddTagSpecCategoryBtn?.addEventListener("click", () => {
    const config = state.sessionTagSpec?.category_config || cloneTagSpecConfig();
    const nextIndex = (config.categories?.length || 0) + 1;
    config.categories.push({
      key: `custom_category_${nextIndex}`,
      label: `Custom Category ${nextIndex}`,
      guidance: "",
      allowed_tags: [],
      allow_free_text: true,
    });
    state.sessionTagSpec = {
      ...(state.sessionTagSpec || {}),
      category_config: config,
      prompt_template: state.sessionTagSpec?.prompt_template || "",
    };
    renderTagSpecEditor();
  });
  els.libraryTagSpecCategories?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-tag-spec-remove]");
    if (!button) {
      return;
    }
    const index = Number.parseInt(button.getAttribute("data-tag-spec-remove") || "-1", 10);
    if (index < 0) {
      return;
    }
    const config = collectTagSpecFromForm().category_config;
    config.categories.splice(index, 1);
    state.sessionTagSpec = {
      ...(state.sessionTagSpec || {}),
      category_config: config,
      prompt_template: state.sessionTagSpec?.prompt_template || "",
    };
    renderTagSpecEditor();
  });
  els.librarySaveTagSpecBtn?.addEventListener("click", saveTagSpec);
  els.libraryResetTagSpecBtn?.addEventListener("click", resetTagSpec);
  els.librarySaveApprovedTagsBtn?.addEventListener("click", saveApprovedTags);
  els.libraryAddBtn.addEventListener("click", () => {
    if (state.selectedLibrarySourceId) {
      updateSelectionMembership(state.selectedLibrarySourceId, true);
    }
  });
  els.libraryRemoveBtn.addEventListener("click", () => {
    if (state.selectedLibrarySourceId) {
      updateSelectionMembership(state.selectedLibrarySourceId, false);
    }
  });
  els.libraryBookmarkBtn?.addEventListener("click", async () => {
    if (!state.selectedLibrarySourceId) {
      return;
    }
    const bookmarked = await toggleBookmarkForSource(state.selectedLibrarySourceId);
    els.libraryState.textContent = bookmarked ? "Paper bookmarked for later research." : "Bookmark removed.";
    renderLibraryRows();
    renderReviewRows();
    renderDocuments();
  });
  els.libraryAddFreeformTagBtn?.addEventListener("click", async () => {
    if (!state.selectedLibrarySourceId) {
      return;
    }
    const nextTag = String(els.libraryFreeformTagInput?.value || "").trim();
    if (!nextTag) {
      return;
    }
    const annotation = annotationForSource(state.selectedLibrarySourceId);
    const nextGroups = JSON.parse(JSON.stringify(annotation.freeform_tags_by_category || {}));
    nextGroups.uncategorized_tags = (nextGroups.uncategorized_tags || []).concat([nextTag]);
    await saveAnnotation(state.selectedLibrarySourceId, {
      freeform_tags_by_category: nextGroups,
      approved_tags_by_category: annotation.approved_tags_by_category || {},
    });
    els.libraryFreeformTagInput.value = "";
    els.libraryState.textContent = `Added tag: ${nextTag}`;
  });
  els.libraryAddApprovedTagBtn?.addEventListener("click", async () => {
    if (!state.selectedLibrarySourceId) {
      return;
    }
    const nextTag = String(els.libraryApprovedTagSelect?.value || "").trim();
    if (!nextTag) {
      return;
    }
    const annotation = annotationForSource(state.selectedLibrarySourceId);
    const nextGroups = JSON.parse(JSON.stringify(annotation.approved_tags_by_category || {}));
    nextGroups.uncategorized_tags = (nextGroups.uncategorized_tags || []).concat([nextTag]);
    await saveAnnotation(state.selectedLibrarySourceId, {
      freeform_tags_by_category: annotation.freeform_tags_by_category || {},
      approved_tags_by_category: nextGroups,
    });
    els.libraryState.textContent = `Added approved tag: ${nextTag}`;
  });
  els.libraryGenerateSummaryBtn?.addEventListener("click", async () => {
    if (!state.selectedLibrarySourceId) {
      return;
    }
    await queueSummaryGeneration([state.selectedLibrarySourceId], false);
  });
  els.libraryRegenerateSummaryBtn?.addEventListener("click", async () => {
    if (!state.selectedLibrarySourceId) {
      return;
    }
    await queueSummaryGeneration([state.selectedLibrarySourceId], true);
  });
  els.librarySummaryPreviewGenerateBtn?.addEventListener("click", async () => {
    if (!state.selectedLibrarySourceId) {
      return;
    }
    await queueSummaryGeneration([state.selectedLibrarySourceId], false);
  });
  els.librarySummaryPreviewRegenerateBtn?.addEventListener("click", async () => {
    if (!state.selectedLibrarySourceId) {
      return;
    }
    await queueSummaryGeneration([state.selectedLibrarySourceId], true);
  });
  els.librarySummaryPreviewOpenPdfBtn?.addEventListener("click", async () => {
    const item = state.libraryFilteredRows.find((row) => row.id === state.selectedLibrarySourceId);
    if (!item?.previewable_pdf_artifact_id) {
      return;
    }
    const result = await openPdfArtifactPreview(item.previewable_pdf_artifact_id);
    setLibraryState(pdfPreviewStatusMessage(result, item.title), 5000);
  });
  els.libraryCopySummaryBtn?.addEventListener("click", async () => {
    if (!state.selectedLibrarySourceId) {
      return;
    }
    const summary = annotationForSource(state.selectedLibrarySourceId).ai_summary || "";
    const ok = await copyTextToClipboard(summary);
    setLibraryState(ok ? "Copied generated summary." : "Unable to copy generated summary.", 5000);
  });
  els.libraryDetailsTabBtn?.addEventListener("click", () => {
    state.libraryDetailTab = "details";
    renderLibraryRows();
  });
  els.librarySummaryPreviewTabBtn?.addEventListener("click", () => {
    state.libraryDetailTab = "summary_preview";
    renderLibraryRows();
  });
  els.libraryTagSpecTabBtn?.addEventListener("click", () => {
    state.libraryDetailTab = "tag_spec";
    renderLibraryRows();
  });
  els.libraryTagReviewTabBtn?.addEventListener("click", () => {
    state.libraryDetailTab = "tag_review";
    renderLibraryRows();
  });
  els.libraryGenerateCandidateTagsBtn?.addEventListener("click", async () => {
    await queueSessionTagCandidates(false);
  });
  els.libraryRegenerateCandidateTagsBtn?.addEventListener("click", async () => {
    await queueSessionTagCandidates(true);
  });
  els.libraryResetRejectedTagsBtn?.addEventListener("click", async () => {
    await resetRejectedTagCandidates();
  });
  els.libraryApplyApprovedTagsBtn?.addEventListener("click", async () => {
    await applyApprovedTags(false);
  });
  els.libraryReapplyApprovedTagsBtn?.addEventListener("click", async () => {
    await applyApprovedTags(true);
  });
  els.libraryApplyApprovedTagsOneBtn?.addEventListener("click", async () => {
    await applyApprovedTagsForSelectedPaper(false);
  });
  els.libraryReapplyApprovedTagsOneBtn?.addEventListener("click", async () => {
    await applyApprovedTagsForSelectedPaper(true);
  });
  els.libraryTagCandidatesList?.addEventListener("click", async (event) => {
    const approveButton = event.target.closest("[data-tag-candidate-approve]");
    if (approveButton) {
      await approveTagCandidate(approveButton.getAttribute("data-tag-candidate-approve") || "");
      setLibraryState("Candidate tag approved.", 5000);
      return;
    }
    const rejectButton = event.target.closest("[data-tag-candidate-reject]");
    if (rejectButton) {
      await rejectTagCandidate(rejectButton.getAttribute("data-tag-candidate-reject") || "");
      setLibraryState("Candidate tag rejected.", 5000);
    }
  });
  els.bookmarksCreateSessionBtn?.addEventListener("click", async () => {
    const bookmark = selectedBookmark();
    if (!bookmark) {
      return;
    }
    await createSessionFromBookmark(bookmark.id);
  });
  els.bookmarksRemoveBtn?.addEventListener("click", async () => {
    const bookmark = selectedBookmark();
    if (!bookmark) {
      return;
    }
    await removeBookmark(bookmark.id);
    await loadBookmarks();
  });
  els.libraryMetadataBtn.addEventListener("click", exportLibraryMetadata);
  els.libraryZipBtn.addEventListener("click", exportLibraryZip);
  els.saveApiKeyBtn.addEventListener("click", () => {
    state.token = els.apiKeyInput.value.trim();
    saveToken();
    els.apiKeyState.textContent = state.token ? "API key saved." : "API key cleared.";
    connectLiveUpdates();
    refreshAll();
  });
  els.saveAiSettingsBtn?.addEventListener("click", saveAiSettings);
  els.saveProviderSettingsBtn.addEventListener("click", saveProviderSettings);
  els.startParseBtn?.addEventListener("click", startParseForLatestAcquisition);
  els.createDatabaseBackupBtn?.addEventListener("click", createAdvancedDatabaseBackup);
  els.refreshDatabaseBackupsBtn?.addEventListener("click", loadAdvancedDatabaseBackups);
  els.restoreDatabaseBtn?.addEventListener("click", restoreAdvancedDatabaseBackup);
  els.advancedEventsPauseBtn.addEventListener("click", async () => {
    state.advancedEventsPaused = !state.advancedEventsPaused;
    renderAdvancedOperationalEvents();
    if (!state.advancedEventsPaused && state.activePage === "advanced") {
      await loadAdvancedOperationalEvents();
    }
  });
  els.advancedEventsAutoscrollBtn.addEventListener("click", () => {
    state.advancedEventsAutoscroll = !state.advancedEventsAutoscroll;
    renderAdvancedOperationalEvents();
  });
  els.globalSearchBtn.addEventListener("click", globalSearch);
  els.runLookupBtn.addEventListener("click", lookupRun);
  document.addEventListener("click", (event) => {
    if (!state.fileMenuOpen || !els.fileMenuPanel || !els.fileMenuBtn) {
      return;
    }
    const target = event.target;
    if (target instanceof Node && (els.fileMenuPanel.contains(target) || els.fileMenuBtn.contains(target))) {
      return;
    }
    state.fileMenuOpen = false;
    state.showNewSessionForm = false;
    renderShell();
  });
  document.addEventListener("keydown", handleKeyboard);
}

async function init() {
  readDom();
  loadSessions();
  renderSessions();
  renderShell();
  wireEvents();
  await syncSessionsFromServer();
  await refreshAll();
  connectLiveUpdates();
  startAdvancedEventPolling();
  startHealthPolling();
}

init().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  els.activityLine.textContent = `Startup error: ${message}`;
  els.activityIndicator.hidden = true;
});
