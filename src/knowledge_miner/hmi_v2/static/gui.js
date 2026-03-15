const AUTH_STORAGE_KEY = "km_hmi2_api_key";
const SESSION_STORAGE_KEY = "km_hmi2_sessions";
const ACTIVE_SESSION_KEY = "km_hmi2_active_session";
const INTERNAL_REPO_URL_KEY = "km_hmi2_internal_repo_url";
const SESSION_CONTEXT_MAX = 4096;
const DEFAULT_PROVIDER_LIMITS = Object.freeze({ openalex: 25, semantic_scholar: 25, brave: 20 });
const OFFLINE_HEALTH_POLL_MS = 4000;
const OFFLINE_FAILURE_THRESHOLD = 2;
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
  reviewQueue: "pending",
  reviewSort: { key: "iteration", dir: "desc" },
  latest: { discovery: "", acquisition: "", parse: "" },
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
  internalRepositoryBaseUrl: localStorage.getItem(INTERNAL_REPO_URL_KEY) || "",
  advancedEventsPaused: false,
  advancedEventsAutoscroll: true,
  advancedEventRows: [],
  advancedEventGroupedCounts: [],
  advancedEventPollTimer: null,
  healthPollTimer: null,
  healthFailureCount: 0,
  serverOffline: false,
  offlineMessage: "",
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

