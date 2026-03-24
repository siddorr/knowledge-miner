const AUTH_STORAGE_KEY = "km_hmi2_api_key";
const SESSION_STORAGE_KEY = "km_hmi2_sessions";
const ACTIVE_SESSION_KEY = "km_hmi2_active_session";
const INTERNAL_REPO_URL_KEY = "km_hmi2_internal_repo_url";
const SESSION_CONTEXT_MAX = 4096;
const DEFAULT_PROVIDER_LIMITS = Object.freeze({ openalex: 25, semantic_scholar: 25, brave: 20 });
const AI_MODEL_PRESETS = Object.freeze(["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1"]);
const OFFLINE_HEALTH_POLL_MS = 4000;
const OFFLINE_FAILURE_THRESHOLD = 2;
const API_FETCH_PAGE_SIZE = 500;
const DOCUMENTS_PAGE_SIZE = 50;
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
  documentsSort: { key: "rank", dir: "asc" },
  documentsPage: 0,
  librarySort: { key: "rank", dir: "asc" },
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
  liveRefreshTimer: null,
  discoverPollTimer: null,
  internalRepositoryBaseUrl: localStorage.getItem(INTERNAL_REPO_URL_KEY) || "",
  aiSettings: null,
  advancedEventsPaused: false,
  advancedEventsAutoscroll: true,
  advancedEventRows: [],
  advancedEventGroupedCounts: [],
  advancedEventPollTimer: null,
  healthPollTimer: null,
  healthFailureCount: 0,
  serverOffline: false,
  offlineMessage: "",
  systemStatus: null,
  bookmarks: [],
  selectedBookmarkId: "",
  paperAnnotations: {},
  sessionApprovedTags: [],
  sessionSummaryPrompt: "",
  summaryPollTimer: null,
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
    "documentsDetailMetadata", "documentsRowActionBtn", "documentsBookmarkBtn", "internalRepoUrlInput", "saveInternalRepoUrlBtn", "internalRepoUrlState",
    "libraryMatches", "libraryHighest", "libraryLowest", "libraryQuery", "libraryExportSize", "libraryRows",
    "libraryTitle", "libraryAbstract", "libraryMetadata", "libraryAddBtn", "libraryRemoveBtn", "libraryBookmarkBtn", "libraryZipBtn",
    "libraryMetadataBtn", "libraryState", "libraryGenerateVisibleSummariesBtn", "libraryPromptToggleBtn", "libraryApprovedTagsToggleBtn",
    "librarySummaryPromptPanel", "librarySummaryPromptInput", "librarySaveSummaryPromptBtn", "librarySummaryPromptState",
    "libraryApprovedTagsPanel", "libraryApprovedTagsInput", "librarySaveApprovedTagsBtn", "libraryApprovedTagsState",
    "libraryFilterHelp", "libraryFreeformTags", "libraryFreeformTagInput", "libraryAddFreeformTagBtn",
    "libraryApprovedTags", "libraryApprovedTagSelect", "libraryAddApprovedTagBtn",
    "librarySummaryStatus", "librarySummaryText", "libraryGenerateSummaryBtn", "libraryRegenerateSummaryBtn",
    "apiKeyInput", "saveApiKeyBtn", "apiKeyState", "aiModelSelect", "saveAiSettingsBtn", "aiSettingsState", "latestDiscoveryId", "latestAcquisitionId", "latestParseId",
    "openalexLimitInput", "braveCountInput", "braveAllowlistCheckbox", "saveProviderSettingsBtn", "providerSettingsState",
    "globalSearchInput", "globalSearchBtn", "globalSearchResults", "runLookupInput", "runLookupBtn", "runLookupResult",
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
    const merged = new Map(state.sessions.map((session) => [session.id, normalizeSession(session)]));
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
  const available = Boolean(systemStatus.query_suggestions_available);
  let disabled = false;
  let message = "";
  if (!context) {
    disabled = true;
    message = "Session context is required before generating suggestions.";
  } else if (!available) {
    disabled = true;
    message = querySuggestionsUnavailableText(systemStatus.query_suggestions_reason);
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
    ai_summary: null,
    summary_status: "none",
    summary_generated_at: null,
    summary_error: null,
    can_generate_summary: false,
    summary_block_reason: "parsed_text_required",
  };
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

function errorDetail(error) {
  if (!(error instanceof Error)) {
    return String(error || "");
  }
  return error.message || "";
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
  const uploadButton = els.batchUploadForm?.querySelector('button[type="submit"]');
  if (uploadButton && state.serverOffline) {
    uploadButton.disabled = true;
  }
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
  if (key === "title") {
    return "asc";
  }
  return "desc";
}

