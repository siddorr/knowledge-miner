const POLL_ACTIVE_MS = 5000;
const POLL_BACKGROUND_MS = 15000;
const TELEMETRY_INPUT_DEBOUNCE_MS = 400;
const SYSTEM_TOKEN = typeof window !== "undefined" ? window.__KM_HMI_DEFAULT_TOKEN__ || null : null;
const AUTH_ENABLED = typeof window !== "undefined" ? window.__KM_HMI_AUTH_ENABLED__ !== false : true;

const state = {
  apiKey: "",
  tokenSource: "none",
  pollTimer: null,
  runRows: [],
  latest: { discovery: "", acquisition: "", parse: "" },
  review: { offset: 0, loaded: false, expanded: new Set() },
  documents: { offset: 0, loaded: false, selectedSourceId: "" },
  search: { loaded: false, payload: null, items: [] },
  context: {},
  telemetry: {
    sessionId: "",
    inputTimers: new Map(),
  },
  statusStrip: {
    nextActionRoute: "build",
  },
};

function el(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  const node = el(id);
  if (node) node.textContent = value;
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setPollState(message, stale = false) {
  const node = el("pollState");
  if (!node) return;
  node.textContent = message;
  node.classList.remove("poll-ok", "poll-stale");
  node.classList.add(stale ? "poll-stale" : "poll-ok");
}

function requiredKey() {
  if (!AUTH_ENABLED) return;
  if (!state.apiKey) throw new Error("API key is required");
}

function authHeaders() {
  if (!AUTH_ENABLED || !state.apiKey) return {};
  return { Authorization: `Bearer ${state.apiKey}` };
}

function telemetryHeaders() {
  if (AUTH_ENABLED && !state.apiKey) return null;
  return { ...authHeaders(), "Content-Type": "application/json" };
}

function telemetrySessionId() {
  if (state.telemetry.sessionId) return state.telemetry.sessionId;
  const seed = `${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
  state.telemetry.sessionId = `hmi_${seed}`;
  return state.telemetry.sessionId;
}

const SAFE_VALUE_PREVIEW_IDS = new Set([
  "startDiscoverySeeds",
  "searchQuery",
  "globalSearchQuery",
  "discoverRunIdInput",
  "reviewRunIdInput",
  "documentsAcqRunIdInput",
  "searchParseRunIdInput",
  "runIdInput",
]);

function controlIdFromTarget(target) {
  if (!target) return "unknown";
  const raw = target.id || target.name || target.getAttribute("data-action") || target.tagName.toLowerCase();
  return String(raw).slice(0, 120);
}

function controlLabelFromTarget(target) {
  if (!target) return null;
  const aria = target.getAttribute("aria-label");
  if (aria) return aria.slice(0, 160);
  const text = (target.textContent || "").trim();
  if (text) return text.slice(0, 160);
  if (target.id) {
    try {
      const label = document.querySelector(`label[for="${CSS.escape(target.id)}"]`);
      const labelText = (label?.textContent || "").trim();
      if (labelText) return labelText.slice(0, 160);
    } catch (_err) {
      // ignore query/escape failures
    }
  }
  return null;
}

function sanitizeValuePreview(target) {
  if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement)) {
    return null;
  }
  const controlId = controlIdFromTarget(target);
  const inputType = target instanceof HTMLInputElement ? (target.type || "").toLowerCase() : "";
  if (["password", "file", "hidden"].includes(inputType)) return "[redacted]";
  if (!SAFE_VALUE_PREVIEW_IDS.has(controlId)) return "[redacted]";
  const value = String(target.value || "").trim();
  if (!value) return "";
  if (value.length <= 120) return value;
  return `${value.slice(0, 120)}...`;
}

function emitTelemetryEvent(eventType, target, forcedValuePreview = undefined) {
  const headers = telemetryHeaders();
  if (!headers) return;
  const sectionNode = target?.closest ? target.closest("section") : null;
  const valuePreview = forcedValuePreview !== undefined ? forcedValuePreview : sanitizeValuePreview(target);
  const payload = {
    events: [
      {
        event_type: eventType,
        control_id: controlIdFromTarget(target),
        control_label: controlLabelFromTarget(target),
        page: activeSection(),
        section: sectionNode?.id || activeSection(),
        session_id: telemetrySessionId(),
        run_id: state.latest.discovery || null,
        acq_run_id: state.latest.acquisition || null,
        parse_run_id: state.latest.parse || null,
        value_preview: valuePreview,
        timestamp_ms: Date.now(),
      },
    ],
  };
  fetch("/v1/hmi/events", {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
    keepalive: true,
  }).catch(() => {
    // fire-and-forget: telemetry failure must not block UI
  });
}

function emitDebouncedInputTelemetry(target) {
  const controlId = controlIdFromTarget(target);
  const prev = state.telemetry.inputTimers.get(controlId);
  if (prev) clearTimeout(prev);
  const timer = setTimeout(() => {
    state.telemetry.inputTimers.delete(controlId);
    emitTelemetryEvent("input", target);
  }, TELEMETRY_INPUT_DEBOUNCE_MS);
  state.telemetry.inputTimers.set(controlId, timer);
}

async function apiGet(path) {
  requiredKey();
  const res = await fetch(path, { headers: authHeaders() });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_err) {
      // ignore
    }
    throw new Error(detail);
  }
  return res.json();
}

async function apiPost(path, payload) {
  requiredKey();
  const res = await fetch(path, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_err) {
      // ignore
    }
    throw new Error(detail);
  }
  return res.json();
}

async function apiDownload(path, filename) {
  requiredKey();
  const res = await fetch(path, { headers: authHeaders() });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch (_err) {
      // ignore
    }
    throw new Error(detail);
  }
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

function setContext(patch) {
  state.context = { ...state.context, ...patch };
  const out = el("globalContext");
  if (out) out.textContent = JSON.stringify(state.context, null, 2);
}

function activeSection() {
  const id = window.location.hash.replace("#", "") || "build";
  const valid = ["build", "discover", "review", "documents", "library", "advanced"];
  return valid.includes(id) ? id : "build";
}

function updateSectionVisibility() {
  const current = activeSection();
  for (const id of ["build", "discover", "review", "documents", "library", "advanced"]) {
    const node = el(id);
    if (!node) continue;
    node.hidden = id !== current;
  }
}

function schedulePoll() {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  const interval = document.visibilityState === "hidden" ? POLL_BACKGROUND_MS : POLL_ACTIVE_MS;
  state.pollTimer = setTimeout(runPollCycle, interval);
}

function upsertRunRow(phase, runId, payload) {
  const summary =
    phase === "discovery"
      ? `iter=${payload.current_iteration || 0}, accepted=${payload.accepted_total || 0}, expanded=${payload.expanded_candidates_total || 0}`
      : phase === "acquisition"
        ? `downloaded=${payload.downloaded_total || 0}, partial=${payload.partial_total || 0}, failed=${payload.failed_total || 0}`
        : `parsed=${payload.parsed_total || 0}, failed=${payload.failed_total || 0}, chunks=${payload.chunked_total || 0}`;
  const row = { phase, id: runId, status: payload.status || "queued", summary };
  const idx = state.runRows.findIndex((r) => r.phase === phase && r.id === runId);
  if (idx >= 0) state.runRows[idx] = row;
  else state.runRows.unshift(row);
}

function renderRunsTable() {
  const tbody = el("runsTable");
  if (!tbody) return;
  const phaseFilter = (el("runFilterPhase") || {}).value || "all";
  const statusFilter = (el("runFilterStatus") || {}).value || "all";
  const rows = state.runRows.filter((r) => (phaseFilter === "all" || r.phase === phaseFilter) && (statusFilter === "all" || r.status === statusFilter));
  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="4">No runs loaded.</td></tr>';
    return;
  }
  tbody.innerHTML = rows
    .map((r) => `<tr><td>${escapeHtml(r.phase)}</td><td>${escapeHtml(r.id)}</td><td>${escapeHtml(r.status)}</td><td>${escapeHtml(r.summary)}</td></tr>`)
    .join("");
}

function setLatestId(kind, value) {
  const trimmed = (value || "").trim();
  state.latest[kind] = trimmed;
  if (kind === "discovery") setText("latestDiscoveryId", trimmed || "-");
  if (kind === "acquisition") setText("latestAcqId", trimmed || "-");
  if (kind === "parse") setText("latestParseId", trimmed || "-");
}

function getDiscoveryRunId() {
  const override = (el("reviewRunIdInput") || {}).value || "";
  const discoverOverride = (el("discoverRunIdInput") || {}).value || "";
  return override.trim() || discoverOverride.trim() || state.latest.discovery;
}

function getAcqRunId() {
  const override = (el("documentsAcqRunIdInput") || {}).value || "";
  return override.trim() || state.latest.acquisition;
}

function getParseRunId() {
  const override = (el("searchParseRunIdInput") || {}).value || "";
  return override.trim() || state.latest.parse;
}

function statusToUi(status) {
  if (["completed", "downloaded", "parsed", "accepted"].includes(status)) return "Ready";
  if (["failed", "error", "rejected", "partial"].includes(status)) return "Needs Action";
  return "In Progress";
}

function statusBadge(status) {
  const text = statusToUi(status);
  const klass = text === "Ready" ? "status-ready" : text === "Needs Action" ? "status-alert" : "status-warn";
  return `<span class="status-badge ${klass}">${escapeHtml(text)}</span>`;
}

function reasonText(reasonCode) {
  if (reasonCode === "paywalled") return "Paywalled";
  if (reasonCode === "no_oa_found") return "No open-access source found";
  if (reasonCode === "rate_limited") return "Provider rate limited";
  if (reasonCode === "robots_blocked") return "Blocked by robots/legal policy";
  if (reasonCode === "source_error") return "Source retrieval error";
  return reasonCode || "Unknown issue";
}

function renderTable(tbodyId, rows, cols) {
  const tbody = el(tbodyId);
  if (!tbody) return;
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="${cols}">No records found.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.join("");
}

function updateStatusStrip({
  pendingReview,
  awaitingDocs,
  docFailures,
  lastRunState,
  activeTopic = "Default Topic",
}) {
  setText("statusActiveTopic", activeTopic);
  setText("statusPendingReview", String(pendingReview));
  setText("statusAwaitingDocs", String(awaitingDocs));
  setText("statusDocFailures", String(docFailures));
  setText("statusLastRun", lastRunState || "none");
  if (pendingReview > 0) state.statusStrip.nextActionRoute = "review";
  else if (docFailures > 0 || awaitingDocs > 0) state.statusStrip.nextActionRoute = "documents";
  else state.statusStrip.nextActionRoute = "build";
  setText("statusNextActionBtn", `Next: ${state.statusStrip.nextActionRoute}`);
}

function abstractView(text, expanded) {
  const raw = (text || "").trim();
  if (!raw) return { text: "", long: false };
  if (raw.length <= 220 || expanded) return { text: raw, long: raw.length > 220 };
  return { text: `${raw.slice(0, 220)}...`, long: true };
}

async function loadSystemStatus() {
  try {
    const payload = await apiGet("/v1/system/status");
    const provider = payload.provider_readiness || {};
    const brave = provider.brave && provider.brave.api_key_present ? "brave:ready" : "brave:missing-key";
    const s2 = provider.semantic_scholar && provider.semantic_scholar.api_key_present ? "s2:ready" : "s2:limited";
    const ai = payload.ai_filter_active ? "ai:active" : `ai:${payload.ai_filter_warning ? "warning" : "disabled"}`;
    setText("systemBadges", `${payload.auth_mode} | ${ai} | ${brave} | ${s2}`);
  } catch (err) {
    setText("systemBadges", `status unavailable: ${err.message}`);
  }
}

async function loadDashboard() {
  setText("dashboardError", "");
  const queue = await apiGet("/v1/work-queue?limit=200&offset=0");
  const needsReview = queue.items.filter((i) => i.phase === "discovery" && i.status === "needs_review").length;
  const docIssues = queue.items.filter((i) => i.phase === "acquisition" && (i.status === "failed" || i.status === "partial")).length;
  const parseErrors = queue.items.filter((i) => i.phase === "parse" && i.status === "failed").length;
  setText("reviewNavBadge", String(needsReview));
  setText("documentsNavBadge", String(docIssues));

  let recent = "No run loaded";
  if (state.latest.discovery) {
    try {
      const run = await apiGet(`/v1/discovery/runs/${encodeURIComponent(state.latest.discovery)}`);
      recent = `status=${run.status}, accepted=${run.accepted_total}, discovered=${run.expanded_candidates_total}`;
      upsertRunRow("discovery", state.latest.discovery, run);
      renderRunsTable();
    } catch (_err) {
      recent = `latest run unavailable (${state.latest.discovery})`;
    }
  }

  renderTable(
    "dashboardCards",
    [
      `<tr><td>${needsReview}</td><td>${docIssues}</td><td>${parseErrors}</td><td>${escapeHtml(recent)}</td></tr>`,
    ],
    4,
  );
  updateStatusStrip({
    pendingReview: needsReview,
    awaitingDocs: docIssues,
    docFailures: docIssues,
    lastRunState: recent,
  });
  return true;
}

async function loadDiscover() {
  setText("discoverError", "");
  const runId = getDiscoveryRunId();
  if (!runId) {
    setText("discoverSummary", "No discovery run selected.");
    return true;
  }
  const run = await apiGet(`/v1/discovery/runs/${encodeURIComponent(runId)}`);
  setLatestId("discovery", runId);
  upsertRunRow("discovery", runId, run);
  renderRunsTable();
  setText(
    "discoverSummary",
    JSON.stringify(
      {
        run_id: run.run_id,
        status: run.status,
        seed_queries: run.seed_queries,
        current_iteration: run.current_iteration,
        accepted_total: run.accepted_total,
        expanded_candidates_total: run.expanded_candidates_total,
        citation_edges_total: run.citation_edges_total,
        ai_filter_active: run.ai_filter_active,
        ai_filter_warning: run.ai_filter_warning,
      },
      null,
      2,
    ),
  );
  setText("discoverState", `Loaded run ${runId}`);
  return true;
}

async function loadReview() {
  setText("reviewError", "");
  const runId = getDiscoveryRunId();
  if (!runId) throw new Error("discovery run id is required");
  const status = el("reviewStatusFilter").value;
  const limit = Number(el("reviewLimit").value);
  const offset = state.review.offset;
  const page = await apiGet(
    `/v1/discovery/runs/${encodeURIComponent(runId)}/sources?status=${encodeURIComponent(status)}&limit=${limit}&offset=${offset}`,
  );
  setLatestId("discovery", runId);
  setText("reviewPage", `offset=${offset}, limit=${limit}, total=${page.total}`);
  renderTable(
    "reviewRows",
    page.items.map((s) => {
      const expanded = state.review.expanded.has(s.id);
      const view = abstractView(s.abstract || "", expanded);
      const toggle = view.long
        ? `<button type="button" class="review-action" data-action="toggle" data-source-id="${escapeHtml(s.id)}">${expanded ? "Collapse" : "Expand"}</button>`
        : "";
      const why = `${escapeHtml(s.decision_source || "")}${s.heuristic_score != null ? ` | score=${escapeHtml(String(s.heuristic_score))}` : ""}`;
      return `<tr data-source-id="${escapeHtml(s.id)}"><td>${escapeHtml(s.title || "")}</td><td><span>${escapeHtml(view.text)}</span> ${toggle}</td><td><button type="button" class="review-action" data-action="accept" data-source-id="${escapeHtml(s.id)}">Accept</button> <button type="button" class="review-action" data-action="reject" data-source-id="${escapeHtml(s.id)}">Reject</button> ${statusBadge(s.review_status)}</td><td>${why}</td></tr>`;
    }),
    4,
  );
  state.review.loaded = true;
  return true;
}

async function loadDocuments() {
  setText("documentsError", "");
  const acqRunId = getAcqRunId();
  if (!acqRunId) throw new Error("acquisition run id is required");
  const limit = Number(el("documentsLimit").value);
  const offset = state.documents.offset;
  const queue = await apiGet(
    `/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/manual-downloads?limit=${limit}&offset=${offset}`,
  );
  const run = await apiGet(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}`);
  setLatestId("acquisition", acqRunId);
  upsertRunRow("acquisition", acqRunId, run);
  renderRunsTable();

  renderTable(
    "documentsRows",
    queue.items.map((item) => {
      const candidates = item.manual_url_candidates || [];
      const openUrl = item.selected_url || item.source_url || candidates[0] || "";
      return `<tr><td>${escapeHtml(item.title || "")}</td><td>${escapeHtml(reasonText(item.reason_code || item.last_error || item.status))}</td><td><button type="button" class="documents-action" data-action="retry" data-source-id="${escapeHtml(item.source_id)}" data-discovery-run-id="${escapeHtml(run.discovery_run_id)}">Retry</button> <button type="button" class="documents-action" data-action="upload" data-source-id="${escapeHtml(item.source_id)}">Upload PDF</button> ${openUrl ? `<a href="${escapeHtml(openUrl)}" target="_blank" rel="noopener noreferrer">Open source</a>` : ""}</td></tr>`;
    }),
    3,
  );
  setText("documentsPage", `offset=${offset}, limit=${limit}, total=${queue.total}`);
  setText("documentsState", `Loaded ${queue.total} items for ${acqRunId}`);
  state.documents.loaded = true;
  return true;
}