function readDom() {
  const ids = [
    "activityLine", "activityIndicator", "authStatus", "aiStatus", "dbStatus", "headerProgress", "headerProgressLabel",
    "newSessionBtn", "saveSessionBtn", "loadSessionBtn", "deleteSessionBtn", "stopRunningBtn", "sessionSelect", "sessionState",
    "discoverSessionName", "discoverQueryInput", "addQueryBtn", "generateQuerySuggestionsBtn", "runDiscoveryBtn", "runNextCitationBtn", "resumeCitationBtn", "discoverIterationLine",
    "discoverQueryList", "discoverSuggestedQueryList", "discoverSuggestionsState", "discoverSelectedCount", "discoverRunQueries", "discoverRunQueriesState", "discoverCitationHint",
    "discoverOpenalexLimitInput", "discoverSemanticScholarLimitInput", "discoverBraveLimitInput", "discoverProviderLimitsState",
    "discoverSummaryDiscovered", "discoverSummaryApproved", "discoverSummaryRejected", "discoverSummaryReviewed", "discoverSummaryPending", "discoverState",
    "sessionContextInput", "saveSessionContextBtn", "sessionContextCounter", "sessionContextState", "sessionContextUpdated",
    "reviewHeading", "reviewRows", "reviewTitle", "reviewAbstract", "reviewMetadata", "reviewSignals",
    "reviewAcceptBtn", "reviewRejectBtn", "reviewLaterBtn", "reviewState", "reviewBadge", "reviewQueueHelp", "reviewFilterChips",
    "documentsDownloaded", "documentsFailed", "documentsManual", "documentsPending", "documentsRows",
    "downloadMissingBtn", "retryFailedBtn", "documentsExportCsvBtn", "batchUploadForm", "batchUploadFiles",
    "batchUploadResults", "documentsState", "documentsBadge", "documentsDetailTitle", "documentsDetailSummary",
    "documentsDetailMetadata", "documentsRowActionBtn", "internalRepoUrlInput", "saveInternalRepoUrlBtn", "internalRepoUrlState",
    "libraryMatches", "libraryHighest", "libraryLowest", "libraryQuery", "libraryExportSize", "libraryRows",
    "libraryTitle", "libraryAbstract", "libraryMetadata", "libraryAddBtn", "libraryRemoveBtn", "libraryZipBtn",
    "libraryMetadataBtn", "libraryState",
    "apiKeyInput", "saveApiKeyBtn", "apiKeyState", "latestDiscoveryId", "latestAcquisitionId", "latestParseId",
    "openalexLimitInput", "braveCountInput", "braveAllowlistCheckbox", "saveProviderSettingsBtn", "providerSettingsState",
    "globalSearchInput", "globalSearchBtn", "globalSearchResults", "runLookupInput", "runLookupBtn", "runLookupResult",
    "advancedEventsPauseBtn", "advancedEventsAutoscrollBtn", "advancedEventsState", "advancedEventCounters", "advancedEventsLog",
    "footerSystem", "footerAi", "footerDb", "footerUpdated",
  ];
  for (const id of ids) {
    els[id] = $(id);
  }
  els.pages = {
    discover: $("page-discover"),
    review: $("page-review"),
    documents: $("page-documents"),
    library: $("page-library"),
    advanced: $("page-advanced"),
  };
  els.navButtons = Array.from(document.querySelectorAll(".nav-btn"));
  els.reviewFilterButtons = Array.from(document.querySelectorAll("[data-review-filter]"));
  els.reviewSortButtons = Array.from(document.querySelectorAll("[data-review-sort]"));
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
  return {
    id: raw?.id || `session_${Math.random().toString(36).slice(2, 10)}`,
    name: raw?.name || "New Session",
    queries: normalizedQueries,
    discoveryRunId: raw?.discoveryRunId || "",
    resultsRunId: raw?.resultsRunId || raw?.discoveryRunId || "",
    acquisitionRunId: raw?.acquisitionRunId || "",
    exportSourceIds: Array.isArray(raw?.exportSourceIds) ? raw.exportSourceIds : [],
    sessionContext: typeof raw?.sessionContext === "string" ? raw.sessionContext : "",
    sessionContextUpdatedAt: typeof raw?.sessionContextUpdatedAt === "string" ? raw.sessionContextUpdatedAt : "",
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

function persistSessions() {
  localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(state.sessions));
  localStorage.setItem(ACTIVE_SESSION_KEY, state.activeSessionId);
}

function activeSession() {
  return state.sessions.find((session) => session.id === state.activeSessionId) || state.sessions[0];
}

function activeQueries(session = activeSession()) {
  return session.queries.filter((query) => query.selected).map((query) => query.text.trim()).filter(Boolean);
}

function discoverRunId(session = activeSession()) {
  return (session?.discoveryRunId || "").trim();
}

function resultsRunId(session = activeSession()) {
  return (session?.resultsRunId || session?.discoveryRunId || "").trim();
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
  if (!latestRunId) {
    els.sessionState.textContent = "No discovery runs found yet. Start discovery.";
    if (els.discoverState) {
      els.discoverState.textContent = "No discovery runs found yet. Start discovery.";
    }
    return false;
  }
  const previous = session.discoveryRunId || "";
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
  if (els.internalRepoUrlInput) {
    els.internalRepoUrlInput.value = state.internalRepositoryBaseUrl;
  }
  renderReviewFilterChips();
  renderReviewSortButtons();
  renderStopButton();
  applyOfflineActionState();
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
    "downloadMissingBtn",
    "retryFailedBtn",
    "documentsExportCsvBtn",
    "documentsRowActionBtn",
    "saveInternalRepoUrlBtn",
    "libraryAddBtn",
    "libraryRemoveBtn",
    "libraryZipBtn",
    "libraryMetadataBtn",
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
  state.reviewSort = { key: "iteration", dir: "desc" };
}

function compareReviewItems(a, b) {
  const { key, dir } = state.reviewSort;
  const factor = dir === "asc" ? 1 : -1;
  const valueA = Number(a[key] ?? 0);
  const valueB = Number(b[key] ?? 0);
  if (valueA !== valueB) {
    return (valueA - valueB) * factor;
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
    const base = button.dataset.reviewSort === "iteration"
      ? "Iter"
      : button.dataset.reviewSort === "year"
        ? "Year"
        : button.dataset.reviewSort === "citation_count"
          ? "Cit"
          : "Score";
    button.textContent = `${base}${suffix}`;
  });
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
  els.discoverSessionName.value = current.name;
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
        els.discoverSuggestionsState.textContent = `Added suggested query: ${text}`;
      } else {
        els.discoverSuggestionsState.textContent = `Query already selected: ${text}`;
      }
    });
    li.appendChild(button);
    els.discoverSuggestedQueryList.appendChild(li);
  });
  if (!state.suggestedQueries.length) {
    els.discoverSuggestionsState.textContent = "No suggestions generated yet.";
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
    if (typeof profile.name === "string" && profile.name.trim()) {
      session.name = profile.name.trim();
    }
    persistSessions();
    if (state.activeSessionId === sessionId) {
      renderSessions();
      els.sessionContextState.textContent = session.sessionContext
        ? "Session context loaded."
        : "Session context is required before running discovery.";
    }
  } catch (error) {
    const detail = errorDetail(error);
    if (detail.includes("session_not_found")) {
      session.sessionContext = "";
      session.sessionContextUpdatedAt = "";
      persistSessions();
      if (state.activeSessionId === sessionId) {
        renderSessions();
        els.sessionContextState.textContent = "No context saved for this session yet.";
      }
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

async function generateQuerySuggestions() {
  const session = activeSession();
  const context = normalizeSessionContext(session.sessionContext || els.sessionContextInput.value);
  if (!context) {
    els.discoverSuggestionsState.textContent = "Save session context before generating suggestions.";
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
        max_suggestions: 8,
      }),
    });
    state.suggestedQueries = Array.isArray(result.data?.suggestions) ? result.data.suggestions : [];
    renderSuggestedQueries();
    els.discoverSuggestionsState.textContent = state.suggestedQueries.length
      ? `Generated ${state.suggestedQueries.length} suggestion(s). Click one to add it to the selected query list.`
      : (result.data?.warning || "No suggestions returned.");
  } catch (error) {
    els.discoverSuggestionsState.textContent = `Unable to generate suggestions: ${errorDetail(error)}`;
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
    els.authStatus.textContent = `Auth: ${data.auth_mode}`;
    els.aiStatus.textContent = `AI: ${data.ai_filter_active ? "ready" : "inactive"}`;
    els.dbStatus.textContent = `DB: ${data.db_ready ? "ready" : "not ready"}`;
    els.footerSystem.textContent = `System: ${data.auth_mode}`;
    els.footerAi.textContent = `AI: ${data.ai_filter_active ? "ready" : "inactive"}`;
    els.footerDb.textContent = `DB: ${data.db_ready ? "ready" : "not ready"}`;
    els.footerUpdated.textContent = `Last update: ${new Date().toLocaleTimeString()}`;
  } catch {
    els.footerSystem.textContent = "System: error";
    els.footerUpdated.textContent = "Last update: error";
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
  return [
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

function renderDiscoverRunQueries() {
  els.discoverRunQueries.innerHTML = "";
  if (!state.discoverRunQueries.length) {
    els.discoverRunQueriesState.textContent = "No executed queries for the active run yet.";
    return;
  }
  els.discoverRunQueriesState.textContent = `${state.discoverRunQueries.length} executed quer${state.discoverRunQueries.length === 1 ? "y" : "ies"} loaded for the active run.`;
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
    const countText = item.status === "completed" || item.status === "ranking_relevance"
      ? String(item.discovered_count)
      : "-";
    tr.innerHTML = `
      <td>${item.position}</td>
      <td>${escapeHtml(item.query)}</td>
      <td><span class="status-chip ${escapeHtml(item.status)}">${escapeHtml(displayStatus)}</span></td>
      <td>${escapeHtml(providers)}</td>
      <td>${escapeHtml(reviewCounts)}${scopeProgress ? `<div class="muted">${escapeHtml(scopeProgress)}</div>` : ""}</td>
      <td>${countText}</td>
    `;
    els.discoverRunQueries.appendChild(tr);
  });
}