function defaultDocumentsSortDir(key) {
  if (key === "rank" || key === "title" || key === "status" || key === "lineage") {
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
    const active = button.dataset.reviewSort === state.reviewSort.key;
    button.classList.toggle("active", active);
    const suffix = active ? (state.reviewSort.dir === "asc" ? " ▲" : " ▼") : "";
    const base = button.dataset.reviewSort === "lineage"
      ? "#"
      : button.dataset.reviewSort === "year"
          ? "Year"
          : button.dataset.reviewSort === "citation_count"
            ? "Cit"
            : "Score";
    button.textContent = `${base}${suffix}`;
  });
}

function renderDocumentsSortButtons() {
  if (!els.documentsSortButtons) {
    return;
  }
  const labels = {
    lineage: "#",
    rank: "Rank",
    score: "Score",
    year: "Year",
    citations: "Cit",
    title: "Title",
    status: "Status",
  };
  els.documentsSortButtons.forEach((button) => {
    const key = button.dataset.documentsSort;
    const active = key === state.documentsSort.key;
    button.classList.toggle("active", active);
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
    lineage: "#",
    rank: "Rank",
    relevance_score: "AI Score",
    year: "Year",
    citation_count: "Citations",
    title: "Title",
  };
  els.librarySortButtons.forEach((button) => {
    const key = button.dataset.librarySort;
    const active = key === state.librarySort.key;
    button.classList.toggle("active", active);
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
      }
      persistSessions();
      return;
    }
    if (state.activeSessionId === sessionId) {
      els.sessionContextState.textContent = `Unable to load context: ${detail}`;
    }
  }
}