async function runSearchData(payload) {
  setText("searchError", "");
  const result = await apiPost("/v1/search", payload);
  state.search.items = result.items || [];
  renderTable(
    "searchRows",
    state.search.items.map((item, idx) => {
      const snippet = item.snippet || "";
      return `<tr><td>${escapeHtml(snippet)}</td><td><button type="button" class="search-action" data-action="doc" data-index="${idx}">Doc</button> <button type="button" class="search-action" data-action="text" data-index="${idx}">Text</button> <button type="button" class="search-action" data-action="source" data-index="${idx}">Source</button></td></tr>`;
    }),
    2,
  );
  setText("searchState", `Results: ${result.total}`);
  state.search.loaded = true;
}

async function runSearch(event) {
  event.preventDefault();
  setText("searchError", "");
  try {
    const parseRunId = getParseRunId();
    if (!parseRunId) throw new Error("parse run id is required");
    const payload = {
      parse_run_id: parseRunId,
      query: el("searchQuery").value.trim(),
      limit: Number(el("searchLimit").value),
    };
    if (!payload.query) throw new Error("query is required");
    state.search.payload = payload;
    await runSearchData(payload);
  } catch (err) {
    setText("searchError", `Search failed: ${err.message}`);
  }
}

async function showSearchDoc(index) {
  const item = state.search.items[index];
  if (!item) return;
  const detail = await apiGet(`/v1/parse/documents/${encodeURIComponent(item.document_id)}`);
  el("searchDocDetail").textContent = JSON.stringify(detail, null, 2);
}

