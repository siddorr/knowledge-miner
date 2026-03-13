import { createApiClient } from "./hmi/api.js";
import { createDocumentsModule } from "./hmi/documents.js";
import { createLibraryModule } from "./hmi/library.js";
import { createReviewModule } from "./hmi/review.js";
import { createSessionModule } from "./hmi/session.js";
import { createTelemetryClient } from "./hmi/telemetry.js";
import {
  AUTH_ENABLED,
  BC_NAME,
  LAUNCH_SECTION,
  LEADER_HEARTBEAT_MS,
  LEADER_STALE_MS,
  LEADER_STORAGE_KEY,
  POLL_ACTIVE_MS,
  POLL_BACKGROUND_MS,
  POLL_DISCONNECTED_IDLE_MS,
  SESSIONS_AUTO_RESTORE_KEY,
  SESSIONS_STORAGE_KEY,
  SYSTEM_TOKEN,
  TELEMETRY_INPUT_DEBOUNCE_MS,
  createInitialState,
} from "./hmi/state.js";

const state = createInitialState();

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

function isReadRateLimitedError(err) {
  return String(err?.message || "").includes("read_rate_limited");
}

let apiClient = null;

function parseLeaderRecord() {
  try {
    const raw = localStorage.getItem(LEADER_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed.tabId !== "string" || typeof parsed.ts !== "number") return null;
    return parsed;
  } catch (_err) {
    return null;
  }
}

function isLeaderAlive(record) {
  if (!record) return false;
  return Date.now() - Number(record.ts || 0) <= LEADER_STALE_MS;
}

function publishLeaderHeartbeat() {
  localStorage.setItem(LEADER_STORAGE_KEY, JSON.stringify({ tabId: state.multiTab.tabId, ts: Date.now() }));
}

function setLeaderMode(enabled) {
  if (state.multiTab.isLeader === enabled) return;
  state.multiTab.isLeader = enabled;
  if (enabled) {
    setPollState("Leader tab mode: this tab performs background refresh.");
    openLiveUpdatesChannel();
    schedulePoll();
  } else {
    if (state.live.eventSource) {
      state.live.eventSource.close();
      state.live.eventSource = null;
    }
    setLiveUpdatesState(false);
    setPollState("Follower tab mode: waiting for leader updates.");
  }
}

function broadcastMessage(type, payload = {}) {
  const channel = state.multiTab.channel;
  if (!channel) return;
  try {
    channel.postMessage({ type, payload, from: state.multiTab.tabId, ts: Date.now() });
  } catch (_err) {
    // ignore cross-tab channel errors
  }
}

function broadcastSnapshot() {
  if (!state.multiTab.isLeader) return;
  broadcastMessage("ui_snapshot", {
    footerSystemReady: (el("footerSystemReady")?.textContent || "").trim(),
    footerAiReady: (el("footerAiReady")?.textContent || "").trim(),
    footerDbReady: (el("footerDbReady")?.textContent || "").trim(),
    pollState: (el("pollState")?.textContent || "").trim(),
    pendingReview: (el("statusPendingReview")?.textContent || "0").trim(),
    awaitingDocs: (el("statusAwaitingDocs")?.textContent || "0").trim(),
    docFailures: (el("statusDocFailures")?.textContent || "0").trim(),
    reviewBadge: (el("reviewNavBadge")?.textContent || "0").trim(),
    documentsBadge: (el("documentsNavBadge")?.textContent || "0").trim(),
    latestDiscovery: state.latest.discovery || "",
    latestAcquisition: state.latest.acquisition || "",
    latestParse: state.latest.parse || "",
  });
}

function applySnapshot(payload) {
  if (!payload) return;
  if (payload.footerSystemReady) setText("footerSystemReady", payload.footerSystemReady);
  if (payload.footerAiReady) setText("footerAiReady", payload.footerAiReady);
  if (payload.footerDbReady) setText("footerDbReady", payload.footerDbReady);
  if (payload.pollState) setPollState(`Follower sync: ${payload.pollState}`);
  if (payload.pendingReview !== undefined) setText("statusPendingReview", String(payload.pendingReview));
  if (payload.awaitingDocs !== undefined) setText("statusAwaitingDocs", String(payload.awaitingDocs));
  if (payload.docFailures !== undefined) setText("statusDocFailures", String(payload.docFailures));
  if (payload.reviewBadge !== undefined) setText("reviewNavBadge", String(payload.reviewBadge));
  if (payload.documentsBadge !== undefined) setText("documentsNavBadge", String(payload.documentsBadge));
  if (payload.latestDiscovery) setText("latestDiscoveryId", payload.latestDiscovery);
  if (payload.latestAcquisition) setText("latestAcqId", payload.latestAcquisition);
  if (payload.latestParse) setText("latestParseId", payload.latestParse);
}

function initMultiTabSync() {
  if (typeof BroadcastChannel !== "undefined") {
    const channel = new BroadcastChannel(BC_NAME);
    state.multiTab.channel = channel;
    channel.onmessage = (event) => {
      const data = event.data || {};
      if (!data || data.from === state.multiTab.tabId) return;
      if (data.type === "ui_snapshot" && !state.multiTab.isLeader) {
        applySnapshot(data.payload || {});
      }
      if (data.type === "leader_heartbeat") {
        const record = parseLeaderRecord();
        if (record && record.tabId !== state.multiTab.tabId && isLeaderAlive(record)) {
          setLeaderMode(false);
        }
      }
    };
  }

  const tick = () => {
    const current = parseLeaderRecord();
    if (!isLeaderAlive(current) || current?.tabId === state.multiTab.tabId) {
      publishLeaderHeartbeat();
      setLeaderMode(true);
      broadcastMessage("leader_heartbeat");
    } else {
      setLeaderMode(false);
    }
  };

  tick();
  state.multiTab.heartbeatTimer = setInterval(tick, LEADER_HEARTBEAT_MS);

  window.addEventListener("storage", (event) => {
    if (event.key !== LEADER_STORAGE_KEY) return;
    const current = parseLeaderRecord();
    if (!current || !isLeaderAlive(current)) return;
    if (current.tabId !== state.multiTab.tabId) setLeaderMode(false);
  });
}

function hasActiveWork() {
  if (state.busy.count > 0) return true;
  return state.runRows.some((row) => row.status === "queued" || row.status === "running");
}

function setLiveUpdatesState(connected) {
  state.live.connected = connected;
  setText("liveUpdatesState", `Live updates: ${connected ? "connected" : "disconnected"}`);
}

function setButtonBusy(id, busy) {
  const node = el(id);
  if (node) node.disabled = busy;
}

function setButtonRunning(id, busy) {
  const node = el(id);
  if (!node) return;
  if (busy) {
    if (!node.dataset.originalLabel) node.dataset.originalLabel = node.textContent || "";
    node.textContent = "Running...";
  } else if (node.dataset.originalLabel) {
    node.textContent = node.dataset.originalLabel;
  }
}

function setGlobalBusy(phase, busy) {
  if (busy) state.busy.count += 1;
  else state.busy.count = Math.max(0, state.busy.count - 1);
  if (busy && phase) state.busy.phase = phase;
  if (state.busy.count === 0) state.busy.phase = "";
  state.busy.updatedAt = new Date().toISOString();
  const active = state.busy.count > 0;
  setText(
    "inProgressState",
    active ? `In progress: ${state.busy.phase || "operation"} (updated ${state.busy.updatedAt})` : "Idle",
  );
  const banner = el("inProgressBanner");
  if (banner) banner.hidden = !active;
  if (active) emitTelemetryEvent("change", document.body, `busy:${state.busy.phase}:enter`);
  else emitTelemetryEvent("change", document.body, "busy:exit");
}

async function runBusy(phase, buttonIds, fn) {
  setGlobalBusy(phase, true);
  for (const id of buttonIds || []) {
    setButtonBusy(id, true);
    setButtonRunning(id, true);
  }
  try {
    const out = await fn();
    emitTelemetryEvent("change", document.body, `action:${phase}:complete`);
    return out;
  } catch (err) {
    emitTelemetryEvent("change", document.body, `action:${phase}:fail`);
    throw err;
  } finally {
    for (const id of buttonIds || []) {
      setButtonBusy(id, false);
      setButtonRunning(id, false);
    }
    setGlobalBusy(phase, false);
  }
}

function requiredKey() {
  if (!AUTH_ENABLED) return;
  if (!state.apiKey) throw new Error("API key is required");
}

function authHeaders() {
  if (!AUTH_ENABLED || !state.apiKey) return {};
  return { Authorization: `Bearer ${state.apiKey}` };
}

const telemetry = createTelemetryClient({
  state,
  authEnabled: AUTH_ENABLED,
  authHeaders,
  activeSection,
  telemetryInputDebounceMs: TELEMETRY_INPUT_DEBOUNCE_MS,
});

function emitTelemetryEvent(eventType, target, forcedValuePreview = undefined) {
  telemetry.emitEvent(eventType, target, forcedValuePreview);
}

const documents = createDocumentsModule({
  state,
  el,
  setText,
  apiPost,
  refreshDocuments,
  loadDashboard,
  ensureDiscoveryRunExists,
  getDiscoveryRunId,
  getAcqRunId,
  runBusy,
  emitTelemetryEvent,
  setLatestId,
  ensureAcquisitionRunContext,
  requiredKey,
  authHeaders,
  updateDocumentsSelectionControls,
});

const review = createReviewModule({
  state,
  setText,
  apiPost,
  getDiscoveryRunId,
  refreshReview,
  loadDashboard,
  renderReviewDetails,
  renderFastReviewCard,
  updateReviewSelectionControls,
  activeSection,
});

  const library = createLibraryModule({
  state,
  el,
  setText,
  renderTable,
  escapeHtml,
  apiGet,
  apiPost,
  apiDownload,
  runBusy,
  getParseRunId,
  setLatestId,
  setContext,
});