function updateCitationAvailability(acceptedCount) {
  const disabled = acceptedCount <= 0;
  els.runNextCitationBtn.disabled = disabled;
  els.discoverCitationHint.textContent = disabled
    ? "Need at least 1 accepted paper before running citation iteration."
    : "Citation iteration is available for the current session.";
}

async function loadDiscover(recoverOnNotFound = true) {
  const session = activeSession();
  renderSessions();
  state.currentDiscoveryStatus = null;
  state.discoverRunQueries = [];
  if (!session.discoveryRunId) {
    els.discoverIterationLine.textContent = "Iteration: -";
    ["discoverSummaryDiscovered", "discoverSummaryApproved", "discoverSummaryRejected", "discoverSummaryReviewed", "discoverSummaryPending"].forEach((id) => {
      els[id].textContent = "0";
    });
    renderDiscoverRunQueries();
    updateCitationAvailability(0);
    els.resumeCitationBtn.disabled = true;
    return;
  }
  let runResult;
  let allResult;
  let pendingResult;
  let rejectedResult;
  let queryResult;
  try {
    [runResult, allResult, pendingResult, rejectedResult, queryResult] = await Promise.all([
      api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}`),
      api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/sources?status=all&limit=1000`),
      api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/sources?status=needs_review&limit=1000`),
      api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/sources?status=rejected&limit=1000`),
      api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/queries`),
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
  const hasResumableCitation = state.discoverRunQueries.some(
    (item) => item.query === "citation expansion" && item.checkpoint_state === "resumable",
  );
  els.resumeCitationBtn.disabled = !hasResumableCitation;
  const allItems = allResult.data.items || [];
  const pendingItems = pendingResult.data.items || [];
  const rejectedItems = rejectedResult.data.items || [];
  const approvedCount = allItems.filter((item) => item.accepted).length;
  const reviewedCount = allItems.length - pendingItems.length;
  els.discoverIterationLine.textContent = `Iteration: ${run.current_iteration} / ${run.total}`;
  els.discoverSummaryDiscovered.textContent = String(allItems.length);
  els.discoverSummaryApproved.textContent = String(approvedCount);
  els.discoverSummaryRejected.textContent = String(rejectedItems.length);
  els.discoverSummaryReviewed.textContent = String(reviewedCount);
  els.discoverSummaryPending.textContent = String(pendingItems.length);
  if (run.stage_status === "running" && resultsRunId(session) && resultsRunId(session) !== discoverRunId(session)) {
    els.discoverState.textContent = `New discovery run is in progress. Review/Documents/Library still show results from ${resultsRunId(session)}.`;
  } else if (run.status === "completed" && discoverRunId(session) && resultsRunId(session) !== discoverRunId(session)) {
    session.resultsRunId = discoverRunId(session);
    session.acquisitionRunId = "";
    persistSessions();
    els.discoverState.textContent = `New discovery run completed. Review/Documents/Library switched to ${session.resultsRunId}.`;
  } else {
    els.discoverState.textContent = run.message;
  }
  updateCitationAvailability(approvedCount);
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
    return;
  }
  els.reviewTitle.textContent = item.title;
  els.reviewAbstract.textContent = item.abstract || "No abstract available.";
  els.reviewMetadata.innerHTML = buildMetadataHtml(item);
  els.reviewSignals.textContent = reviewSignalText(item);
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
    tr.innerHTML = `<td>${escapeHtml(item.review_status || "-")}</td><td>${item.iteration || "-"}</td><td>${item.year || "-"}</td><td>${item.citation_count ?? "-"}</td><td>${Number(item.relevance_score || 0).toFixed(2)}</td><td>${escapeHtml(item.title)}</td>`;
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
  if (!runId) {
    state.reviewItems = [];
    state.reviewIndex = -1;
    renderReviewRows();
    els.reviewState.textContent = "No discovery run attached to the active session.";
    return;
  }
  let result;
  try {
    result = await api(`/v1/discovery/runs/${encodeURIComponent(runId)}/sources?status=${encodeURIComponent(status)}&limit=200`);
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
  const session = activeSession();
  const item = state.reviewItems[state.reviewIndex];
  const runId = resultsRunId(session);
  if (!runId || !item) {
    return;
  }
  const selectionHint = decision === "reject" ? nextReviewSelectionHint() : null;
  beginBusy("Waiting for review");
  try {
    await api(`/v1/sources/${encodeURIComponent(item.id)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, run_id: runId }),
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
    return;
  }
  els.documentsDetailTitle.textContent = row.title;
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
  applyOfflineActionState();
}