async function showSearchText(index) {
  const item = state.search.items[index];
  if (!item) return;
  const body = await apiGet(`/v1/parse/documents/${encodeURIComponent(item.document_id)}/text`);
  el("searchDocText").textContent = body.text || "";
}

async function showSearchSource(index) {
  const item = state.search.items[index];
  if (!item) return;
  const parseRunId = getParseRunId();
  if (!parseRunId) throw new Error("parse run id is required");
  const parseRun = await apiGet(`/v1/parse/runs/${encodeURIComponent(parseRunId)}`);
  const acqRun = await apiGet(`/v1/acquisition/runs/${encodeURIComponent(parseRun.acq_run_id)}`);
  setLatestId("acquisition", parseRun.acq_run_id);
  setLatestId("discovery", acqRun.discovery_run_id);
  setContext({ parse_run_id: parseRunId, acq_run_id: parseRun.acq_run_id, discovery_run_id: acqRun.discovery_run_id, source_id: item.source_id });
  el("searchSourceContext").textContent = JSON.stringify(state.context, null, 2);
  window.location.hash = "#review";
}

async function loadAiSettings() {
  setText("aiSettingsState", "");
  try {
    const p = await apiGet("/v1/settings/ai-filter");
    el("aiEnabledSelect").value = p.use_ai_filter ? "true" : "false";
    el("aiModelInput").value = p.ai_model || "";
    el("aiBaseUrlInput").value = p.ai_base_url || "";
    el("aiApiKeyInput").value = "";
    setText("aiSettingsState", `AI filter ${p.ai_filter_active ? "active" : "disabled/warning"}; key=${p.has_api_key ? "present" : "missing"}`);
  } catch (err) {
    setText("aiSettingsState", `Load failed: ${err.message}`);
  }
}