const sessionModule = createSessionModule({
  state,
  el,
  setText,
  escapeHtml,
  activeSection,
  setLatestId,
  sourceFingerprint,
  renderBuildTopics,
  setBuildTab,
  apiGet,
  loadDashboard,
  scheduleReviewAutoLoad,
  refreshDocuments,
  runSearch,
  addListener,
  createNewSession: createNewTopic,
  sessionsStorageKey: SESSIONS_STORAGE_KEY,
  sessionsAutoRestoreKey: SESSIONS_AUTO_RESTORE_KEY,
});

async function apiGet(path) {
  if (!apiClient) throw new Error("api_not_initialized");
  return apiClient.get(path);
}

async function apiPost(path, payload) {
  if (!apiClient) throw new Error("api_not_initialized");
  return apiClient.post(path, payload);
}

async function apiDownload(path, filename) {
  if (!apiClient) throw new Error("api_not_initialized");
  return apiClient.download(path, filename);
}

async function coalescedRefresh(key, minIntervalMs, fn, force = false) {
  const inflight = state.refresh.inflight.get(key);
  if (inflight) return inflight;
  const last = Number(state.refresh.lastAt.get(key) || 0);
  if (!force && Date.now() - last < minIntervalMs) return true;
  const promise = (async () => {
    try {
      return await fn();
    } finally {
      state.refresh.inflight.delete(key);
      state.refresh.lastAt.set(key, Date.now());
    }
  })();
  state.refresh.inflight.set(key, promise);
  return promise;
}

function setContext(patch) {
  state.context = { ...state.context, ...patch };
  const out = el("globalContext");
  if (out) out.textContent = JSON.stringify(state.context, null, 2);
}

function loadSessionsFromStorage() {
  return sessionModule.loadSessionsFromStorage();
}

function saveSessionsToStorage() {
  return sessionModule.saveSessionsToStorage();
}

function sessionSummaryLabel(item) {
  return sessionModule.sessionSummaryLabel(item);
}

function renderSessionHistory() {
  return sessionModule.renderSessionHistory();
}

function captureSessionState() {
  return sessionModule.captureSessionState();
}

function applySessionState(snapshot) {
  return sessionModule.applySessionState(snapshot);
}

function saveCurrentSession() {
  return sessionModule.saveCurrentSession();
}

async function validateSessionSnapshot(snapshot) {
  return sessionModule.validateSessionSnapshot(snapshot);
}

async function loadSelectedSession() {
  return sessionModule.loadSelectedSession();
}

function deleteSelectedSession() {
  return sessionModule.deleteSelectedSession();
}

function activeSection() {
  const fallback = ["build", "review", "documents", "library", "advanced"].includes(LAUNCH_SECTION) ? LAUNCH_SECTION : "build";
  const id = window.location.hash.replace("#", "") || fallback;
  const valid = ["build", "discover", "review", "documents", "library", "advanced"];
  return valid.includes(id) ? id : fallback;
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
  state.pollTimer = null;
  if (!state.multiTab.isLeader) return;
  if (document.visibilityState === "hidden") {
    setPollState("Hidden tab: periodic refresh paused.");
    return;
  }
  const active = hasActiveWork();
  if (state.live.connected && !active) {
    setPollState("Live updates connected. Idle mode: interval polling paused.");
    return;
  }
  let interval;
  if (!state.live.connected && !active) interval = POLL_DISCONNECTED_IDLE_MS;
  else interval = document.visibilityState === "hidden" ? POLL_BACKGROUND_MS : POLL_ACTIVE_MS;
  const throttleMs = Math.max(0, state.net.readThrottleUntil - Date.now());
  if (throttleMs > 0) interval = Math.max(interval, throttleMs);
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
  if (kind === "discovery") {
    setText("latestDiscoveryId", trimmed || "-");
    setText("statusActiveDiscoveryRun", trimmed || "-");
    if (el("discoverRunIdInput")) el("discoverRunIdInput").value = trimmed;
    if (el("startAcqRunId")) el("startAcqRunId").value = trimmed;
  }
  if (kind === "acquisition") {
    setText("latestAcqId", trimmed || "-");
    if (el("documentsAcqRunIdInput")) el("documentsAcqRunIdInput").value = trimmed;
    if (el("startParseAcqRunId")) el("startParseAcqRunId").value = trimmed;
  }
  if (kind === "parse") {
    setText("latestParseId", trimmed || "-");
    if (el("searchParseRunIdInput")) el("searchParseRunIdInput").value = trimmed;
  }
  if (kind === "discovery" && activeSection() === "review") {
    state.review.offset = 0;
    scheduleReviewAutoLoad("run_context_changed");
  }
}

function clearRunInputs() {
  const ids = ["discoverRunIdInput", "documentsAcqRunIdInput", "searchParseRunIdInput"];
  for (const id of ids) {
    const node = el(id);
    if (node) node.value = "";
  }
}

function resetStaleRunContext(reason) {
  const key = `${reason}:${state.latest.discovery}:${state.latest.acquisition}:${state.latest.parse}`;
  if (state.stale.lastResetKey === key) return false;
  state.stale.lastResetKey = key;
  setLatestId("discovery", "");
  setLatestId("acquisition", "");
  setLatestId("parse", "");
  clearRunInputs();
  state.review.selected.clear();
  state.review.loaded = false;
  state.documents.selected.clear();
  state.documents.loaded = false;
  state.search.loaded = false;
  setText("reviewState", "No active runs found. Start from Discover -> Run One Iteration.");
  setText("discoverState", "No active runs found. Start from Discover -> Run One Iteration.");
  setText("documentsState", "No active runs found. Start from Discover -> Run One Iteration.");
  emitTelemetryEvent("change", document.body, "stale_context_reset");
  return true;
}

async function useLatestRunContext(interactive = false) {
  const payload = await apiGet("/v1/runs/latest");
  const d = (payload.discovery_run_id || "").trim();
  const a = (payload.acquisition_run_id || "").trim();
  const p = (payload.parse_run_id || "").trim();
  if (!d && !a && !p) {
    resetStaleRunContext("use_latest_none");
    return false;
  }
  const prev = { ...state.latest };
  const changed = (!!d && d !== prev.discovery) || (!!a && a !== prev.acquisition) || (!!p && p !== prev.parse);
  if (interactive && changed) {
    const ok = window.confirm(
      `Switch active context?\nDiscovery: ${prev.discovery || "-"} -> ${d || prev.discovery || "-"}\nAcquisition: ${prev.acquisition || "-"} -> ${a || prev.acquisition || "-"}\nParse: ${prev.parse || "-"} -> ${p || prev.parse || "-"}`,
    );
    if (!ok) return false;
  }
  if (d) setLatestId("discovery", d);
  if (a) setLatestId("acquisition", a);
  if (p) setLatestId("parse", p);
  if (changed) {
    emitTelemetryEvent(
      "change",
      el("useLatestRunBtn") || document.body,
      `context_switch discovery:${prev.discovery || "-"}->${state.latest.discovery || "-"} acq:${prev.acquisition || "-"}->${state.latest.acquisition || "-"} parse:${prev.parse || "-"}->${state.latest.parse || "-"}`,
    );
  }
  setText("reviewState", "Loaded latest run context.");
  return true;
}

function getDiscoveryRunId() {
  return (state.latest.discovery || "").trim();
}

function getAcqRunId() {
  return (state.latest.acquisition || "").trim();
}

function getParseRunId() {
  return (state.latest.parse || "").trim();
}

async function ensureDiscoveryRunExists(runId) {
  if (!runId) throw new Error("discovery run context is required");
  try {
    await apiGet(`/v1/discovery/runs/${encodeURIComponent(runId)}`);
    return true;
  } catch (err) {
    if (String(err.message || "").includes("run_not_found")) {
      throw new Error("Active discovery run is missing. Use Latest or start a new run.");
    }
    throw err;
  }
}

