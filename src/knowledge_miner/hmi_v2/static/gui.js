const AUTH_STORAGE_KEY = "km_hmi2_api_key";
const SESSION_STORAGE_KEY = "km_hmi2_sessions";
const ACTIVE_SESSION_KEY = "km_hmi2_active_session";

const DEFAULT_QUERY = "ultrapure water semiconductor";
const REVIEW_STATUS_TO_API = {
  pending: "needs_review",
  processing: "processing",
  accepted: "accepted",
  rejected: "rejected",
  later: "later",
  all: "all",
};

const state = {
  authEnabled: Boolean(window.__KM_HMI2_AUTH_ENABLED__),
  token: window.__KM_HMI2_DEFAULT_TOKEN__ || localStorage.getItem(AUTH_STORAGE_KEY) || "",
  sessions: [],
  activeSessionId: localStorage.getItem(ACTIVE_SESSION_KEY) || "",
  pendingSessionId: "",
  activePage: window.__KM_HMI2_LAUNCH_SECTION__ || "discover",
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
  eventSource: null,
  inFlight: 0,
  busyLabel: "",
  currentDiscoveryStatus: null,
  currentAcquisitionStatus: null,
  liveRefreshTimer: null,
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
    "newSessionBtn", "saveSessionBtn", "loadSessionBtn", "deleteSessionBtn", "sessionSelect", "sessionState",
    "discoverSessionName", "discoverQueryInput", "addQueryBtn", "runDiscoveryBtn", "runNextCitationBtn", "resumeCitationBtn", "discoverIterationLine",
    "discoverQueryList", "discoverSelectedCount", "discoverRunQueries", "discoverCitationHint",
    "discoverSummaryDiscovered", "discoverSummaryApproved", "discoverSummaryRejected", "discoverSummaryReviewed", "discoverSummaryPending", "discoverState",
    "reviewHeading", "reviewRows", "reviewTitle", "reviewAbstract", "reviewMetadata", "reviewSignals",
    "reviewAcceptBtn", "reviewRejectBtn", "reviewLaterBtn", "reviewState", "reviewBadge", "reviewStatusFilter", "reviewQueueHelp",
    "documentsDownloaded", "documentsFailed", "documentsManual", "documentsPending", "documentsRows",
    "downloadMissingBtn", "retryFailedBtn", "documentsExportCsvBtn", "batchUploadForm", "batchUploadFiles",
    "batchUploadResults", "documentsState", "documentsBadge", "documentsDetailTitle", "documentsDetailSummary",
    "documentsDetailMetadata", "documentsRowActionBtn",
    "libraryMatches", "libraryHighest", "libraryLowest", "libraryQuery", "libraryExportSize", "libraryRows",
    "libraryTitle", "libraryAbstract", "libraryMetadata", "libraryAddBtn", "libraryRemoveBtn", "libraryZipBtn",
    "libraryMetadataBtn", "libraryState",
    "apiKeyInput", "saveApiKeyBtn", "apiKeyState", "latestDiscoveryId", "latestAcquisitionId", "latestParseId",
    "openalexLimitInput", "braveCountInput", "braveAllowlistCheckbox", "saveProviderSettingsBtn", "providerSettingsState",
    "globalSearchInput", "globalSearchBtn", "globalSearchResults", "runLookupInput", "runLookupBtn", "runLookupResult",
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
    queries: normalizedQueries.length ? normalizedQueries : [{ id: `query_${Math.random().toString(36).slice(2, 10)}`, text: DEFAULT_QUERY, selected: true }],
    discoveryRunId: raw?.discoveryRunId || "",
    acquisitionRunId: raw?.acquisitionRunId || "",
    exportSourceIds: Array.isArray(raw?.exportSourceIds) ? raw.exportSourceIds : [],
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

function saveToken() {
  if (state.token) {
    localStorage.setItem(AUTH_STORAGE_KEY, state.token);
  } else {
    localStorage.removeItem(AUTH_STORAGE_KEY);
  }
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
  const response = await fetch(path, { ...options, headers });
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

function setProgress(percent, label) {
  const value = Number.isFinite(percent) ? percent : 0;
  els.headerProgress.value = value;
  els.headerProgressLabel.textContent = label || `${Math.round(value)}%`;
}

function renderActivity() {
  let text = "Idle";
  let active = false;
  if (state.inFlight > 0) {
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
  renderActivity();
}

function renderSessionQueries() {
  const session = activeSession();
  els.discoverQueryList.innerHTML = "";
  session.queries.forEach((query) => {
    const li = document.createElement("li");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = query.selected !== false;
    checkbox.addEventListener("change", () => {
      query.selected = checkbox.checked;
      persistSessions();
      updateQuerySelectionState();
    });
    const label = document.createElement("span");
    label.textContent = query.text;
    li.appendChild(checkbox);
    li.appendChild(label);
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
  renderSessionQueries();
}

function updateQuerySelectionState() {
  const count = activeQueries().length;
  els.discoverSelectedCount.textContent = `Selected queries: ${count}`;
  els.runDiscoveryBtn.disabled = count === 0;
}

function bindLatestIdsToSession() {
  const session = activeSession();
  if (!session.discoveryRunId && state.latest.discovery) {
    session.discoveryRunId = state.latest.discovery;
  }
  if (!session.acquisitionRunId && state.latest.acquisition) {
    session.acquisitionRunId = state.latest.acquisition;
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

function renderDiscoverRunQueries() {
  els.discoverRunQueries.innerHTML = "";
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
    const countText = item.status === "completed" || item.status === "ranking_relevance"
      ? String(item.discovered_count)
      : "-";
    tr.innerHTML = `
      <td>${item.position}</td>
      <td>${escapeHtml(item.query)}</td>
      <td><span class="status-chip ${escapeHtml(item.status)}">${escapeHtml(item.status)}</span></td>
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

async function loadDiscover() {
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
  const [runResult, allResult, pendingResult, rejectedResult, queryResult] = await Promise.all([
    api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}`),
    api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/sources?status=all&limit=1000`),
    api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/sources?status=needs_review&limit=1000`),
    api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/sources?status=rejected&limit=1000`),
    api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/queries`),
  ]);
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
  els.discoverState.textContent = run.message;
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
  const label = queue.charAt(0).toUpperCase() + queue.slice(1);
  return `Review Sources - ${label}: ${count}`;
}

function renderReviewRows() {
  els.reviewRows.innerHTML = "";
  state.reviewItems.forEach((item, index) => {
    const tr = document.createElement("tr");
    tr.classList.toggle("active", index === state.reviewIndex);
    tr.innerHTML = `<td>${escapeHtml(item.review_status || "-")}</td><td>${item.year || "-"}</td><td>${item.citation_count ?? "-"}</td><td>${Number(item.relevance_score || 0).toFixed(2)}</td><td>${escapeHtml(item.title)}</td>`;
    tr.addEventListener("click", () => {
      state.reviewIndex = index;
      state.selectedReviewSourceId = item.id;
      renderReviewRows();
      renderReviewDetail();
    });
    els.reviewRows.appendChild(tr);
  });
  const queue = (els.reviewStatusFilter?.value || "pending").trim();
  els.reviewHeading.textContent = reviewHeadingForQueue(queue, state.reviewItems.length);
  els.reviewBadge.textContent = String(state.reviewItems.length);
  renderReviewDetail();
}

async function loadReview() {
  const session = activeSession();
  const queue = (els.reviewStatusFilter?.value || "pending").trim();
  const status = REVIEW_STATUS_TO_API[queue] || "needs_review";
  if (!session.discoveryRunId) {
    state.reviewItems = [];
    state.reviewIndex = -1;
    renderReviewRows();
    els.reviewState.textContent = "No discovery run attached to the active session.";
    return;
  }
  const result = await api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/sources?status=${encodeURIComponent(status)}&limit=200`);
  state.reviewItems = result.data.items || [];
  state.reviewIndex = state.reviewItems.length ? 0 : -1;
  state.selectedReviewSourceId = state.reviewItems[0]?.id || "";
  renderReviewRows();
  els.reviewState.textContent = state.reviewItems.length ? `Review queue loaded (${queue}).` : `No ${queue} review items.`;
}

async function submitReviewDecision(decision) {
  const session = activeSession();
  const item = state.reviewItems[state.reviewIndex];
  if (!session.discoveryRunId || !item) {
    return;
  }
  beginBusy("Waiting for review");
  try {
    await api(`/v1/sources/${encodeURIComponent(item.id)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, run_id: session.discoveryRunId }),
    });
    await loadReview();
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