async function saveAiSettings() {
  setText("aiSettingsState", "");
  try {
    const payload = {
      use_ai_filter: el("aiEnabledSelect").value === "true",
      ai_model: el("aiModelInput").value.trim(),
      ai_base_url: el("aiBaseUrlInput").value.trim(),
    };
    const key = el("aiApiKeyInput").value.trim();
    if (key) payload.ai_api_key = key;
    const p = await apiPost("/v1/settings/ai-filter", payload);
    setText("aiSettingsState", `Saved. AI filter ${p.ai_filter_active ? "active" : "disabled/warning"}; key=${p.has_api_key ? "present" : "missing"}`);
    el("aiApiKeyInput").value = "";
  } catch (err) {
    setText("aiSettingsState", `Save failed: ${err.message}`);
  }
}

function setApiStateText() {
  if (!AUTH_ENABLED) {
    setText("authState", "Auth disabled");
    setText("authModeHint", "No app token required");
    return;
  }
  if (!state.apiKey) {
    setText("authState", "Key not set");
    setText("authModeHint", "Manual token required");
    return;
  }
  if (state.tokenSource === "system") {
    setText("authState", "Using system token");
    setText("authModeHint", "System token mode (manual override allowed)");
    return;
  }
  setText("authState", "Using manual token");
  setText("authModeHint", "Manual override mode");
}