async function ensureAcquisitionRunExists(acqRunId) {
  if (!acqRunId) throw new Error("acquisition run context is required");
  try {
    await apiGet(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}`);
    return true;
  } catch (err) {
    if (String(err.message || "").includes("run_not_found")) {
      throw new Error("Active acquisition run is missing. Use Latest or start acquisition again.");
    }
    throw err;
  }
}

async function ensureAcquisitionRunContext({ retryFailedOnly = false } = {}) {
  let acqRunId = getAcqRunId();
  if (acqRunId) {
    try {
      await ensureAcquisitionRunExists(acqRunId);
      return acqRunId;
    } catch (err) {
      if (!String(err.message || "").includes("run_not_found")) throw err;
      setLatestId("acquisition", "");
      acqRunId = "";
    }
  }

  const runId = getDiscoveryRunId();
  if (!runId) throw new Error("discovery run context is required");
  await ensureDiscoveryRunExists(runId);
  const next = await apiPost("/v1/acquisition/runs", { run_id: runId, retry_failed_only: retryFailedOnly });
  setLatestId("acquisition", next.acq_run_id);
  return next.acq_run_id;
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

function formatReviewSignals(row) {
  const out = [];
  if ((row.title || "").toLowerCase().includes("upw")) out.push("upw");
  if ((row.title || "").toLowerCase().includes("semiconductor")) out.push("semiconductor");
  if ((row.abstract || "").toLowerCase().includes("contamination")) out.push("contamination control");
  if (!out.length) out.push("keywords pending");
  return out.join(", ");
}

function reviewStatusLabel(row) {
  if (!row) return "-";
  if (row.review_status === "needs_review") return "Pending";
  if (row.accepted || row.review_status === "auto_accept" || row.review_status === "human_accept") return "Accepted";
  if (row.review_status === "human_later") return "Later";
  return "Rejected";
}

function renderReviewDetails(row) {
  if (!row) {
    setText("reviewDetailTitle", "No source selected.");
    setText("reviewDetailScore", "-");
    setText("reviewDetailStatus", "-");
    setText("reviewDetailMeta", "Year: - | Journal: - | Citations: - | Authors: - | Link: -");
    setText("reviewDetailAbstract", "No source selected.");
    setText("reviewDetailSignals", "-");
    const link = el("reviewDetailLink");
    if (link) link.href = "#";
    return;
  }
  setText("reviewDetailTitle", row.title || "");
  setText("reviewDetailScore", String(row.heuristic_score ?? row.relevance_score ?? "-"));
  setText("reviewDetailStatus", reviewStatusLabel(row));
  const year = row.year ?? "-";
  const journal = row.journal || "-";
  const citations = row.citations ?? row.citation_count ?? "-";
  const authors = Array.isArray(row.authors) ? row.authors.slice(0, 3).join(", ") : row.authors || "-";
  const link = row.url || "-";
  setText(
    "reviewDetailMeta",
    `Year: ${year} | Journal: ${journal} | Citations: ${citations} | Authors: ${authors} | Link: ${link}`,
  );
  setText("reviewDetailAbstract", row.abstract || "");
  setText("reviewDetailSignals", formatReviewSignals(row));
  const link = el("reviewDetailLink");
  if (link) link.href = row.url || "#";
}

function setReviewMode(mode) {
  state.review.mode = mode === "fast" ? "fast" : "table";
  const fast = state.review.mode === "fast";
  const panel = el("fastReviewPanel");
  const tableWrap = el("reviewRows")?.closest(".table-wrap");
  if (panel) panel.hidden = !fast;
  if (tableWrap) tableWrap.hidden = fast;
  const paginationRow = el("reviewPaginationRow");
  if (paginationRow && fast) paginationRow.hidden = true;
  if (fast) renderFastReviewCard();
}

function renderFastReviewCard() {
  const total = state.review.items.length;
  if (!total) {
    setText("fastReviewPosition", "Paper 0 of 0");
    setText("fastReviewCard", "No source selected.");
    return;
  }
  state.review.activeIndex = Math.max(0, Math.min(state.review.activeIndex, total - 1));
  const row = state.review.items[state.review.activeIndex];
  setText("fastReviewPosition", `Paper ${state.review.activeIndex + 1} of ${total}`);
  setText(
    "fastReviewCard",
    `Title: ${row.title || ""}\n\nScore: ${row.heuristic_score ?? row.relevance_score ?? "-"}\nStatus: ${reviewStatusLabel(row)}\n\nAbstract:\n${row.abstract || ""}\n\nAI signals: ${formatReviewSignals(row)}`,
  );
  renderReviewDetails(row);
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

function paginationState(total, offset, limit) {
  const safeTotal = Math.max(0, Number(total || 0));
  const safeLimit = Math.max(1, Number(limit || 1));
  const safeOffset = Math.max(0, Number(offset || 0));
  const hasItems = safeTotal > 0;
  const hasPrev = hasItems && safeOffset > 0;
  const hasNext = hasItems && safeOffset + safeLimit < safeTotal;
  return {
    has_items: hasItems,
    has_prev: hasPrev,
    has_next: hasNext,
    is_single_page: !hasItems || safeTotal <= safeLimit,
  };
}

function applyPaginationControls(prefix, total, offset, limit) {
  const status = paginationState(total, offset, limit);
  const row = el(`${prefix}PaginationRow`);
  const prev = el(`${prefix}Prev`);
  const next = el(`${prefix}Next`);
  if (row) row.hidden = status.is_single_page;
  if (prev) prev.disabled = !status.has_prev;
  if (next) next.disabled = !status.has_next;
}

function updateReviewSelectionControls() {
  const hasRows = state.review.items.length > 0;
  setButtonBusy("reviewSelectAllBtn", !hasRows);
  setButtonBusy("reviewDeselectAllBtn", !hasRows);
  const hasSelection = state.review.selected.size > 0;
  setButtonBusy("reviewBatchAcceptBtn", !hasSelection);
  setButtonBusy("reviewBatchRejectBtn", !hasSelection);
}

function updateDocumentsSelectionControls() {
  const hasRows = state.documents.items.length > 0;
  setButtonBusy("documentsSelectAllBtn", !hasRows);
  setButtonBusy("documentsDeselectAllBtn", !hasRows);
  const hasSelection = state.documents.selected.size > 0;
  setButtonBusy("documentsCopySelectedBtn", !hasSelection);
}

function activeTopic() {
  return state.build.topics.find((t) => t.id === state.build.activeTopicId) || state.build.topics[0];
}

function renderBuildDetails() {
  const session = activeTopic();
  if (!session) {
    setText("buildDetails", "No session selected.");
    return;
  }
  const coverage = state.build.coverageByTopic[session.id] || {
    candidates: 0,
    accepted: 0,
    pending_review: 0,
    awaiting_documents: 0,
    failed_documents: 0,
  };
  setText(
    "buildDetails",
    JSON.stringify(
      {
        session_id: session.id,
        session_name: session.name,
        selected_tab: state.build.activeTab,
        session_query: state.build.topicQueriesByTopic[session.id] || "",
        latest_discovery_run: state.latest.discovery || null,
        coverage,
      },
      null,
      2,
    ),
  );
  setText("statusActiveSession", session.name);
}

function renderBuildSources() {
  const session = activeTopic();
  const sessionId = session?.id || "";
  const rows = state.build.stagedSourcesByTopic[sessionId] || [];
  renderTable(
    "buildSourcesRows",
    rows.map((value) => `<tr><td>${escapeHtml(session?.name || "")}</td><td>${escapeHtml(value)}</td></tr>`),
    2,
  );
}

function renderBuildTopics() {
  const host = el("buildTopicList");
  if (!host) return;
  host.innerHTML = state.build.topics
    .map((topic) => {
      const active = topic.id === state.build.activeTopicId ? " active" : "";
      const c = state.build.coverageByTopic[topic.id] || {
        candidates: 0,
        accepted: 0,
        awaiting_documents: 0,
        failed_documents: 0,
      };
      const badge = `c:${c.candidates} a:${c.accepted} w:${c.awaiting_documents} f:${c.failed_documents}`;
      return `<button type="button" class="topic-btn${active}" data-topic-id="${escapeHtml(topic.id)}">${escapeHtml(topic.name)} <small>${escapeHtml(badge)}</small></button>`;
    })
    .join("");
  const queryInput = el("buildTopicQuery");
  if (queryInput) queryInput.value = state.build.topicQueriesByTopic[state.build.activeTopicId] || "";
  renderBuildDetails();
  renderBuildSources();
}

function setBuildTab(tab) {
  state.build.activeTab = tab;
  const tabs = ["add-sources", "queries", "runs"];
  const tabMap = {
    "add-sources": ["buildTabAddSources", "buildTabPanelAddSources"],
    queries: ["buildTabQueries", "buildTabPanelQueries"],
    runs: ["buildTabRuns", "buildTabPanelRuns"],
  };
  for (const id of tabs) {
    const [btnId, panelId] = tabMap[id];
    const btn = el(btnId);
    const panel = el(panelId);
    if (btn) btn.classList.toggle("active", id === tab);
    if (panel) panel.hidden = id !== tab;
  }
  renderBuildDetails();
}

function sourceFingerprint(value) {
  return value.trim().toLowerCase();
}

function topicKeywords(topic) {
  const query = state.build.topicQueriesByTopic[topic.id] || "";
  const raw = `${topic.name} ${query}`.toLowerCase();
  return raw
    .split(/[^a-z0-9]+/g)
    .map((part) => part.trim())
    .filter((part) => part.length >= 3);
}

function topicForSource(source, topicList) {
  if (!topicList.length) return null;
  if (topicList.length === 1) return topicList[0].id;
  const hay = `${source.title || ""} ${source.abstract || ""} ${source.url || ""}`.toLowerCase();
  let bestTopicId = topicList[0].id;
  let bestScore = -1;
  for (const topic of topicList) {
    const keywords = topicKeywords(topic);
    let score = 0;
    for (const kw of keywords) {
      if (hay.includes(kw)) score += 1;
    }
    if (score > bestScore) {
      bestScore = score;
      bestTopicId = topic.id;
    }
  }
  return bestTopicId;
}

function emptyTopicCoverage() {
  return {
    candidates: 0,
    accepted: 0,
    pending_review: 0,
    awaiting_documents: 0,
    failed_documents: 0,
  };
}

function mergeTopicCoverage(queueItems, sources) {
  const byTopic = {};
  const sourceToTopic = new Map();
  for (const topic of state.build.topics) byTopic[topic.id] = emptyTopicCoverage();
  const fallbackTopicId = state.build.activeTopicId || state.build.topics[0]?.id || "topic_default";

  for (const source of sources) {
    const topicId = topicForSource(source, state.build.topics) || fallbackTopicId;
    sourceToTopic.set(source.id, topicId);
    const bucket = byTopic[topicId] || (byTopic[topicId] = emptyTopicCoverage());
    bucket.candidates += 1;
    if (source.accepted || source.review_status === "auto_accept" || source.review_status === "human_accept") {
      bucket.accepted += 1;
    }
  }

  for (const item of queueItems) {
    const topicId = sourceToTopic.get(item.source_id) || fallbackTopicId;
    const bucket = byTopic[topicId] || (byTopic[topicId] = emptyTopicCoverage());
    if (item.phase === "discovery" && item.status === "needs_review") {
      bucket.pending_review += 1;
    }
    if (item.phase === "acquisition") {
      bucket.awaiting_documents += 1;
      if (item.status === "failed" || item.status === "partial") {
        bucket.failed_documents += 1;
      }
    }
  }
  return byTopic;
}

function applyActiveTopicCoverageToShell() {
  const c = state.build.coverageByTopic[state.build.activeTopicId] || emptyTopicCoverage();
  setText("reviewNavBadge", String(c.pending_review));
  setText("documentsNavBadge", String(c.awaiting_documents));
  updateStatusStrip({
    pendingReview: c.pending_review,
    awaitingDocs: c.awaiting_documents,
    docFailures: c.failed_documents,
    lastRunState: (el("statusLastRun")?.textContent || "").trim() || "none",
    activeTopic: activeTopic()?.name || "Default Session",
  });
}

function copyFeedbackIdForTarget(sourceNode, explicitFeedbackId = "") {
  if (explicitFeedbackId) return explicitFeedbackId;
  const sectionId = sourceNode?.closest("section")?.id || "";
  if (sectionId === "review") return "reviewState";
  if (sectionId === "documents") return "documentsState";
  if (sectionId === "library") return "searchState";
  if (sectionId === "discover") return "discoverState";
  if (sectionId === "advanced") return "idCopyState";
  return "addSourceState";
}

async function copyFieldValue(targetId, explicitFeedbackId = "", sourceNode = null) {
  const node = el(targetId);
  if (!node) return;
  const feedbackId = copyFeedbackIdForTarget(sourceNode, explicitFeedbackId);
  const hasValue = "value" in node;
  let value = hasValue ? String(node.value || "").trim() : "";
  if (!value) value = String(node.textContent || "").trim();
  if (!value) {
    setText(feedbackId, "Nothing to copy.");
    return;
  }
  try {
    await navigator.clipboard.writeText(value);
    setText(feedbackId, "Copied");
  } catch (_err) {
    setText(feedbackId, "Copy failed.");
  }
}

function updateStatusStrip({
  pendingReview,
  awaitingDocs,
  docFailures,
  lastRunState,
  activeTopic = "Default Session",
}) {
  setText("statusActiveSession", activeTopic);
  setText("statusPendingReview", String(pendingReview));
  setText("statusAwaitingDocs", String(awaitingDocs));
  setText("statusDocFailures", String(docFailures));
  setText("statusActiveDiscoveryRun", state.latest.discovery || "-");
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

function pendingReviewRunIds(queueItems) {
  const ids = new Set();
  for (const row of queueItems || []) {
    if (!row || row.phase !== "discovery" || row.status !== "needs_review") continue;
    const runId = String(row.run_id || row.context?.discovery_run_id || "").trim();
    if (runId) ids.add(runId);
  }
  return Array.from(ids);
}

async function ensureReviewRunContext() {
  if (getDiscoveryRunId()) return true;
  const queue = await apiGet("/v1/work-queue?limit=200&offset=0");
  const runIds = pendingReviewRunIds(queue.items || []);
  if (runIds.length === 1) {
    setLatestId("discovery", runIds[0]);
    emitTelemetryEvent("change", el("review"), `review_autoload:resolved_run run_id=${runIds[0]}`);
    return true;
  }
  if (runIds.length > 1) {
    emitTelemetryEvent("change", el("review"), `review_autoload:multiple_runs count=${runIds.length}`);
    const latest = await apiGet("/v1/runs/latest");
    const latestRunId = String(latest.discovery_run_id || "").trim();
    if (latestRunId) {
      setLatestId("discovery", latestRunId);
      emitTelemetryEvent("change", el("review"), `review_autoload:resolved_run run_id=${latestRunId}`);
      return true;
    }
    return false;
  }
  const latest = await apiGet("/v1/runs/latest");
  const latestRunId = String(latest.discovery_run_id || "").trim();
  if (latestRunId) {
    setLatestId("discovery", latestRunId);
    emitTelemetryEvent("change", el("review"), `review_autoload:resolved_run run_id=${latestRunId}`);
    return true;
  }
  emitTelemetryEvent("change", el("review"), "review_autoload:no_run_context");
  return false;
}

async function loadSystemStatus() {
  try {
    const payload = await apiGet("/v1/system/status");
    const provider = payload.provider_readiness || {};
    const brave = provider.brave && provider.brave.api_key_present ? "brave:ready" : "brave:missing-key";
    const s2 = provider.semantic_scholar && provider.semantic_scholar.api_key_present ? "s2:ready" : "s2:limited";
    const ai = payload.ai_filter_active ? "active" : payload.ai_filter_warning ? "warning" : "disabled";
    const db = payload.db_ready ? "ready" : `missing-${(payload.db_missing_tables || []).length}`;
    const sys = payload.auth_enabled ? "ready" : "auth-disabled";
    setText("footerSystemReady", `System readiness: ${sys} (${brave}, ${s2})`);
    setText("footerAiReady", `AI readiness: ${ai}`);
    setText("footerDbReady", `DB readiness: ${db}`);
    if (state.multiTab.isLeader) broadcastSnapshot();
    return payload;
  } catch (err) {
    setText("footerSystemReady", `System readiness: unavailable (${err.message})`);
    throw err;
  }
}

async function loadDashboard() {
  setText("dashboardError", "");
  const queue = await apiGet("/v1/work-queue?limit=200&offset=0");
  const queueItems = queue.items || [];
  const needsReview = queueItems.filter((i) => i.phase === "discovery" && i.status === "needs_review").length;
  const docIssues = queueItems.filter((i) => i.phase === "acquisition" && (i.status === "failed" || i.status === "partial")).length;
  const parseErrors = queue.items.filter((i) => i.phase === "parse" && i.status === "failed").length;
  let sources = [];
  let awaitingAcceptedDocs = docIssues;
  let docFailures = docIssues;
  let activeRunStatus = "unknown";
  if (state.latest.discovery) {
    try {
      const payload = await apiGet(`/v1/discovery/runs/${encodeURIComponent(state.latest.discovery)}/sources?status=all&limit=500&offset=0`);
      sources = payload.items || [];
    } catch (_err) {
      sources = [];
    }
    try {
      const acceptedPayload = await apiGet(
        `/v1/discovery/runs/${encodeURIComponent(state.latest.discovery)}/sources?status=accepted&limit=1000&offset=0`,
      );
      const acceptedIds = new Set((acceptedPayload.items || []).map((row) => row.id));
      if (state.latest.acquisition) {
        const [acqRun, acqItems] = await Promise.all([
          apiGet(`/v1/acquisition/runs/${encodeURIComponent(state.latest.acquisition)}`),
          apiGet(`/v1/acquisition/runs/${encodeURIComponent(state.latest.acquisition)}/items?limit=1000&offset=0`),
        ]);
        activeRunStatus = acqRun.status || "unknown";
        const items = acqItems.items || [];
        const downloaded = new Set(items.filter((row) => row.status === "downloaded").map((row) => row.source_id));
        awaitingAcceptedDocs = Array.from(acceptedIds).filter((id) => !downloaded.has(id)).length;
        docFailures = items.filter((row) => row.status === "failed" || row.status === "partial").length;
      } else {
        awaitingAcceptedDocs = acceptedIds.size;
      }
    } catch (_err) {
      // keep fallback queue-derived values
    }
  }
  state.build.coverageByTopic = mergeTopicCoverage(queueItems, sources);
  const activeCoverage = state.build.coverageByTopic[state.build.activeTopicId] || emptyTopicCoverage();
  const pendingForUi = activeCoverage.pending_review ?? needsReview;
  const awaitingForUi = activeCoverage.awaiting_documents ?? awaitingAcceptedDocs;
  const failedForUi = activeCoverage.failed_documents ?? docFailures;
  const reviewPendingRuns = pendingReviewRunIds(queueItems);
  const reviewContextResolvable = !!state.latest.discovery || reviewPendingRuns.length === 1;
  const pendingBadge = pendingForUi > 0 && !reviewContextResolvable ? 0 : pendingForUi;
  setText("reviewNavBadge", String(pendingBadge));
  setText("documentsNavBadge", String(awaitingForUi));
  renderBuildTopics();

  let recent = "No run loaded";
  if (state.latest.discovery) {
    try {
      const run = await apiGet(`/v1/discovery/runs/${encodeURIComponent(state.latest.discovery)}`);
      recent = `status=${run.status}, accepted=${run.accepted_total}, discovered=${run.expanded_candidates_total}`;
      upsertRunRow("discovery", state.latest.discovery, run);
      renderRunsTable();
    } catch (err) {
      if (String(err.message || "").includes("run_not_found")) {
        resetStaleRunContext("dashboard_discovery_not_found");
      }
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
  if (!queueItems.length && (activeRunStatus === "running" || activeRunStatus === "queued")) {
    setText("dashboardState", "Still processing. Results may appear soon.");
  }
  updateStatusStrip({
    pendingReview: pendingBadge,
    awaitingDocs: awaitingForUi,
    docFailures: failedForUi,
    lastRunState: recent,
    activeTopic: activeTopic()?.name || "Default Session",
  });
  if (state.multiTab.isLeader) broadcastSnapshot();
  return true;
}

async function loadDiscover() {
  setText("discoverError", "");
  const runId = getDiscoveryRunId();
  if (!runId) {
    setText("discoverSummary", "No discovery run selected.");
    return true;
  }
  let run;
  try {
    run = await apiGet(`/v1/discovery/runs/${encodeURIComponent(runId)}`);
  } catch (err) {
    if (String(err.message || "").includes("run_not_found")) {
      resetStaleRunContext("discover_not_found");
      return true;
    }
    throw err;
  }
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
  let runId = getDiscoveryRunId();
  if (!runId) {
    const resolved = await ensureReviewRunContext();
    runId = getDiscoveryRunId();
    if (!resolved || !runId) {
      setText("reviewState", "No active session context found. Start Discover to create a session.");
      state.review.total = 0;
      state.review.items = [];
      renderTable("reviewRows", [], 5);
      renderReviewDetails(null);
      renderFastReviewCard();
      applyPaginationControls("review", 0, state.review.offset, Number(el("reviewLimit").value));
      return true;
    }
  }
  const queueFilter = el("reviewStatusFilter").value;
  const status = queueFilter === "pending" ? "needs_review" : queueFilter === "later" ? "later" : queueFilter;
  const limit = Number(el("reviewLimit").value);
  const offset = state.review.offset;
  let pageRaw;
  try {
    pageRaw = await apiGet(
      `/v1/discovery/runs/${encodeURIComponent(runId)}/sources?status=${encodeURIComponent(status)}&limit=${limit}&offset=${offset}`,
    );
  } catch (err) {
    if (String(err.message || "").includes("run_not_found")) {
      resetStaleRunContext("review_not_found");
      setText("reviewError", "Active run is unavailable. Click Use Latest to switch explicitly, or start a new run.");
      state.review.total = 0;
      state.review.items = [];
      renderTable("reviewRows", [], 5);
      renderReviewDetails(null);
      renderFastReviewCard();
      applyPaginationControls("review", 0, state.review.offset, limit);
      return true;
    }
    throw err;
  }
  const items = pageRaw.items || [];
  const total = Number(pageRaw.total || 0);
  if (total > 0 && offset >= total) {
    state.review.offset = Math.max(0, Math.floor((total - 1) / limit) * limit);
    return loadReview();
  }
  state.review.items = items;
  state.review.total = total;
  setLatestId("discovery", runId);
  setText("reviewPage", `offset=${state.review.offset}, limit=${limit}, total=${total}`);
  applyPaginationControls("review", total, state.review.offset, limit);
  try {
    const run = await apiGet(`/v1/discovery/runs/${encodeURIComponent(runId)}`);
    if (!items.length && (run.status === "queued" || run.status === "running")) {
      setText("reviewState", "Discovery run is still processing. Review queue will fill automatically.");
    } else if (!items.length) {
      setText("reviewState", "No items in this queue.");
    }
  } catch (_err) {
    // keep current UI state
  }
  renderTable(
    "reviewRows",
    items.map((s, idx) => {
      const checked = state.review.selected.has(s.id) ? " checked" : "";
      const score = s.heuristic_score ?? s.relevance_score ?? "-";
      const year = s.year ?? "-";
      const citations = s.citations ?? s.citation_count ?? "-";
      return `<tr data-source-id="${escapeHtml(s.id)}"><td><input type="checkbox" class="review-select" data-source-id="${escapeHtml(s.id)}"${checked}></td><td>${escapeHtml(String(year))}</td><td>${escapeHtml(String(citations))}</td><td>${escapeHtml(String(score))}</td><td><button type="button" class="review-action" data-action="preview" data-source-id="${escapeHtml(s.id)}">${idx + 1}. ${escapeHtml(s.title || "")}</button></td></tr>`;
    }),
    5,
  );
  if (items.length) {
    if (state.review.activeIndex >= items.length) state.review.activeIndex = 0;
    renderReviewDetails(items[state.review.activeIndex]);
  } else {
    renderReviewDetails(null);
  }
  renderFastReviewCard();
  state.review.loaded = true;
  updateReviewSelectionControls();
  return true;
}

async function loadDocuments() {
  setText("documentsError", "");
  const acqRunId = getAcqRunId();
  const limit = Number(el("documentsLimit").value);
  const offset = state.documents.offset;
  const queueFilter = (el("documentsQueueFilter") || {}).value || "awaiting";
  if (!acqRunId) {
    const discoveryRunId = getDiscoveryRunId();
    state.documents.discoveryRunId = discoveryRunId || "";
    if (!discoveryRunId) {
      setText("documentsState", "No active runs found. Start from Discover -> Run One Iteration.");
      state.documents.total = 0;
      state.documents.items = [];
      renderTable("documentsRows", [], 6);
      applyPaginationControls("documents", 0, state.documents.offset, limit);
      updateDocumentsSelectionControls();
      return true;
    }
    let approvedPayload;
    try {
      approvedPayload = await apiGet(
        `/v1/discovery/runs/${encodeURIComponent(discoveryRunId)}/sources?status=accepted&limit=1000&offset=0`,
      );
    } catch (err) {
      if (String(err.message || "").includes("run_not_found")) {
        resetStaleRunContext("documents_discovery_not_found");
        renderTable("documentsRows", [], 6);
        return true;
      }
      throw err;
    }
    const normalizedApproved = (approvedPayload.items || []).map((row) => ({
      source_id: row.id,
      status: "approved",
      title: row.title || row.id,
      doi: row.doi || "",
      source_url: row.url || "",
      selected_url: row.url || "",
      attempt_count: 0,
      last_error: "",
      reason_code: null,
      problem: "Approved - ready to process",
      discovery_run_id: discoveryRunId,
    }));
    const filteredApproved = normalizedApproved.filter((row) => {
      if (queueFilter === "awaiting") return true;
      if (queueFilter === "acquired") return false;
      if (queueFilter === "failed") return false;
      if (queueFilter === "manual_recovery") return false;
      return true;
    });
    const total = filteredApproved.length;
    if (total > 0 && offset >= total) {
      state.documents.offset = Math.max(0, Math.floor((total - 1) / limit) * limit);
    }
    const pageRows = filteredApproved.slice(state.documents.offset, state.documents.offset + limit);
    state.documents.total = total;
    state.documents.items = pageRows;
    renderTable(
      "documentsRows",
      pageRows.map((item, idx) => {
        const checked = state.documents.selected.has(item.source_id) ? " checked" : "";
        const openUrl = item.source_url || "";
        const status = `approved${checked ? " (selected)" : ""}`;
        return `<tr><td>${idx + 1}</td><td>-</td><td>-</td><td>-</td><td><button type="button" class="documents-action" data-action="select" data-source-id="${escapeHtml(item.source_id)}">${escapeHtml(item.title || "")}</button></td><td>${escapeHtml(status)} ${openUrl ? `<a href="${escapeHtml(openUrl)}" target="_blank" rel="noopener noreferrer">Open</a>` : ""} <input type="checkbox" class="documents-select" data-source-id="${escapeHtml(item.source_id)}"${checked}></td></tr>`;
      }),
      6,
    );
    setText("documentsPage", `offset=${state.documents.offset}, limit=${limit}, total=${total}`);
    applyPaginationControls("documents", total, state.documents.offset, limit);
    setText("documentsState", `Approved sources ready: ${total}. You can upload PDFs now or run auto-acquisition.`);
    if (!total) {
      setText("documentsDetails", "No approved sources yet. Continue review decisions first.");
    }
    state.documents.loaded = true;
    updateDocumentsSelectionControls();
    return true;
  }
  let itemsPayload;
  let queue;
  let run;
  try {
    [itemsPayload, queue, run] = await Promise.all([
      apiGet(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/items?limit=1000&offset=0`),
      apiGet(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/manual-downloads?limit=1000&offset=0`),
      apiGet(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}`),
    ]);
  } catch (err) {
    if (String(err.message || "").includes("run_not_found")) {
      resetStaleRunContext("documents_not_found");
      state.documents.total = 0;
      renderTable("documentsRows", [], 6);
      applyPaginationControls("documents", 0, state.documents.offset, limit);
      return true;
    }
    throw err;
  }
  state.documents.acqRunMeta = run;
  state.documents.discoveryRunId = run.discovery_run_id || state.documents.discoveryRunId || "";
  const manualBySourceId = new Map((queue.items || []).map((row) => [row.source_id, row]));
  const normalized = (itemsPayload.items || []).map((row) => {
    const manual = manualBySourceId.get(row.source_id);
    const problem =
      row.status === "downloaded"
        ? "Acquired"
        : row.status === "queued"
          ? "Awaiting"
          : reasonText(manual?.reason_code || row.last_error || row.status);
    return {
      source_id: row.source_id,
      status: row.status,
      title: manual?.title || row.source_id,
      doi: manual?.doi || "",
      source_url: manual?.source_url || row.selected_url || "",
      selected_url: row.selected_url || manual?.selected_url || "",
      attempt_count: row.attempt_count,
      last_error: row.last_error || manual?.last_error || "",
      reason_code: manual?.reason_code || null,
      problem,
    };
  });
  const filtered = normalized.filter((row) => {
    if (queueFilter === "awaiting") return row.status === "queued";
    if (queueFilter === "acquired") return row.status === "downloaded";
    if (queueFilter === "failed") return row.status === "failed" || row.status === "partial";
    if (queueFilter === "manual_recovery") return row.status === "failed" || row.status === "partial" || row.status === "skipped";
    return true;
  });
  const total = filtered.length;
  if (total > 0 && offset >= total) {
    state.documents.offset = Math.max(0, Math.floor((total - 1) / limit) * limit);
  }
  const pageRows = filtered.slice(state.documents.offset, state.documents.offset + limit);
  state.documents.total = total;
  state.documents.items = pageRows;
  setLatestId("acquisition", acqRunId);
  upsertRunRow("acquisition", acqRunId, run);
  renderRunsTable();

  renderTable(
    "documentsRows",
    pageRows.map((item, idx) => {
      const openUrl = item.selected_url || item.source_url || "";
      const checked = state.documents.selected.has(item.source_id) ? " checked" : "";
      const openLink = openUrl ? ` <a href="${escapeHtml(openUrl)}" target="_blank" rel="noopener noreferrer">Open source</a>` : "";
      let nextStep = "Awaiting download";
      if (item.status === "failed" || item.status === "partial") {
        nextStep = `<button type="button" class="documents-action" data-action="retry" data-source-id="${escapeHtml(item.source_id)}" data-discovery-run-id="${escapeHtml(state.documents.discoveryRunId || "")}">Download</button> <button type="button" class="documents-action" data-action="upload" data-source-id="${escapeHtml(item.source_id)}">Upload PDF</button>`;
      } else if (item.status === "skipped" && item.reason_code !== "manual_complete") {
        nextStep = `<button type="button" class="documents-action" data-action="manual-complete" data-source-id="${escapeHtml(item.source_id)}">Manual Complete</button>`;
      } else if (item.status === "downloaded") {
        nextStep = "Acquired";
      }
      const score = item.score ?? "-";
      const year = item.year ?? "-";
      const citations = item.citations ?? "-";
      return `<tr><td>${idx + 1}</td><td>${escapeHtml(String(score))}</td><td>${escapeHtml(String(year))}</td><td>${escapeHtml(String(citations))}</td><td><button type="button" class="documents-action" data-action="select" data-source-id="${escapeHtml(item.source_id)}">${escapeHtml(item.title || "")}</button></td><td>${nextStep}${openLink} <input type="checkbox" class="documents-select" data-source-id="${escapeHtml(item.source_id)}"${checked}></td></tr>`;
    }),
    6,
  );
  setText("documentsPage", `offset=${state.documents.offset}, limit=${limit}, total=${total}`);
  applyPaginationControls("documents", total, state.documents.offset, limit);
  setText("documentsState", `Loaded ${total} items for ${acqRunId}`);
  const downloadedCount = normalized.filter((row) => row.status === "downloaded").length;
  const failedCount = normalized.filter((row) => row.status === "failed" || row.status === "partial").length;
  const manualCount = normalized.filter((row) => row.reason_code === "manual_complete").length;
  const pendingCount = normalized.filter((row) => row.status === "queued").length;
  setText("documentsSummaryDownloaded", String(downloadedCount));
  setText("documentsSummaryFailed", String(failedCount));
  setText("documentsSummaryManual", String(manualCount));
  setText("documentsSummaryPending", String(pendingCount));
  if (!total) {
    if (run.status === "queued" || run.status === "running") {
      setText("documentsDetails", "Acquisition is still processing. Items will appear as they are resolved.");
    } else {
      setText("documentsDetails", "No item selected.");
    }
  }
  state.documents.loaded = true;
  updateDocumentsSelectionControls();
  return true;
}