function renderDocuments() {
  els.documentsRows.innerHTML = "";
  state.documentRows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.classList.toggle("active", row.source.id === state.selectedDocumentSourceId);
    tr.innerHTML = `<td>${row.rank}</td><td>${row.score}</td><td>${row.year}</td><td>${row.citations}</td><td>${escapeHtml(row.title)}</td><td>${row.status}</td>`;
    tr.addEventListener("click", () => {
      state.selectedDocumentSourceId = row.source.id;
      renderDocuments();
    });
    els.documentsRows.appendChild(tr);
  });
  renderDocumentsDetail();
}

async function loadDocuments(recoverOnNotFound = true) {
  const session = activeSession();
  state.currentAcquisitionStatus = null;
  const runId = resultsRunId(session);
  if (!runId) {
    state.documentRows = [];
    state.selectedDocumentSourceId = "";
    els.documentsDownloaded.textContent = "0";
    els.documentsFailed.textContent = "0";
    els.documentsManual.textContent = "0";
    els.documentsPending.textContent = "0";
    els.documentsBadge.textContent = "0";
    els.documentsState.textContent = "No discovery run attached to the active session.";
    renderDocuments();
    return;
  }
  let acceptedResult;
  try {
    const acceptedItems = await fetchAllPages(async (offset, limit) => {
      const response = await api(
        `/v1/discovery/runs/${encodeURIComponent(runId)}/sources?status=accepted&limit=${limit}&offset=${offset}`,
      );
      return response.data || {};
    });
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
  let statusData = null;
  let items = [];
  if (session.acquisitionRunId) {
    try {
      statusData = (await api(`/v1/acquisition/runs/${encodeURIComponent(session.acquisitionRunId)}`)).data;
      if (statusData?.discovery_run_id === runId) {
        items = await fetchAllPages(async (offset, limit) => {
          const response = await api(
            `/v1/acquisition/runs/${encodeURIComponent(session.acquisitionRunId)}/items?limit=${limit}&offset=${offset}`,
          );
          return response.data || {};
        });
      } else {
        session.acquisitionRunId = "";
        statusData = null;
        items = [];
        persistSessions();
      }
    } catch {
      session.acquisitionRunId = "";
      persistSessions();
    }
  }
  state.currentAcquisitionStatus = statusData;
  const itemMap = new Map(items.map((item) => [item.source_id, item]));
  state.documentRows = normalizeDocumentRows(accepted, itemMap);
  if (!state.documentRows.some((row) => row.source.id === state.selectedDocumentSourceId)) {
    state.selectedDocumentSourceId = state.documentRows[0]?.source.id || "";
  }
  renderDocuments();
  els.documentsDownloaded.textContent = String(statusData?.downloaded_total || items.filter((item) => item.status === "downloaded").length);
  els.documentsFailed.textContent = String(statusData?.failed_total || items.filter((item) => item.status === "failed").length);
  els.documentsManual.textContent = String(items.filter((item) => item.status === "partial" || item.status === "skipped").length);
  els.documentsPending.textContent = String(Math.max(accepted.length - items.filter((item) => item.status !== "pending").length, 0));
  els.documentsBadge.textContent = String(items.filter((item) => item.status === "failed" || item.status === "partial").length);
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

function renderLibraryDetail(item) {
  if (!item) {
    els.libraryTitle.textContent = "No paper selected.";
    els.libraryAbstract.textContent = "Select a paper to inspect and export.";
    els.libraryMetadata.innerHTML = "Year: - | Journal: - | Citations: - | Authors: - | Link: -";
    return;
  }
  els.libraryTitle.textContent = item.title;
  els.libraryAbstract.textContent = item.abstract || "No abstract available.";
  els.libraryMetadata.innerHTML = buildMetadataHtml(item);
}

function renderLibraryRows() {
  const query = els.libraryQuery.value.trim().toLowerCase();
  const filtered = !query
    ? [...state.libraryRows]
    : state.libraryRows.filter((item) => `${item.title} ${item.abstract || ""}`.toLowerCase().includes(query));
  state.libraryFilteredRows = filtered;
  els.libraryRows.innerHTML = "";
  filtered.forEach((item, index) => {
    const tr = document.createElement("tr");
    tr.classList.toggle("active", item.id === state.selectedLibrarySourceId);
    tr.innerHTML = `<td>${index + 1}</td><td>${Number(item.relevance_score || 0).toFixed(2)}</td><td>${item.year || "-"}</td><td>${item.citation_count ?? "-"}</td><td>${escapeHtml(item.title)}</td>`;
    tr.addEventListener("click", () => {
      state.selectedLibrarySourceId = item.id;
      renderLibraryRows();
      renderLibraryDetail(item);
    });
    els.libraryRows.appendChild(tr);
  });
  const scores = filtered.map((item) => Number(item.relevance_score || 0));
  els.libraryMatches.textContent = String(filtered.length);
  els.libraryHighest.textContent = scores.length ? Math.max(...scores).toFixed(2) : "-";
  els.libraryLowest.textContent = scores.length ? Math.min(...scores).toFixed(2) : "-";
  const detail = filtered.find((item) => item.id === state.selectedLibrarySourceId) || filtered[0];
  state.selectedLibrarySourceId = detail?.id || "";
  renderLibraryDetail(detail);
}

function resetSessionBoundPaneState() {
  state.currentDiscoveryStatus = null;
  state.currentAcquisitionStatus = null;
  state.discoverRunQueries = [];
  state.suggestedQueries = [];
  state.reviewItems = [];
  state.reviewIndex = -1;
  state.documentRows = [];
  state.libraryRows = [];
  state.libraryFilteredRows = [];
  state.selectedDocumentSourceId = "";
  state.selectedReviewSourceId = "";
  state.selectedLibrarySourceId = "";

  els.discoverIterationLine.textContent = "Iteration: -";
  ["discoverSummaryDiscovered", "discoverSummaryApproved", "discoverSummaryRejected", "discoverSummaryReviewed", "discoverSummaryPending"].forEach((id) => {
    els[id].textContent = "0";
  });
  renderDiscoverRunQueries();
  renderSuggestedQueries();
  updateCitationAvailability(0);
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

  renderStopButton();
  renderActivity();
}

async function loadLibrary(recoverOnNotFound = true) {
  const session = activeSession();
  const runId = resultsRunId(session);
  if (!runId) {
    state.libraryRows = [];
    renderLibraryRows();
    return;
  }
  let result;
  try {
    result = await api(`/v1/discovery/runs/${encodeURIComponent(runId)}/sources?status=accepted&limit=1000`);
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
  renderLibraryRows();
  els.libraryState.textContent = state.libraryRows.length ? "Library export data loaded." : "No accepted sources available.";
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
      els.discoverState.textContent = `Discovery started. Review/Documents still show previous results from ${previousResultsRunId} until the new run completes.`;
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
    els.discoverState.textContent = "Run the first discovery iteration before starting citation expansion.";
    return;
  }
  if (!queries.length) {
    els.discoverState.textContent = "Select at least one manual query for the next citation iteration.";
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
      els.discoverState.textContent = `Citation iteration started. Review/Documents still show previous results from ${previousResultsRunId} until the new run completes.`;
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
  const session = activeSession();
  if (!session.acquisitionRunId) {
    els.documentsState.textContent = "Start document acquisition first.";
    return;
  }
  const files = Array.from(els.batchUploadFiles.files || []);
  if (!files.length) {
    els.documentsState.textContent = "Choose at least one file.";
    return;
  }
  beginBusy("Downloading documents");
  try {
    const form = new FormData();
    files.forEach((file) => form.append("files", file));
    const result = await api(`/v1/acquisition/runs/${encodeURIComponent(session.acquisitionRunId)}/manual-upload-batch`, {
      method: "POST",
      body: form,
    });
    els.batchUploadResults.textContent = JSON.stringify(result.data, null, 2);
    await loadDocuments();
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
    const params = new URLSearchParams();
    for (const id of state.documentRows.map((row) => row.source.id)) {
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
    const result = await api(`/v1/library-export/runs/${encodeURIComponent(runId)}/pdfs.zip?${params.toString()}`);
    downloadBlob(result.data, `library_export_${runId}.zip`);
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
    await loadSessionProfile(activeSession()?.id || "");
    await loadLatestIds();
    try {
      await ensureBoundDiscoveryRun();
    } catch {
      // Keep refresh best-effort; pane loaders will surface actionable state.
    }
    await loadSystemStatus();
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

  els.newSessionBtn.addEventListener("click", () => {
    const session = createBlankSession();
    state.sessions.push(session);
    state.activeSessionId = session.id;
    state.pendingSessionId = session.id;
    resetReviewSort();
    persistSessions();
    renderSessions();
    renderShell();
    resetSessionBoundPaneState();
    els.sessionContextState.textContent = "No context saved for this session yet.";
    els.sessionState.textContent = `Created new session: ${session.name}`;
  });
  els.saveSessionBtn.addEventListener("click", () => {
    const session = activeSession();
    session.name = els.discoverSessionName.value.trim() || session.name;
    persistSessions();
    renderSessions();
    els.sessionState.textContent = `Saved session: ${session.name}`;
  });
  els.loadSessionBtn.addEventListener("click", async () => {
    const nextId = els.sessionSelect.value;
    if (!nextId) {
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
  });
  els.stopRunningBtn.addEventListener("click", stopRunningTask);
  els.sessionSelect.addEventListener("change", () => {
    state.pendingSessionId = els.sessionSelect.value;
    const pending = state.sessions.find((session) => session.id === state.pendingSessionId);
    if (pending) {
      els.sessionState.textContent = `Selected session: ${pending.name}. Press Load to open it.`;
    }
  });
  els.discoverSessionName.addEventListener("change", () => {
    const session = activeSession();
    session.name = els.discoverSessionName.value.trim() || session.name;
    persistSessions();
    renderSessions();
  });
  els.sessionContextInput.addEventListener("input", () => {
    const session = activeSession();
    session.sessionContext = els.sessionContextInput.value;
    els.sessionContextCounter.textContent = `${normalizeSessionContext(session.sessionContext).length} / ${SESSION_CONTEXT_MAX}`;
    persistSessions();
    updateQuerySelectionState();
    if (!normalizeSessionContext(session.sessionContext)) {
      els.sessionContextState.textContent = "Session context is required before running discovery.";
    } else {
      els.sessionContextState.textContent = "Unsaved context changes.";
    }
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
      const key = button.dataset.reviewSort || "iteration";
      if (state.reviewSort.key === key) {
        state.reviewSort.dir = state.reviewSort.dir === "desc" ? "asc" : "desc";
      } else {
        state.reviewSort = { key, dir: key === "iteration" ? "desc" : "desc" };
      }
      renderReviewRows();
    });
  });
  els.downloadMissingBtn.addEventListener("click", () => startAcquisition(false));
  els.retryFailedBtn.addEventListener("click", () => startAcquisition(true));
  els.documentsRowActionBtn.addEventListener("click", handleSelectedDocumentAction);
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
  els.libraryMetadataBtn.addEventListener("click", exportLibraryMetadata);
  els.libraryZipBtn.addEventListener("click", exportLibraryZip);
  els.saveApiKeyBtn.addEventListener("click", () => {
    state.token = els.apiKeyInput.value.trim();
    saveToken();
    els.apiKeyState.textContent = state.token ? "API key saved." : "API key cleared.";
    connectLiveUpdates();
    refreshAll();
  });
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
  document.addEventListener("keydown", handleKeyboard);
}

async function init() {
  readDom();
  loadSessions();
  renderSessions();
  renderShell();
  wireEvents();
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