function getCopyId(kind) {
  if (kind === "discovery") return (el("latestDiscoveryId").textContent || "").trim();
  if (kind === "acquisition") return (el("latestAcqId").textContent || "").trim();
  return (el("latestParseId").textContent || "").trim();
}

async function copyLatestId(kind) {
  const id = getCopyId(kind);
  if (!id || id === "-") {
    setText("idCopyState", "No ID to copy.");
    return;
  }
  try {
    await navigator.clipboard.writeText(id);
    setText("idCopyState", `Copied ${kind} ID: ${id}`);
  } catch (_err) {
    setText("idCopyState", "Copy failed.");
  }
}

async function lookupRun(event) {
  event.preventDefault();
  setText("runsError", "");
  try {
    const phase = el("runPhaseSelect").value;
    const runId = el("runIdInput").value.trim();
    if (!runId) throw new Error("run id is required");
    const endpoint =
      phase === "discovery"
        ? `/v1/discovery/runs/${encodeURIComponent(runId)}`
        : phase === "acquisition"
          ? `/v1/acquisition/runs/${encodeURIComponent(runId)}`
          : `/v1/parse/runs/${encodeURIComponent(runId)}`;
    const payload = await apiGet(endpoint);
    upsertRunRow(phase, runId, payload);
    setLatestId(phase, runId);
    renderRunsTable();
  } catch (err) {
    setText("runsError", `Lookup failed: ${err.message}`);
  }
}