function reviewRefreshKey() {
  const runId = getDiscoveryRunId();
  const status = el("reviewStatusFilter")?.value || "pending";
  const limit = el("reviewLimit")?.value || "50";
  return `review:${runId}:${status}:${state.review.offset}:${limit}`;
}

function documentsRefreshKey() {
  const acqRunId = getAcqRunId();
  const queue = el("documentsQueueFilter")?.value || "awaiting";
  const limit = el("documentsLimit")?.value || "50";
  return `documents:${acqRunId}:${queue}:${state.documents.offset}:${limit}:${getDiscoveryRunId()}`;
}

async function refreshReview(force = false) {
  return coalescedRefresh(reviewRefreshKey(), 700, () => loadReview(), force);
}

async function refreshDocuments(force = false) {
  return coalescedRefresh(documentsRefreshKey(), 700, () => loadDocuments(), force);
}

function libraryFilters() {
  return library.libraryFilters();
}

function libraryDocPassesFilters(doc, filters) {
  return library.libraryDocPassesFilters(doc, filters);
}

function setSearchPreview(doc, snippet = "") {
  return library.setSearchPreview(doc, snippet);
}

function renderLibraryRows(items, modeLabel) {
  return library.renderLibraryRows(items, modeLabel);
}

async function ensureSearchDocsCache(parseRunId, force = false) {
  return library.ensureSearchDocsCache(parseRunId, force);
}