async function loadDocuments() {
  const session = activeSession();
  state.currentAcquisitionStatus = null;
  if (!session.discoveryRunId) {
    state.documentRows = [];
    state.selectedDocumentSourceId = "";
    renderDocuments();
    return;
  }
  const acceptedResult = await api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/sources?status=accepted&limit=1000`);
  const accepted = acceptedResult.data.items || [];
  let statusData = null;
  let items = [];
  if (session.acquisitionRunId) {
    try {
      statusData = (await api(`/v1/acquisition/runs/${encodeURIComponent(session.acquisitionRunId)}`)).data;
      items = (await api(`/v1/acquisition/runs/${encodeURIComponent(session.acquisitionRunId)}/items?limit=1000`)).data.items || [];
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

async function loadLibrary() {
  const session = activeSession();
  if (!session.discoveryRunId) {
    state.libraryRows = [];
    renderLibraryRows();
    return;
  }
  const result = await api(`/v1/discovery/runs/${encodeURIComponent(session.discoveryRunId)}/sources?status=accepted&limit=1000`);
  state.libraryRows = result.data.items || [];
  renderLibraryRows();
  els.libraryState.textContent = state.libraryRows.length ? "Library export data loaded." : "No accepted sources available.";
}

async function createDiscoveryRun() {
  const session = activeSession();
  const queries = activeQueries(session);
  if (!queries.length) {
    els.discoverState.textContent = "Select at least one manual query.";
    return;
  }
  beginBusy("Searching providers");
  setProgress(10, "Queued");
  try {
    const result = await api("/v1/discovery/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ seed_queries: queries, selected_queries: queries, max_iterations: 1 }),
    });
    session.discoveryRunId = result.data.run_id;
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
      body: JSON.stringify({ selected_queries: queries }),
    });
    session.discoveryRunId = result.data.run_id;
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
  if (!session.discoveryRunId) {
    els.documentsState.textContent = "Run discovery first.";
    return;
  }
  beginBusy("Downloading documents");
  setProgress(15, "Queued");
  try {
    const result = await api("/v1/acquisition/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: session.discoveryRunId, retry_failed_only: retryFailedOnly, selected_source_ids: selectedSourceIds }),
    });
    session.acquisitionRunId = result.data.acq_run_id;
    persistSessions();
    await loadLatestIds();
    await loadDocuments();
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
  if (!session.discoveryRunId) {
    return;
  }
  beginBusy("Preparing export");
  try {
    const params = new URLSearchParams();
    for (const id of state.documentRows.map((row) => row.source.id)) {
      params.append("source_id", id);
    }
    const result = await api(`/v1/library-export/runs/${encodeURIComponent(session.discoveryRunId)}/metadata.csv?${params.toString()}`);
    downloadBlob(result.data, `documents_${session.discoveryRunId}.csv`);
  } finally {
    endBusy();
  }
}

async function exportLibraryMetadata() {
  const session = activeSession();
  if (!session.discoveryRunId) {
    return;
  }
  beginBusy("Preparing export");
  try {
    const params = new URLSearchParams();
    for (const id of selectedLibraryIds()) {
      params.append("source_id", id);
    }
    const result = await api(`/v1/library-export/runs/${encodeURIComponent(session.discoveryRunId)}/metadata.csv?${params.toString()}`);
    downloadBlob(result.data, `library_export_${session.discoveryRunId}.csv`);
  } finally {
    endBusy();
  }
}

async function exportLibraryZip() {
  const session = activeSession();
  if (!session.discoveryRunId) {
    return;
  }
  beginBusy("Preparing export");
  try {
    const params = new URLSearchParams();
    for (const id of selectedLibraryIds()) {
      params.append("source_id", id);
    }
    const result = await api(`/v1/library-export/runs/${encodeURIComponent(session.discoveryRunId)}/pdfs.zip?${params.toString()}`);
    downloadBlob(result.data, `library_export_${session.discoveryRunId}.zip`);
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

function connectLiveUpdates() {
  if (state.eventSource) {
    state.eventSource.close();
  }
  const tokenParam = state.authEnabled && state.token ? `?api_key=${encodeURIComponent(state.token)}` : "";
  state.eventSource = new EventSource(`/v1/events/stream${tokenParam}`);
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
    els.documentsBadge.textContent = String(payload.doc_issues || 0);
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
    await loadLatestIds();
    await loadSystemStatus();
    await loadProviderSettings();
    await loadDiscover();
    await loadReview();
    await loadDocuments();
    await loadLibrary();
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
    });
  });

  els.newSessionBtn.addEventListener("click", () => {
    const session = createBlankSession();
    state.sessions.push(session);
    state.activeSessionId = session.id;
    state.pendingSessionId = session.id;
    persistSessions();
    renderSessions();
    renderShell();
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
    persistSessions();
    renderSessions();
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
    persistSessions();
    renderSessions();
    await refreshAll();
    els.sessionState.textContent = `Deleted session. Active: ${activeSession().name}`;
  });
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
    }
    els.discoverQueryInput.value = "";
  });
  els.runDiscoveryBtn.addEventListener("click", createDiscoveryRun);
  els.runNextCitationBtn.addEventListener("click", createNextCitationIteration);
  els.resumeCitationBtn.addEventListener("click", resumeCitationIteration);
  els.reviewAcceptBtn.addEventListener("click", () => submitReviewDecision("accept"));
  els.reviewRejectBtn.addEventListener("click", () => submitReviewDecision("reject"));
  els.reviewLaterBtn.addEventListener("click", () => submitReviewDecision("later"));
  els.reviewStatusFilter.addEventListener("change", () => {
    loadReview();
  });
  els.downloadMissingBtn.addEventListener("click", () => startAcquisition(false));
  els.retryFailedBtn.addEventListener("click", () => startAcquisition(true));
  els.documentsRowActionBtn.addEventListener("click", handleSelectedDocumentAction);
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
}

init().catch((error) => {
  const message = error instanceof Error ? error.message : String(error);
  els.activityLine.textContent = `Startup error: ${message}`;
  els.activityIndicator.hidden = true;
});