async function startDiscovery(event) {
  event.preventDefault();
  setText("dashboardError", "");
  try {
    const raw = el("startDiscoverySeeds").value;
    const seedQueries = raw.split(",").map((s) => s.trim()).filter(Boolean);
    if (!seedQueries.length) throw new Error("provide at least one seed query");
    const aiMode = el("startDiscoveryAiMode").value;
    const aiFilterEnabled = aiMode === "default" ? null : aiMode === "on";
    const result = await apiPost("/v1/discovery/runs", {
      seed_queries: seedQueries,
      max_iterations: Number(el("startDiscoveryMaxIterations").value),
      ai_filter_enabled: aiFilterEnabled,
    });
    setLatestId("discovery", result.run_id);
    el("discoverRunIdInput").value = result.run_id;
    el("reviewRunIdInput").value = result.run_id;
    setText("dashboardState", `Run created: ${result.run_id}`);
    await loadDashboard();
    await loadDiscover();
    window.location.hash = "#discover";
  } catch (err) {
    setText("dashboardError", `Start failed: ${err.message}`);
  }
}

async function loadReviewClick(event) {
  event.preventDefault();
  state.review.offset = 0;
  try {
    await loadReview();
  } catch (err) {
    setText("reviewError", `Load failed: ${err.message}`);
  }
}

async function handleReviewAction(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (!target.classList.contains("review-action")) return;
  const action = target.dataset.action || "";
  const sourceId = target.dataset.sourceId || "";

  if (action === "toggle") {
    if (!sourceId) return;
    if (state.review.expanded.has(sourceId)) state.review.expanded.delete(sourceId);
    else state.review.expanded.add(sourceId);
    try {
      await loadReview();
    } catch (err) {
      setText("reviewError", `Load failed: ${err.message}`);
    }
    return;
  }

  if (action !== "accept" && action !== "reject") return;
  if (!sourceId) return;
  try {
    await apiPost(`/v1/sources/${encodeURIComponent(sourceId)}/review`, { decision: action === "accept" ? "accept" : "reject" });
    setText("reviewState", `Reviewed ${sourceId}: ${action}`);
    await loadReview();
    await loadDashboard();
  } catch (err) {
    setText("reviewError", `Review failed: ${err.message}`);
  }
}

async function startAcquisition(event) {
  event.preventDefault();
  setText("acqError", "");
  try {
    const runId = el("startAcqRunId").value.trim();
    if (!runId) throw new Error("discovery run id is required");
    const retryFailedOnly = el("startAcqRetry").value === "true";
    const result = await apiPost("/v1/acquisition/runs", { run_id: runId, retry_failed_only: retryFailedOnly });
    setLatestId("acquisition", result.acq_run_id);
    el("documentsAcqRunIdInput").value = result.acq_run_id;
    setText("acqError", `Started acquisition: ${result.acq_run_id}`);
  } catch (err) {
    setText("acqError", `Start failed: ${err.message}`);
  }
}

async function startParse(event) {
  event.preventDefault();
  setText("parseError", "");
  try {
    const acqRunId = el("startParseAcqRunId").value.trim();
    if (!acqRunId) throw new Error("acquisition run id is required");
    const retryFailedOnly = el("startParseRetry").value === "true";
    const result = await apiPost("/v1/parse/runs", { acq_run_id: acqRunId, retry_failed_only: retryFailedOnly });
    setLatestId("parse", result.parse_run_id);
    el("searchParseRunIdInput").value = result.parse_run_id;
    setText("parseError", `Started parse: ${result.parse_run_id}`);
  } catch (err) {
    setText("parseError", `Start failed: ${err.message}`);
  }
}

async function handleDocumentsAction(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement) || !target.classList.contains("documents-action")) return;
  const action = target.dataset.action || "";
  const sourceId = target.dataset.sourceId || "";
  const discoveryRunId = target.dataset.discoveryRunId || "";

  if (action === "upload") {
    state.documents.selectedSourceId = sourceId;
    el("manualUploadSourceId").value = sourceId;
    setText("documentsState", `Upload target selected: ${sourceId}`);
    el("manualUploadFile").focus();
    return;
  }

  if (action === "retry") {
    try {
      await apiPost("/v1/acquisition/runs", { run_id: discoveryRunId, retry_failed_only: true });
      setText("documentsState", "Retry acquisition started.");
      await loadDocuments();
      await loadDashboard();
    } catch (err) {
      setText("documentsError", `Retry failed: ${err.message}`);
    }
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      if (comma < 0) {
        reject(new Error("file_read_failed"));
        return;
      }
      resolve(result.slice(comma + 1));
    };
    reader.onerror = () => reject(new Error("file_read_failed"));
    reader.readAsDataURL(file);
  });
}