async function saveSessionContext() {
  const session = activeSession();
  const context = normalizeSessionContext(els.sessionContextInput.value);
  if (!context) {
    els.sessionContextState.textContent = "Session context is required.";
    updateQuerySelectionState();
    return false;
  }
  if (context.length > SESSION_CONTEXT_MAX) {
    els.sessionContextState.textContent = `Session context must be <= ${SESSION_CONTEXT_MAX} characters.`;
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
    return true;
  } catch (error) {
    els.sessionContextState.textContent = `Unable to save context: ${errorDetail(error)}`;
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
  persistSessions();
}

async function loadSystemStatus() {
  try {
    const result = await api("/v1/system/status");
    const data = result.data;
    state.systemStatus = data;
    els.authStatus.textContent = `Auth: ${data.auth_mode}`;
    els.aiStatus.textContent = `AI: ${data.ai_filter_active ? "ready" : "inactive"}`;
    els.dbStatus.textContent = `DB: ${data.db_ready ? "ready" : "not ready"}`;
    els.footerSystem.textContent = `System: ${data.auth_mode}`;
    els.footerAi.textContent = `AI: ${data.ai_filter_active ? "ready" : "inactive"}`;
    els.footerDb.textContent = `DB: ${data.db_ready ? "ready" : "not ready"}`;
    els.footerUpdated.textContent = `Last update: ${new Date().toLocaleTimeString()}`;
    updateSuggestionAvailability();
  } catch {
    state.systemStatus = null;
    els.footerSystem.textContent = "System: error";
    els.footerUpdated.textContent = "Last update: error";
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
      `OpenAlex: ${item.openalex_count ?? 0}`,
      `Brave: ${item.brave_count ?? 0}`,
      `Semantic Scholar: ${item.semantic_scholar_count ?? 0}`,
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
  const disabled = acceptedCount <= 0 || remainingParentCount <= 0;
  els.runNextCitationBtn.disabled = disabled;
  if (acceptedCount <= 0) {
    els.discoverCitationHint.textContent = "Need at least 1 accepted paper before running citation expansion.";
    return;
  }
  els.discoverCitationHint.textContent = disabled
    ? "No new accepted papers are available for citation expansion."
    : `Citation expansion is available for ${remainingParentCount} accepted paper${remainingParentCount === 1 ? "" : "s"}.`;
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
  let allResult;
  let pendingResult;
  let rejectedResult;
  let queryResult;
  try {
    [runResult, queryResult] = await Promise.all([
      api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}`),
      api(`/v1/sessions/${encodeURIComponent(session.id)}/queries`),
    ]);
    [allResult, pendingResult, rejectedResult] = await Promise.all([
      fetchAllPages(async (offset, limit) => {
        const response = await api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/sources?status=all&limit=${limit}&offset=${offset}`);
        return response.data || {};
      }, API_FETCH_PAGE_SIZE),
      fetchAllPages(async (offset, limit) => {
        const response = await api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/sources?status=needs_review&limit=${limit}&offset=${offset}`);
        return response.data || {};
      }, API_FETCH_PAGE_SIZE),
      fetchAllPages(async (offset, limit) => {
        const response = await api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/sources?status=rejected&limit=${limit}&offset=${offset}`);
        return response.data || {};
      }, API_FETCH_PAGE_SIZE),
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
  const allItems = allResult || [];
  const pendingItems = pendingResult || [];
  const rejectedItems = rejectedResult || [];
  const approvedCount = allItems.filter((item) => item.accepted).length;
  const reviewedCount = allItems.length - pendingItems.length;
  const liveTotals = activeDiscoverQueryTotals(session);
  const citationScope = activeCitationScopeProgress(session);
  const liveDiscovered = Math.max(allItems.length, liveTotals.discovered);
  const liveApproved = Math.max(approvedCount, liveTotals.accepted);
  const liveRejected = Math.max(rejectedItems.length, liveTotals.rejected);
  const livePending = Math.max(pendingItems.length, liveTotals.pending);
  const liveReviewed = Math.max(reviewedCount, liveApproved + liveRejected);
  const activeRunNumber = activeRunQueries[0]?.run_number ?? "-";
  els.discoverIterationLine.textContent = `Run: ${activeRunNumber}`;
  els.discoverSummaryDiscovered.textContent = String(run.stage_status === "running" ? liveDiscovered : allItems.length);
  els.discoverSummaryApproved.textContent = String(run.stage_status === "running" ? liveApproved : approvedCount);
  els.discoverSummaryRejected.textContent = String(run.stage_status === "running" ? liveRejected : rejectedItems.length);
  els.discoverSummaryReviewed.textContent = String(run.stage_status === "running" ? liveReviewed : reviewedCount);
  els.discoverSummaryPending.textContent = String(run.stage_status === "running" ? livePending : pendingItems.length);
  if (run.stage_status === "running" && citationScope && citationScope.total > 0) {
    els.discoverState.textContent = `Citation expansion running. Found so far: ${liveDiscovered}. Parents processed: ${citationScope.processed}/${citationScope.total}.`;
  } else if (run.stage_status === "running" && resultsRunId(session) && resultsRunId(session) !== discoverRunId(session)) {
    els.discoverState.textContent = `New discovery run is in progress. Found so far: ${liveDiscovered}. Review/Documents/Library keep the accumulated session results.`;
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
  updateCitationAvailability(approvedCount, Number(run.citation_unexpanded_parent_count ?? 0));
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
    tr.innerHTML = `<td>${escapeHtml(item.review_status || "-")}</td><td>${escapeHtml(formatLineageNumber(item))}</td><td>${item.year || "-"}</td><td>${item.citation_count ?? "-"}</td><td>${Number(item.relevance_score || 0).toFixed(2)}</td><td>${isBookmarked(item.id) ? '<span class="bookmark-chip">B</span> ' : ""}${escapeHtml(item.title)}</td>`;
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

async function loadReview(recoverOnNotFound = true, selectionHint = null) {
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
      ? `/v1/discovery/runs/${encodeURIComponent(runId)}/sources?status=${encodeURIComponent(status)}&limit=200`
      : sessionSourcesPath(session.id, status, 200);
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
}

async function submitReviewDecision(decision) {
  const item = state.reviewItems[state.reviewIndex];
  if (!item) {
    return;
  }
  const selectionHint = decision === "reject" ? nextReviewSelectionHint() : null;
  beginBusy("Waiting for review");
  try {
    await api(`/v1/sources/${encodeURIComponent(item.id)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    await loadReview(true, selectionHint);
    await loadDiscover();
    await loadDocuments();
    await loadLibrary();
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
      acquisitionItem: item || null,
      status,
      title: source.title,
      year: source.year || "-",
      score: Number(source.relevance_score || 0).toFixed(2),
      citations: source.citation_count ?? "-",
    };
  });
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
    els.documentsRowActionBtn.textContent = "Download Selected";
    if (els.documentsBookmarkBtn) {
      els.documentsBookmarkBtn.textContent = "Bookmark";
      els.documentsBookmarkBtn.disabled = true;
    }
    return;
  }
  els.documentsDetailTitle.textContent = `${isBookmarked(row.source.id) ? "[Bookmarked] " : ""}${row.title}`;
  const detailBits = [`Status: ${row.status}`];
  if (row.acquisitionItem?.last_error) {
    detailBits.push(`Last error: ${row.acquisitionItem.last_error}`);
  }
  els.documentsDetailSummary.textContent = detailBits.join(" | ");
  els.documentsDetailMetadata.innerHTML = buildMetadataHtml(row.source);
  els.documentsRowActionBtn.disabled = false;
  if (row.status === "pending") {
    els.documentsRowActionBtn.textContent = "Download Selected";
  } else if (row.status === "failed" || row.status === "partial") {
    els.documentsRowActionBtn.textContent = "Retry Selected";
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
    tr.innerHTML = `<td>${escapeHtml(row.lineage)}</td><td>${row.rank}</td><td>${row.score}</td><td>${row.year}</td><td>${row.citations}</td><td>${isBookmarked(row.source.id) ? '<span class="bookmark-chip">B</span> ' : ""}<span>${escapeHtml(row.title)}</span> <button type="button" class="mini-copy-btn" title="Copy title" aria-label="Copy title">Copy</button></td><td>${doiCell}</td><td>${row.status}</td>`;
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
      }, API_FETCH_PAGE_SIZE);
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
    }, API_FETCH_PAGE_SIZE);
  } catch {
    latestItems = items;
  }
  state.currentAcquisitionStatus = statusData;
  const itemMap = new Map(latestItems.map((item) => [item.source_id, item]));
  state.documentRows = normalizeDocumentRows(accepted, itemMap);
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
  const params = new URLSearchParams();
  params.set("limit", String(Math.max(sourceIds.length, 1)));
  sourceIds.forEach((sourceId) => params.append("source_id", sourceId));
  const result = await api(`/v1/sessions/${encodeURIComponent(sessionId)}/annotations?${params.toString()}`);
  const items = Array.isArray(result.data?.items) ? result.data.items : [];
  state.paperAnnotations = Object.fromEntries(items.map((item) => [item.source_id, item]));
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

async function loadLibrarySummarySettings(sessionId) {
  const result = await api(`/v1/sessions/${encodeURIComponent(sessionId)}/summary-settings`);
  state.sessionSummaryPrompt = String(result.data?.prompt_template || "");
  if (els.librarySummaryPromptInput) {
    els.librarySummaryPromptInput.value = state.sessionSummaryPrompt;
  }
  if (els.librarySummaryPromptState) {
    els.librarySummaryPromptState.textContent = state.sessionSummaryPrompt
      ? "Session summary prompt loaded."
      : "Using the default summary prompt.";
  }
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
  const promptTemplate = String(els.librarySummaryPromptInput?.value || "").trim();
  if (!promptTemplate) {
    els.librarySummaryPromptState.textContent = "Summary prompt cannot be empty.";
    return;
  }
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/summary-settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt_template: promptTemplate }),
  });
  state.sessionSummaryPrompt = result.data?.prompt_template || promptTemplate;
  els.librarySummaryPromptState.textContent = "Summary prompt saved.";
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
  if (!state.activeSessionId) {
    return;
  }
  state.summaryPollTimer = window.setInterval(async () => {
    if (!Object.values(state.paperAnnotations).some((item) => item.summary_status === "queued" || item.summary_status === "running")) {
      stopSummaryPoll();
      return;
    }
    try {
      await loadLibraryAnnotations(activeSession().id, state.libraryRows.map((item) => item.id));
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
    els.libraryState.textContent = "No papers selected for summary generation.";
    return;
  }
  const result = await api(`/v1/sessions/${encodeURIComponent(session.id)}/summaries/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_ids: sourceIds, force_regenerate: forceRegenerate }),
  });
  const data = result.data || {};
  const blocked = Array.isArray(data.blocked) ? data.blocked : [];
  els.libraryState.textContent = `Summary generation queued: ${data.queued_count || 0}. Blocked: ${blocked.length}.`;
  await loadLibraryAnnotations(session.id, state.libraryRows.map((item) => item.id));
  renderLibraryRows();
  if ((data.queued_count || 0) > 0) {
    startSummaryPoll();
  }
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
    if (els.libraryGenerateSummaryBtn) {
      els.libraryGenerateSummaryBtn.disabled = true;
    }
    if (els.libraryRegenerateSummaryBtn) {
      els.libraryRegenerateSummaryBtn.disabled = true;
    }
    renderTagList(els.libraryFreeformTags, [], "freeform", async () => {});
    renderTagList(els.libraryApprovedTags, [], "approved", async () => {});
    refreshApprovedTagSelect();
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
  renderTagList(els.libraryFreeformTags, annotation.freeform_tags || [], "freeform", async (tag) => {
    const nextTags = (annotation.freeform_tags || []).filter((value) => value !== tag);
    await saveAnnotation(item.id, { freeform_tags: nextTags, approved_tags: annotation.approved_tags || [] });
    els.libraryState.textContent = `Removed tag: ${tag}`;
  });
  renderTagList(els.libraryApprovedTags, annotation.approved_tags || [], "approved", async (tag) => {
    const nextTags = (annotation.approved_tags || []).filter((value) => value !== tag);
    await saveAnnotation(item.id, { freeform_tags: annotation.freeform_tags || [], approved_tags: nextTags });
    els.libraryState.textContent = `Removed approved tag: ${tag}`;
  });
  refreshApprovedTagSelect();
  if (els.librarySummaryText) {
    els.librarySummaryText.textContent = annotation.ai_summary || "No summary generated for this paper yet.";
  }
  if (els.librarySummaryStatus) {
    const reason = annotation.summary_block_reason === "parsed_text_required"
      ? "Summary unavailable until the paper is downloaded and parsed."
      : "";
    const error = annotation.summary_error ? ` Error: ${annotation.summary_error}` : "";
    els.librarySummaryStatus.textContent = `Status: ${annotation.summary_status}.${reason ? ` ${reason}` : ""}${error}`;
  }
  if (els.libraryGenerateSummaryBtn) {
    els.libraryGenerateSummaryBtn.disabled = !annotation.can_generate_summary || annotation.summary_status === "completed";
  }
  if (els.libraryRegenerateSummaryBtn) {
    els.libraryRegenerateSummaryBtn.disabled = !annotation.can_generate_summary;
  }
}