async function runSearchData(payload) {
  return library.runSearchData(payload);
}

async function loadLibraryBrowser(parseRunId) {
  return library.loadLibraryBrowser(parseRunId);
}

async function runSearch(event) {
  return library.runSearch(event);
}

async function showSearchDoc(index) {
  return library.showSearchDoc(index);
}

async function showSearchText(index) {
  return library.showSearchText(index);
}

async function showSearchSource(index) {
  return library.showSearchSource(index);
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

function aiModeSelection() {
  const aiMode = el("startDiscoveryAiMode").value;
  return aiMode === "default" ? null : aiMode === "on";
}

async function startDiscoveryWithSeeds(seedQueries, buttonIds = ["createSessionBtn"]) {
  const aiFilterEnabled = aiModeSelection();
  const result = await runBusy("discovery", buttonIds, async () =>
    apiPost("/v1/discovery/runs", {
      seed_queries: seedQueries,
      max_iterations: 1,
      ai_filter_enabled: aiFilterEnabled,
    }),
  );
  setLatestId("discovery", result.run_id);
  el("discoverRunIdInput").value = result.run_id;
  state.review.offset = 0;
  state.documents.offset = 0;
  setText("dashboardState", "Discovery iteration started. IDs are available in Advanced.");
  await loadDashboard();
  await loadDiscover();
  window.location.hash = "#review";
}

async function startDiscovery(event) {
  event.preventDefault();
  setText("dashboardError", "");
  try {
    const raw = el("startDiscoverySeeds").value;
    const seedQueries = raw.split(",").map((s) => s.trim()).filter(Boolean);
    if (!seedQueries.length) throw new Error("provide at least one seed query");
    await startDiscoveryWithSeeds(seedQueries, ["createSessionBtn"]);
  } catch (err) {
    setText("dashboardError", `Start failed: ${err.message}`);
  }
}

async function runNextCitationIteration() {
  setText("dashboardError", "");
  const runId = getDiscoveryRunId();
  if (!runId) {
    setText("dashboardError", "Discovery run context is required.");
    return;
  }
  try {
    const aiFilterEnabled = aiModeSelection();
    const result = await runBusy("discovery", ["runNextCitationBtn"], async () =>
      apiPost(`/v1/discovery/runs/${encodeURIComponent(runId)}/next-citation-iteration`, {
        ai_filter_enabled: aiFilterEnabled,
      }),
    );
    setText(
      "dashboardState",
      `Citation iteration started from active run ${runId}. New run created: ${result.run_id}. Context unchanged until you switch explicitly.`,
    );
    await loadDashboard();
    await loadDiscover();
  } catch (err) {
    setText("dashboardError", `Citation iteration failed: ${err.message}`);
  }
}

async function searchNewKeywords(event) {
  if (event) event.preventDefault();
  setText("dashboardError", "");
  try {
    const raw = el("quickKeywordInput").value || el("startDiscoverySeeds").value;
    const seedQueries = raw.split(",").map((s) => s.trim()).filter(Boolean);
    if (!seedQueries.length) throw new Error("provide at least one keyword query");
    await startDiscoveryWithSeeds(seedQueries, ["quickKeywordSearchBtn"]);
  } catch (err) {
    setText("dashboardError", `Keyword search failed: ${err.message}`);
  }
}

function handleBuildTopicClick(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement) || !target.classList.contains("topic-btn")) return;
  const topicId = target.dataset.topicId || "";
  if (!topicId) return;
  state.build.activeTopicId = topicId;
  renderBuildTopics();
  applyActiveTopicCoverageToShell();
}