async function registerManualUpload(event) {
  event.preventDefault();
  setText("documentsError", "");
  try {
    const acqRunId = getAcqRunId();
    const sourceId = (el("manualUploadSourceId").value || "").trim() || state.documents.selectedSourceId;
    const fileInput = el("manualUploadFile");
    const file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
    if (!acqRunId) throw new Error("acquisition run id is required");
    if (!sourceId) throw new Error("source id is required");
    if (!file) throw new Error("file is required");
    const contentBase64 = await fileToBase64(file);
    const res = await apiPost(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/manual-upload`, {
      source_id: sourceId,
      filename: file.name,
      content_base64: contentBase64,
      content_type: file.type || null,
    });
    setText("documentsState", `Registered artifact: ${res.artifact_id}`);
    await loadDocuments();
  } catch (err) {
    setText("documentsError", `Upload failed: ${err.message}`);
  }
}

async function exportManualCsv() {
  setText("documentsError", "");
  try {
    const acqRunId = getAcqRunId();
    if (!acqRunId) throw new Error("acquisition run id is required");
    await apiDownload(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/manual-downloads.csv`, `manual_downloads_${acqRunId}.csv`);
  } catch (err) {
    setText("documentsError", `Export failed: ${err.message}`);
  }
}

async function exportSourcesRaw() {
  setText("discoverError", "");
  try {
    const runId = getDiscoveryRunId();
    if (!runId) throw new Error("discovery run id is required");
    await apiDownload(`/v1/exports/sources_raw?run_id=${encodeURIComponent(runId)}`, `sources_raw_${runId}.json`);
  } catch (err) {
    setText("discoverError", `Export failed: ${err.message}`);
  }
}

async function exportManifest() {
  setText("acqError", "");
  try {
    const acqRunId = getAcqRunId();
    if (!acqRunId) throw new Error("acquisition run id is required");
    await apiDownload(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/manifest`, `manifest_${acqRunId}.json`);
  } catch (err) {
    setText("acqError", `Export failed: ${err.message}`);
  }
}

async function runGlobalSearch(event) {
  event.preventDefault();
  setText("globalSearchState", "");
  try {
    const query = el("globalSearchQuery").value.trim();
    if (!query) return;
    const limit = Number(el("globalSearchLimit").value);
    const payload = await apiGet(`/v1/search/global?q=${encodeURIComponent(query)}&limit=${limit}`);
    setText("globalSearchState", `results=${payload.total}`);
    if (payload.items && payload.items.length) {
      setContext(payload.items[0].context || {});
    }
  } catch (err) {
    setText("globalSearchState", `Search failed: ${err.message}`);
  }
}

async function handleSearchAction(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement) || !target.classList.contains("search-action")) return;
  const action = target.dataset.action || "";
  const index = Number(target.dataset.index || "-1");
  if (index < 0) return;
  try {
    if (action === "doc") await showSearchDoc(index);
    else if (action === "text") await showSearchText(index);
    else if (action === "source") await showSearchSource(index);
  } catch (err) {
    setText("searchError", `Action failed: ${err.message}`);
  }
}

async function refreshCurrentSection() {
  const section = activeSection();
  if (section === "build") return loadDashboard();
  if (section === "discover") return loadDiscover();
  if (section === "review" && state.review.loaded) return loadReview();
  if (section === "documents" && state.documents.loaded) return loadDocuments();
  if (section === "library" && state.search.loaded && state.search.payload) return runSearchData(state.search.payload);
  return true;
}

async function runPollCycle() {
  const section = activeSection();
  const interval = document.visibilityState === "hidden" ? POLL_BACKGROUND_MS : POLL_ACTIVE_MS;
  try {
    await loadSystemStatus();
    await refreshCurrentSection();
    setPollState(`Auto-refreshing #${section} every ${Math.round(interval / 1000)}s.`);
  } catch (err) {
    setPollState(`Stale data in #${section}: ${err.message}`, true);
  }
  schedulePoll();
}

function addListener(id, event, handler) {
  const node = el(id);
  if (node) node.addEventListener(event, handler);
}

function initTelemetry() {
  telemetrySessionId();
  document.addEventListener(
    "click",
    (event) => {
      const target = event.target instanceof HTMLElement ? event.target.closest("button, a, summary") : null;
      if (!target) return;
      emitTelemetryEvent("click", target);
    },
    true,
  );
  document.addEventListener(
    "change",
    (event) => {
      const target = event.target;
      if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement)) return;
      emitTelemetryEvent("change", target);
    },
    true,
  );
  document.addEventListener(
    "input",
    (event) => {
      const target = event.target;
      if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) return;
      emitDebouncedInputTelemetry(target);
    },
    true,
  );
  document.addEventListener(
    "submit",
    (event) => {
      const target = event.target;
      if (!(target instanceof HTMLFormElement)) return;
      emitTelemetryEvent("submit", target);
    },
    true,
  );
  window.addEventListener("hashchange", () => emitTelemetryEvent("navigate", document.body, activeSection()));
}