function renderLibraryRows() {
  const query = els.libraryQuery.value.trim().toLowerCase();
  const filtered = !query
    ? [...state.libraryRows]
    : state.libraryRows.filter((item) => `${item.title} ${item.abstract || ""} ${tagSearchBlob(item.id)}`.toLowerCase().includes(query));
  state.libraryFilteredRows = sortedLibraryRows(filtered);
  renderLibrarySortButtons();
  els.libraryRows.innerHTML = "";
  state.libraryFilteredRows.forEach((item, index) => {
    const tr = document.createElement("tr");
    tr.classList.toggle("active", item.id === state.selectedLibrarySourceId);
    const annotation = annotationForSource(item.id);
    const tagMarker = (annotation.freeform_tags?.length || annotation.approved_tags?.length) ? '<span class="bookmark-chip">T</span> ' : "";
    tr.innerHTML = `<td>${escapeHtml(formatLineageNumber(item))}</td><td>${index + 1}</td><td>${Number(item.relevance_score || 0).toFixed(2)}</td><td>${item.year || "-"}</td><td>${item.citation_count ?? "-"}</td><td>${tagMarker}${isBookmarked(item.id) ? '<span class="bookmark-chip">B</span> ' : ""}<span>${escapeHtml(item.title)}</span> <button type="button" class="mini-copy-btn" title="Copy title" aria-label="Copy title">Copy</button></td>`;
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
  await loadLibraryAnnotations(session.id, state.libraryRows.map((item) => item.id));
  await loadLibraryTagCatalog(session.id);
  await loadLibrarySummarySettings(session.id);
  renderLibraryRows();
  els.libraryState.textContent = state.libraryRows.length ? "Library export data loaded." : "No accepted sources available.";
  if (Object.values(state.paperAnnotations).some((item) => item.summary_status === "queued" || item.summary_status === "running")) {
    startSummaryPoll();
  } else {
    stopSummaryPoll();
  }
}

async function createDiscoveryRun() {
  const session = activeSession();
  const queries = activeQueries(session);
  const context = normalizeSessionContext(session.sessionContext || els.sessionContextInput.value);
  const previousResultsRunId = resultsRunId(session);
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
    if (previousResultsRunId && previousResultsRunId !== result.data.run_id) {
      session.resultsRunId = previousResultsRunId;
      els.discoverState.textContent = "Discovery started. Review/Documents/Library keep the accumulated session results while the new run executes.";
    } else {
      session.resultsRunId = result.data.run_id;
    }
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
  const queries = activeQueries(session);
  const previousResultsRunId = resultsRunId(session);
  const providerLimits = normalizeProviderLimits(session.providerLimits);
  if (!session.discoveryRunId) {
    els.discoverState.textContent = "Run discovery before starting citation expansion.";
    return;
  }
  if (!queries.length) {
    els.discoverState.textContent = "Select at least one manual query for citation expansion.";
    return;
  }
  beginBusy("Running citation expansion");
  setProgress(10, "Queued");
  try {
    const result = await api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/next-citation-iteration`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ selected_queries: queries, provider_limits: providerLimits }),
    });
    session.discoveryRunId = result.data.run_id;
    if (previousResultsRunId && previousResultsRunId !== result.data.run_id) {
      session.resultsRunId = previousResultsRunId;
      els.discoverState.textContent = "Citation expansion started. Review/Documents/Library keep the accumulated session results while the new run executes.";
    } else {
      session.resultsRunId = result.data.run_id;
    }
    persistSessions();
    await refreshAll();
  } catch (error) {
    els.discoverState.textContent = error instanceof Error ? error.message : String(error);
  } finally {
    endBusy();
  }
}

async function resumeCitationIteration() {
  const session = activeSession();
  if (!session.discoveryRunId) {
    els.discoverState.textContent = "Run discovery first.";
    return;
  }
  beginBusy("Resuming citation expansion");
  setProgress(15, "Resuming");
  try {
    await api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/citation-expansion/resume`, {
      method: "POST",
    });
    await refreshAll();
  } catch (error) {
    els.discoverState.textContent = error instanceof Error ? error.message : String(error);
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
    await refreshAll();
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
      if (data.ai_model && !AI_MODEL_PRESETS.includes(data.ai_model)) {
        els.aiSettingsState.textContent = `Current model '${data.ai_model}' is custom. Choose a supported preset to replace it.`;
      } else {
        els.aiSettingsState.textContent = `AI settings loaded. Current model: ${data.ai_model || model}.`;
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
    els.aiSettingsState.textContent = `AI model saved: ${result.data?.ai_model || model}.`;
    await loadSystemStatus();
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
    await loadReview();
    await loadDocuments();
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
    try {
      await loadReview();
    } catch (error) {
      els.reviewState.textContent = `Unable to load review queue: ${errorDetail(error)}`;
    }
    try {
      await loadDocuments();
    } catch (error) {
      els.documentsState.textContent = `Unable to load documents: ${errorDetail(error)}`;
    }
    try {
      await loadLibrary();
    } catch (error) {
      els.libraryState.textContent = `Unable to load library: ${errorDetail(error)}`;
    }
    if (state.activePage === "advanced") {
      await loadAdvancedOperationalEvents();
    }
  } finally {
    endBusy();
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
        await loadAdvancedOperationalEvents();
      }
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
  els.createSessionConfirmBtn?.addEventListener("click", () => {
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
    renderShell();
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
  els.runNextCitationBtn.addEventListener("click", createNextCitationIteration);
  els.resumeCitationBtn.addEventListener("click", resumeCitationIteration);
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
    button.addEventListener("click", () => {
      const next = button.dataset.reviewFilter || "pending";
      if (state.reviewQueue === next) {
        return;
      }
      state.reviewQueue = next;
      renderReviewFilterChips();
      loadReview();
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
  els.libraryExportSize.addEventListener("change", renderLibraryRows);
  els.libraryGenerateVisibleSummariesBtn?.addEventListener("click", async () => {
    await queueSummaryGeneration(state.libraryFilteredRows.map((item) => item.id), false);
  });
  els.libraryPromptToggleBtn?.addEventListener("click", () => {
    els.librarySummaryPromptPanel.hidden = !els.librarySummaryPromptPanel.hidden;
  });
  els.libraryApprovedTagsToggleBtn?.addEventListener("click", () => {
    els.libraryApprovedTagsPanel.hidden = !els.libraryApprovedTagsPanel.hidden;
  });
  els.librarySaveSummaryPromptBtn?.addEventListener("click", saveSummaryPrompt);
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
    await saveAnnotation(state.selectedLibrarySourceId, {
      freeform_tags: (annotation.freeform_tags || []).concat([nextTag]),
      approved_tags: annotation.approved_tags || [],
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
    await saveAnnotation(state.selectedLibrarySourceId, {
      freeform_tags: annotation.freeform_tags || [],
      approved_tags: (annotation.approved_tags || []).concat([nextTag]),
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