function createNewTopic() {
  const name = window.prompt("New session name");
  if (!name) return;
  const normalized = name.trim();
  if (!normalized) return;
  const id = `topic_${Date.now().toString(36)}`;
  state.build.topics.push({ id, name: normalized });
  state.build.stagedSourcesByTopic[id] = [];
  state.build.sourceKeysByTopic[id] = new Set();
  state.build.topicQueriesByTopic[id] = "";
  state.build.coverageByTopic[id] = {
    candidates: 0,
    accepted: 0,
    pending_review: 0,
    awaiting_documents: 0,
    failed_documents: 0,
  };
  state.build.activeTopicId = id;
  renderBuildTopics();
  applyActiveTopicCoverageToShell();
}

function handleAddSource(event) {
  event.preventDefault();
  const topic = activeTopic();
  if (!topic) {
    setText("addSourceState", "Create/select session first.");
    return;
  }
  const doi = (el("addSourceDoi").value || "").trim();
  const url = (el("addSourceUrl").value || "").trim();
  const citation = (el("addSourceCitation").value || "").trim();
  if (!doi && !url && !citation) {
    setText("addSourceState", "Provide at least one source input.");
    return;
  }
  const raw = doi || url || citation;
  const key = sourceFingerprint(raw);
  const keys = state.build.sourceKeysByTopic[topic.id] || new Set();
  if (keys.has(key)) {
    setText("addSourceState", `Duplicate source ignored for ${topic.name}.`);
    return;
  }
  keys.add(key);
  state.build.sourceKeysByTopic[topic.id] = keys;
  const bucket = state.build.stagedSourcesByTopic[topic.id] || [];
  bucket.push(raw);
  state.build.stagedSourcesByTopic[topic.id] = bucket;
  setText("addSourceState", `Source staged for ${topic.name}.`);
  renderBuildSources();
}

function handleBulkSource(event) {
  event.preventDefault();
  const topic = activeTopic();
  if (!topic) {
    setText("addSourceState", "Create/select session first.");
    return;
  }
  const lines = (el("bulkSourceInput").value || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (!lines.length) {
    setText("addSourceState", "Provide at least one bulk source line.");
    return;
  }
  const keys = state.build.sourceKeysByTopic[topic.id] || new Set();
  const bucket = state.build.stagedSourcesByTopic[topic.id] || [];
  let added = 0;
  for (const line of lines) {
    const key = sourceFingerprint(line);
    if (keys.has(key)) continue;
    keys.add(key);
    bucket.push(line);
    added += 1;
  }
  state.build.sourceKeysByTopic[topic.id] = keys;
  state.build.stagedSourcesByTopic[topic.id] = bucket;
  setText("addSourceState", `Staged ${added} new source rows for ${topic.name}.`);
  renderBuildSources();
}

function handleBuildQuery(event) {
  event.preventDefault();
  const query = (el("buildTopicQuery").value || "").trim();
  if (!query) {
    setText("buildQueryState", "Query is required.");
    return;
  }
  const topic = activeTopic();
  if (topic) {
    state.build.topicQueriesByTopic[topic.id] = query;
  }
  setText("buildQueryState", `Saved session query for ${activeTopic()?.name || "session"}.`);
  renderBuildTopics();
}

function handleCopyValueClick(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement) || !target.classList.contains("copy-value-btn")) return;
  const targetId = target.dataset.targetId || "";
  if (!targetId) return;
  copyFieldValue(targetId, target.dataset.feedbackId || "", target);
}