function initAuth() {
  const keyInput = el("apiKeyInput");
  const saveKeyBtn = el("saveApiKeyBtn");
  if (!AUTH_ENABLED) {
    if (keyInput) keyInput.style.display = "none";
    if (saveKeyBtn) saveKeyBtn.style.display = "none";
    state.apiKey = "";
    state.tokenSource = "none";
    setApiStateText();
    return;
  }

  const manualToken = localStorage.getItem("km_api_key");
  if (manualToken) {
    state.apiKey = manualToken;
    state.tokenSource = "manual";
  } else if (SYSTEM_TOKEN) {
    state.apiKey = SYSTEM_TOKEN;
    state.tokenSource = "system";
  }
  if (keyInput) keyInput.value = state.apiKey;

  addListener("saveApiKeyBtn", "click", () => {
    state.apiKey = (keyInput.value || "").trim();
    if (state.apiKey) {
      localStorage.setItem("km_api_key", state.apiKey);
      state.tokenSource = "manual";
    } else {
      localStorage.removeItem("km_api_key");
      if (SYSTEM_TOKEN) {
        state.apiKey = SYSTEM_TOKEN;
        state.tokenSource = "system";
        keyInput.value = state.apiKey;
      } else {
        state.tokenSource = "none";
      }
    }
    setApiStateText();
  });
  setApiStateText();
}

function initPagination() {
  addListener("reviewPrev", "click", async () => {
    const limit = Number(el("reviewLimit").value);
    state.review.offset = Math.max(0, state.review.offset - limit);
    try {
      await loadReview();
    } catch (err) {
      setText("reviewError", `Load failed: ${err.message}`);
    }
  });
  addListener("reviewNext", "click", async () => {
    const limit = Number(el("reviewLimit").value);
    state.review.offset += limit;
    try {
      await loadReview();
    } catch (err) {
      setText("reviewError", `Load failed: ${err.message}`);
    }
  });

  addListener("documentsPrev", "click", async () => {
    const limit = Number(el("documentsLimit").value);
    state.documents.offset = Math.max(0, state.documents.offset - limit);
    try {
      await loadDocuments();
    } catch (err) {
      setText("documentsError", `Load failed: ${err.message}`);
    }
  });
  addListener("documentsNext", "click", async () => {
    const limit = Number(el("documentsLimit").value);
    state.documents.offset += limit;
    try {
      await loadDocuments();
    } catch (err) {
      setText("documentsError", `Load failed: ${err.message}`);
    }
  });
}

function init() {
  initAuth();
  initTelemetry();
  updateSectionVisibility();

  addListener("startDiscoveryForm", "submit", startDiscovery);
  addListener("loadDiscoverBtn", "click", async () => {
    try {
      await loadDiscover();
    } catch (err) {
      setText("discoverError", `Load failed: ${err.message}`);
    }
  });
  addListener("discoverTechnicalForm", "submit", async (event) => {
    event.preventDefault();
    try {
      await loadDiscover();
      setText("discoverTechState", "Technical listing settings saved.");
    } catch (err) {
      setText("discoverError", `Load failed: ${err.message}`);
    }
  });
  addListener("downloadSourcesRawBtn", "click", exportSourcesRaw);

  addListener("reviewForm", "submit", loadReviewClick);
  addListener("reviewRows", "click", handleReviewAction);

  addListener("documentsForm", "submit", async (event) => {
    event.preventDefault();
    state.documents.offset = 0;
    try {
      await loadDocuments();
    } catch (err) {
      setText("documentsError", `Load failed: ${err.message}`);
    }
  });
  addListener("documentsRows", "click", handleDocumentsAction);
  addListener("manualUploadForm", "submit", registerManualUpload);
  addListener("manualExportCsvBtn", "click", exportManualCsv);

  addListener("searchForm", "submit", runSearch);
  addListener("searchRows", "click", handleSearchAction);

  addListener("loadAiSettingsBtn", "click", loadAiSettings);
  addListener("saveAiSettingsBtn", "click", saveAiSettings);
  addListener("globalSearchForm", "submit", runGlobalSearch);
  addListener("runLookupForm", "submit", lookupRun);
  addListener("runFilterPhase", "change", renderRunsTable);
  addListener("runFilterStatus", "change", renderRunsTable);
  addListener("startAcqForm", "submit", startAcquisition);
  addListener("startParseForm", "submit", startParse);
  addListener("downloadManifestBtn", "click", exportManifest);

  addListener("copyDiscoveryIdBtn", "click", () => copyLatestId("discovery"));
  addListener("copyAcqIdBtn", "click", () => copyLatestId("acquisition"));
  addListener("copyParseIdBtn", "click", () => copyLatestId("parse"));
  addListener("statusNextActionBtn", "click", () => {
    const route = state.statusStrip.nextActionRoute || "build";
    window.location.hash = `#${route}`;
  });

  initPagination();

  document.addEventListener("visibilitychange", schedulePoll);
  window.addEventListener("hashchange", () => {
    updateSectionVisibility();
    schedulePoll();
  });

  renderRunsTable();
  loadSystemStatus();
  loadDashboard();
  loadAiSettings();
  emitTelemetryEvent("navigate", document.body, activeSection());
  schedulePoll();
}

document.addEventListener("DOMContentLoaded", init);