async function loadReviewClick(event) {
  return review.loadReviewClick(event);
}

function scheduleReviewAutoLoad(reason = "auto") {
  if (state.reviewAuto.timer) clearTimeout(state.reviewAuto.timer);
  state.reviewAuto.timer = setTimeout(async () => {
    state.reviewAuto.timer = null;
    if (activeSection() !== "review") return;
    try {
      await refreshReview();
      setText("reviewState", `Review queue updated (${reason}).`);
    } catch (err) {
      setText("reviewError", `Auto-load failed: ${err.message}`);
    }
  }, 250);
}

async function handleReviewAction(event) {
  return review.handleReviewAction(event);
}

async function applyReviewDecisionToSelected(decision) {
  return review.applyReviewDecisionToSelected(decision);
}

async function applySingleReviewDecision(sourceId, decision) {
  return review.applySingleReviewDecision(sourceId, decision);
}

function fastReviewMove(delta) {
  return review.fastReviewMove(delta);
}

async function fastReviewDecision(decision) {
  return review.fastReviewDecision(decision);
}

function handleReviewShortcuts(event) {
  return review.handleReviewShortcuts(event);
}

async function startAcquisition(event) {
  event.preventDefault();
  setText("acqError", "");
  try {
    await runBusy("acquisition", ["startAcqBtn"], async () => {
      const runId = getDiscoveryRunId();
      await ensureDiscoveryRunExists(runId);
      const retryFailedOnly = el("startAcqRetry").value === "true";
      const result = await apiPost("/v1/acquisition/runs", { run_id: runId, retry_failed_only: retryFailedOnly });
      setLatestId("acquisition", result.acq_run_id);
      setText("acqError", "Acquisition started. IDs are available in Advanced.");
    });
  } catch (err) {
    setText("acqError", `Start failed: ${err.message}`);
  }
}

async function startParse(event) {
  event.preventDefault();
  setText("parseError", "");
  try {
    await runBusy("parse", ["startParseBtn"], async () => {
      const acqRunId = getAcqRunId();
      await ensureAcquisitionRunExists(acqRunId);
      const retryFailedOnly = el("startParseRetry").value === "true";
      const result = await apiPost("/v1/parse/runs", { acq_run_id: acqRunId, retry_failed_only: retryFailedOnly });
      setLatestId("parse", result.parse_run_id);
      setText("parseError", "Parse started. IDs are available in Advanced.");
    });
  } catch (err) {
    setText("parseError", `Start failed: ${err.message}`);
  }
}

async function handleDocumentsAction(event) {
  return documents.handleDocumentsAction(event);
}

async function registerManualUpload(event) {
  return documents.registerManualUpload(event);
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

async function documentsAcquirePending() {
  return documents.documentsAcquirePending();
}

async function documentsRetryFailed() {
  return documents.documentsRetryFailed();
}

async function documentsCopySelected() {
  return documents.documentsCopySelected();
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

function updateFreshness() {
  setText("freshnessState", `Last update: ${new Date().toLocaleTimeString()}`);
}

async function refreshRunProgress() {
  const progressNode = el("statusProgressBar");
  if (!progressNode) return;
  let payload = null;
  try {
    if (state.latest.parse) payload = await apiGet(`/v1/parse/runs/${encodeURIComponent(state.latest.parse)}`);
    else if (state.latest.acquisition) payload = await apiGet(`/v1/acquisition/runs/${encodeURIComponent(state.latest.acquisition)}`);
    else if (state.latest.discovery) payload = await apiGet(`/v1/discovery/runs/${encodeURIComponent(state.latest.discovery)}`);
  } catch (err) {
    if (String(err.message || "").includes("run_not_found")) {
      resetStaleRunContext("progress_run_not_found");
      return;
    }
    return;
  }
  if (!payload) {
    progressNode.value = 0;
    return;
  }
  const percent = Number(payload.percent ?? 0);
  if (Number.isFinite(percent)) progressNode.value = Math.max(0, Math.min(100, percent));
  if (["queued", "running", "waiting_user"].includes(String(payload.stage_status || ""))) {
    const message = payload.message || `${payload.current_stage || "run"} ${payload.stage_status || ""}`;
    const banner = el("inProgressBanner");
    if (banner) banner.hidden = false;
    setText("inProgressState", `${message} (${payload.completed || 0}/${payload.total || 0})`);
  } else if (state.busy.count === 0) {
    const banner = el("inProgressBanner");
    if (banner) banner.hidden = true;
  }
}

function selectAllReviewRows() {
  for (const row of state.review.items) state.review.selected.add(row.id);
  setText("reviewState", `Selected ${state.review.items.length} rows.`);
  refreshReview(true).catch(() => {});
}

function deselectAllReviewRows() {
  state.review.selected.clear();
  setText("reviewState", "Selection cleared.");
  refreshReview(true).catch(() => {});
}

function selectAllDocumentsRows() {
  return documents.selectAllDocumentsRows();
}

function deselectAllDocumentsRows() {
  return documents.deselectAllDocumentsRows();
}

function toggleBatchUploadPanel() {
  return documents.toggleBatchUploadPanel();
}

async function uploadBatchDocuments(event) {
  return documents.uploadBatchDocuments(event);
}

async function handleSearchAction(event) {
  return library.handleSearchAction(event);
}

async function libraryExportMetadataCsv() {
  try {
    await library.exportMetadataCsv();
  } catch (err) {
    setText("searchError", `Export failed: ${err.message}`);
  }
}

async function libraryExportPdfZip() {
  try {
    await library.exportPdfZip();
  } catch (err) {
    setText("searchError", `Export failed: ${err.message}`);
  }
}

function libraryIncludeSelected() {
  library.includeSelected();
}

function libraryExcludeSelected() {
  library.excludeSelected();
}

async function refreshCurrentSection() {
  const section = activeSection();
  if (section === "build") return loadDashboard();
  if (section === "discover") return loadDiscover();
  if (section === "review" && state.review.loaded) return refreshReview();
  if (section === "documents" && state.documents.loaded) return refreshDocuments();
  if (section === "library" && state.search.loaded) return runSearch();
  return true;
}

async function runPollCycle() {
  if (!state.multiTab.isLeader) return;
  const section = activeSection();
  try {
    const sys = await loadSystemStatus();
    if ((sys?.db_run_count || 0) === 0) {
      resetStaleRunContext("db_run_count_zero");
      updateFreshness();
      schedulePoll();
      return;
    }
    await refreshCurrentSection();
    await refreshRunProgress();
    updateFreshness();
    broadcastSnapshot();
    if (!state.live.connected || hasActiveWork()) {
      setPollState(`Fallback refresh active for #${section}.`);
    } else {
      setPollState(`Live updates active for #${section}.`);
    }
  } catch (err) {
    if (isReadRateLimitedError(err)) {
      setPollState("Read rate limited. Passive refresh paused briefly; actions remain available.", true);
    } else {
      setPollState(`Stale data in #${section}: ${err.message}`, true);
    }
  }
  schedulePoll();
}

async function runLiveRefresh(eventType = "queue_updated") {
  try {
    await loadSystemStatus();
    if (eventType === "run_started" || eventType === "run_progress" || eventType === "run_completed") {
      await refreshRunProgress();
      if (activeSection() === "review") await refreshReview();
      if (activeSection() === "documents" && state.documents.loaded) await refreshDocuments();
    } else if (eventType === "queue_updated") {
      if (activeSection() === "review") await refreshReview();
      else if (activeSection() === "documents" && state.documents.loaded) await refreshDocuments();
    }
    await loadDashboard();
    updateFreshness();
    broadcastSnapshot();
    setPollState(`Live update processed (${eventType}).`);
  } catch (err) {
    if (isReadRateLimitedError(err)) {
      setPollState("Read rate limited during live update. Backing off refresh.", true);
    } else {
      setPollState(`Live update failed: ${err.message}`, true);
    }
  } finally {
    schedulePoll();
  }
}

function queueLiveRefresh(eventType) {
  if (state.live.queuedRefresh) clearTimeout(state.live.queuedRefresh);
  state.live.queuedRefresh = setTimeout(() => {
    state.live.queuedRefresh = null;
    runLiveRefresh(eventType);
  }, 250);
}

function openLiveUpdatesChannel() {
  if (!state.multiTab.isLeader) return;
  if (state.live.eventSource) {
    state.live.eventSource.close();
    state.live.eventSource = null;
  }
  let url = "/v1/events/stream";
  if (AUTH_ENABLED) {
    if (!state.apiKey) {
      setLiveUpdatesState(false);
      setPollState("Live updates unavailable: API token is required for stream channel.", true);
      schedulePoll();
      return;
    }
    url = `${url}?api_key=${encodeURIComponent(state.apiKey)}`;
  }
  const stream = new EventSource(url);
  state.live.eventSource = stream;
  stream.addEventListener("open", () => {
    setLiveUpdatesState(true);
    setPollState("Live updates connected.");
    schedulePoll();
  });
  for (const eventType of ["run_started", "run_progress", "run_completed", "queue_updated"]) {
    stream.addEventListener(eventType, () => {
      queueLiveRefresh(eventType);
    });
  }
  stream.addEventListener("error", () => {
    setLiveUpdatesState(false);
    setPollState("Live updates disconnected. Using fallback refresh.", true);
    schedulePoll();
  });
}

function addListener(id, event, handler) {
  const node = el(id);
  if (node) node.addEventListener(event, handler);
}

function initTelemetry() {
  telemetry.init();
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
    openLiveUpdatesChannel();
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
    openLiveUpdatesChannel();
  });
  setApiStateText();
  openLiveUpdatesChannel();
}

function initPagination() {
  addListener("reviewPrev", "click", async () => {
    const limit = Number(el("reviewLimit").value);
    state.review.offset = Math.max(0, state.review.offset - limit);
    try {
      await refreshReview(true);
    } catch (err) {
      setText("reviewError", `Load failed: ${err.message}`);
    }
  });
  addListener("reviewNext", "click", async () => {
    const limit = Number(el("reviewLimit").value);
    const page = paginationState(state.review.total, state.review.offset, limit);
    if (!page.has_next) return;
    state.review.offset += limit;
    try {
      await refreshReview(true);
    } catch (err) {
      setText("reviewError", `Load failed: ${err.message}`);
    }
  });

  addListener("documentsPrev", "click", async () => {
    const limit = Number(el("documentsLimit").value);
    state.documents.offset = Math.max(0, state.documents.offset - limit);
    try {
      await refreshDocuments(true);
    } catch (err) {
      setText("documentsError", `Load failed: ${err.message}`);
    }
  });
  addListener("documentsNext", "click", async () => {
    const limit = Number(el("documentsLimit").value);
    const page = paginationState(state.documents.total, state.documents.offset, limit);
    if (!page.has_next) return;
    state.documents.offset += limit;
    try {
      await refreshDocuments(true);
    } catch (err) {
      setText("documentsError", `Load failed: ${err.message}`);
    }
  });
}

function initSessionPersistence() {
  return sessionModule.initSessionPersistence();
}

function init() {
  if (!window.location.hash) {
    window.location.hash = `#${activeSection()}`;
  }
  apiClient = createApiClient({ state, requiredKey, authHeaders });
  initMultiTabSync();
  initAuth();
  initSessionPersistence();
  initTelemetry();
  updateSectionVisibility();

  addListener("startDiscoveryForm", "submit", startDiscovery);
  addListener("runNextCitationBtn", "click", runNextCitationIteration);
  addListener("quickKeywordForm", "submit", searchNewKeywords);
  addListener("newTopicBtn", "click", createNewTopic);
  addListener("buildTopicList", "click", handleBuildTopicClick);
  addListener("buildTabAddSources", "click", () => setBuildTab("add-sources"));
  addListener("buildTabQueries", "click", () => setBuildTab("queries"));
  addListener("buildTabRuns", "click", () => setBuildTab("runs"));
  addListener("addSourceForm", "submit", handleAddSource);
  addListener("bulkSourceForm", "submit", handleBulkSource);
  addListener("buildQueryForm", "submit", handleBuildQuery);
  addListener("build", "click", handleCopyValueClick);
  addListener("discover", "click", handleCopyValueClick);
  addListener("review", "click", handleCopyValueClick);
  addListener("documents", "click", handleCopyValueClick);
  addListener("library", "click", handleCopyValueClick);
  addListener("advanced", "click", handleCopyValueClick);
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

  addListener("reviewRefreshBtn", "click", async () => {
    try {
      await loadReviewClick(new Event("submit"));
    } catch (_err) {
      // loadReviewClick handles message
    }
  });
  addListener("reviewStatusFilter", "change", () => {
    state.review.offset = 0;
    scheduleReviewAutoLoad("filter_changed");
  });
  addListener("reviewLimit", "change", () => {
    state.review.offset = 0;
    scheduleReviewAutoLoad("limit_changed");
  });
  addListener("discoverRunIdInput", "change", () => {
    const runId = (el("discoverRunIdInput").value || "").trim();
    setLatestId("discovery", runId);
  });
  addListener("documentsAcqRunIdInput", "change", () => {
    const runId = (el("documentsAcqRunIdInput").value || "").trim();
    setLatestId("acquisition", runId);
  });
  addListener("searchParseRunIdInput", "change", () => {
    const runId = (el("searchParseRunIdInput").value || "").trim();
    setLatestId("parse", runId);
  });
  addListener("reviewMode", "change", () => setReviewMode(el("reviewMode").value));
  addListener("reviewRows", "click", handleReviewAction);
  addListener("reviewRows", "change", handleReviewAction);
  addListener("reviewBatchAcceptBtn", "click", () => applyReviewDecisionToSelected("accept"));
  addListener("reviewBatchRejectBtn", "click", () => applyReviewDecisionToSelected("reject"));
  addListener("reviewSelectAllBtn", "click", selectAllReviewRows);
  addListener("reviewDeselectAllBtn", "click", deselectAllReviewRows);
  addListener("reviewDetailAcceptBtn", "click", () => fastReviewDecision("accept"));
  addListener("reviewDetailRejectBtn", "click", () => fastReviewDecision("reject"));
  addListener("reviewDetailLaterBtn", "click", () => fastReviewDecision("later"));
  addListener("fastAcceptBtn", "click", () => fastReviewDecision("accept"));
  addListener("fastRejectBtn", "click", () => fastReviewDecision("reject"));
  addListener("fastLaterBtn", "click", () => fastReviewDecision("later"));
  addListener("fastPrevBtn", "click", () => fastReviewMove(-1));
  addListener("fastNextBtn", "click", () => fastReviewMove(1));
  addListener("documentsForm", "submit", async (event) => {
    event.preventDefault();
    state.documents.offset = 0;
    try {
      await runBusy("documents_view", ["documentsViewIssuesBtn"], async () => {
        await refreshDocuments(true);
      });
    } catch (err) {
      setText("documentsError", `Load failed: ${err.message}`);
    }
  });
  addListener("documentsRows", "click", handleDocumentsAction);
  addListener("documentsRows", "change", handleDocumentsAction);
  addListener("documentsAcquirePendingBtn", "click", documentsAcquirePending);
  addListener("documentsRetryFailedBtn", "click", documentsRetryFailed);
  addListener("documentsCopySelectedBtn", "click", documentsCopySelected);
  addListener("documentsSelectAllBtn", "click", selectAllDocumentsRows);
  addListener("documentsDeselectAllBtn", "click", deselectAllDocumentsRows);
  addListener("openBatchUploadBtn", "click", toggleBatchUploadPanel);
  addListener("batchUploadForm", "submit", uploadBatchDocuments);
  addListener("manualUploadForm", "submit", registerManualUpload);
  addListener("manualExportCsvBtn", "click", exportManualCsv);

  addListener("searchForm", "submit", runSearch);
  addListener("libraryFilterForm", "submit", runSearch);
  addListener("searchRows", "click", handleSearchAction);
  addListener("searchRows", "change", handleSearchAction);
  addListener("libraryExportCsvBtn", "click", libraryExportMetadataCsv);
  addListener("libraryExportZipBtn", "click", libraryExportPdfZip);
  addListener("libraryIncludeSelectedBtn", "click", libraryIncludeSelected);
  addListener("libraryExcludeSelectedBtn", "click", libraryExcludeSelected);

  addListener("loadAiSettingsBtn", "click", loadAiSettings);
  addListener("saveAiSettingsBtn", "click", saveAiSettings);
  addListener("globalSearchForm", "submit", runGlobalSearch);
  addListener("runLookupForm", "submit", lookupRun);
  addListener("runFilterPhase", "change", renderRunsTable);
  addListener("runFilterStatus", "change", renderRunsTable);
  addListener("startAcqRunId", "change", () => {
    const runId = (el("startAcqRunId").value || "").trim();
    setLatestId("discovery", runId);
  });
  addListener("startAcqForm", "submit", startAcquisition);
  addListener("startParseAcqRunId", "change", () => {
    const runId = (el("startParseAcqRunId").value || "").trim();
    setLatestId("acquisition", runId);
  });
  addListener("startParseForm", "submit", startParse);
  addListener("downloadManifestBtn", "click", exportManifest);

  addListener("copyDiscoveryIdBtn", "click", () => copyLatestId("discovery"));
  addListener("copyAcqIdBtn", "click", () => copyLatestId("acquisition"));
  addListener("copyParseIdBtn", "click", () => copyLatestId("parse"));
  addListener("useLatestRunBtn", "click", async () => {
    try {
      const ok = await useLatestRunContext(true);
      if (ok) await loadDashboard();
    } catch (err) {
      setText("reviewError", `Use Latest failed: ${err.message}`);
    }
  });
  addListener("statusNextActionBtn", "click", () => {
    const route = state.statusStrip.nextActionRoute || "build";
    window.location.hash = `#${route}`;
  });

  initPagination();
  document.addEventListener("keydown", handleReviewShortcuts);

  document.addEventListener("visibilitychange", schedulePoll);
  window.addEventListener("hashchange", () => {
    updateSectionVisibility();
    if (activeSection() === "review") scheduleReviewAutoLoad("enter_review");
    schedulePoll();
  });
  window.addEventListener("beforeunload", () => {
    if (state.multiTab.heartbeatTimer) clearInterval(state.multiTab.heartbeatTimer);
    if (state.multiTab.channel) state.multiTab.channel.close();
    if (state.live.eventSource) state.live.eventSource.close();
  });

  renderRunsTable();
  renderBuildTopics();
  setBuildTab(state.build.activeTab);
  setReviewMode(el("reviewMode")?.value || "table");
  (async () => {
    try {
      if (state.sessions.autoRestore && state.sessions.items.length > 0) {
        const latest = state.sessions.items[0];
        try {
          const validated = await validateSessionSnapshot(latest.state);
          applySessionState(validated.snapshot);
          if (validated.notes.length) setText("sessionState", `Auto-restore cleared stale IDs: ${validated.notes.join("; ")}`);
          else setText("sessionState", `Auto-restored: ${latest.name}`);
        } catch (_err) {
          setText("sessionState", "Auto-restore skipped due to invalid saved state.");
        }
      }
      const sys = await loadSystemStatus();
      if ((sys?.db_run_count || 0) === 0) resetStaleRunContext("init_db_run_count_zero");
      else await loadDashboard();
      await refreshRunProgress();
      updateFreshness();
    } catch (_err) {
      // keep shell interactive even when status bootstrap fails
    }
    loadAiSettings();
  })();
  emitTelemetryEvent("navigate", document.body, activeSection());
  if (activeSection() === "review") scheduleReviewAutoLoad("initial_review");
  schedulePoll();
}

document.addEventListener("DOMContentLoaded", init);
