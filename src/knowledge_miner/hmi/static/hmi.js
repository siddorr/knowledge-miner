const POLL_ACTIVE_MS = 5000;
const POLL_BACKGROUND_MS = 15000;
const POLL_DISCONNECTED_IDLE_MS = 30000;
const TELEMETRY_INPUT_DEBOUNCE_MS = 400;
const SESSIONS_STORAGE_KEY = "km_hmi_sessions_v1";
const SESSIONS_AUTO_RESTORE_KEY = "km_hmi_sessions_auto_restore";
const LEADER_STALE_MS = 6000;
const LEADER_HEARTBEAT_MS = 2000;
const LEADER_STORAGE_KEY = "km_hmi_leader";
const BC_NAME = "km_hmi_updates";
const SYSTEM_TOKEN = typeof window !== "undefined" ? window.__KM_HMI_DEFAULT_TOKEN__ || null : null;
const AUTH_ENABLED = typeof window !== "undefined" ? window.__KM_HMI_AUTH_ENABLED__ !== false : true;
const LAUNCH_SECTION = typeof window !== "undefined" ? window.__KM_HMI_LAUNCH_SECTION__ || "build" : "build";

const state = {
  apiKey: "",
  tokenSource: "none",
  pollTimer: null,
  runRows: [],
  latest: { discovery: "", acquisition: "", parse: "" },
  build: {
    topics: [{ id: "topic_default", name: "Default Topic" }],
    activeTopicId: "topic_default",
    activeTab: "runs",
    stagedSourcesByTopic: { topic_default: [] },
    sourceKeysByTopic: { topic_default: new Set() },
    topicQueriesByTopic: { topic_default: "" },
    coverageByTopic: {
      topic_default: {
        candidates: 0,
        accepted: 0,
        pending_review: 0,
        awaiting_documents: 0,
        failed_documents: 0,
      },
    },
  },
  review: { offset: 0, total: 0, loaded: false, expanded: new Set(), selected: new Set(), items: [], mode: "table", activeIndex: 0 },
  documents: {
    offset: 0,
    total: 0,
    loaded: false,
    selectedSourceId: "",
    selected: new Set(),
    items: [],
    acqRunMeta: null,
    discoveryRunId: "",
  },
  search: { loaded: false, payload: null, items: [], mode: "browse", docsById: new Map() },
  context: {},
  telemetry: {
    sessionId: "",
    inputTimers: new Map(),
  },
  statusStrip: {
    nextActionRoute: "build",
  },
  busy: {
    count: 0,
    phase: "",
    updatedAt: "",
  },
  stale: {
    lastResetKey: "",
  },
  reviewAuto: {
    timer: null,
  },
  live: {
    connected: false,
    eventSource: null,
    queuedRefresh: null,
  },
  net: {
    inflightGet: new Map(),
    etagCache: new Map(),
    requestCount: 0,
    dedupHits: 0,
  },
  multiTab: {
    tabId: `tab_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
    isLeader: true,
    heartbeatTimer: null,
    channel: null,
  },
  sessions: {
    items: [],
    autoRestore: true,
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
    systemBadges: (el("systemBadges")?.textContent || "").trim(),
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
  if (payload.systemBadges) setText("systemBadges", payload.systemBadges);
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
  const cacheKey = `GET ${path}`;
  const existing = state.net.inflightGet.get(cacheKey);
  if (existing) {
    state.net.dedupHits += 1;
    return existing;
  }
  const promise = (async () => {
    state.net.requestCount += 1;
    const cached = state.net.etagCache.get(cacheKey);
    const headers = authHeaders();
    if (cached?.etag) headers["If-None-Match"] = cached.etag;
    const res = await fetch(path, { headers });
    if (res.status === 304 && cached) return cached.payload;
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
    const payload = await res.json();
    const etag = res.headers.get("ETag");
    if (etag) state.net.etagCache.set(cacheKey, { etag, payload });
    return payload;
  })();
  state.net.inflightGet.set(cacheKey, promise);
  try {
    return await promise;
  } finally {
    state.net.inflightGet.delete(cacheKey);
  }
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

function loadSessionsFromStorage() {
  try {
    const raw = localStorage.getItem(SESSIONS_STORAGE_KEY);
    if (!raw) {
      state.sessions.items = [];
      return;
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw new Error("invalid_session_format");
    state.sessions.items = parsed.filter((row) => row && typeof row.id === "string" && row.state);
  } catch (_err) {
    state.sessions.items = [];
    localStorage.removeItem(SESSIONS_STORAGE_KEY);
    setText("sessionState", "Session store was corrupted and has been reset.");
  }
}

function saveSessionsToStorage() {
  localStorage.setItem(SESSIONS_STORAGE_KEY, JSON.stringify(state.sessions.items.slice(0, 20)));
}

function sessionSummaryLabel(item) {
  const stamp = item.updated_at ? new Date(item.updated_at).toLocaleString() : "-";
  const name = item.name || "unnamed";
  return `${name} (${stamp})`;
}

function renderSessionHistory() {
  const select = el("sessionHistorySelect");
  if (!select) return;
  const items = state.sessions.items || [];
  if (!items.length) {
    select.innerHTML = '<option value="">No saved sessions</option>';
    setText("sessionState", "No saved sessions.");
    return;
  }
  select.innerHTML = items
    .map((item, idx) => `<option value="${escapeHtml(item.id)}"${idx === 0 ? " selected" : ""}>${escapeHtml(sessionSummaryLabel(item))}</option>`)
    .join("");
  setText("sessionState", `Saved sessions: ${items.length}`);
}

function captureSessionState() {
  return {
    section: activeSection(),
    latest: { ...state.latest },
    build: {
      activeTopicId: state.build.activeTopicId,
      activeTab: state.build.activeTab,
      topics: state.build.topics,
      stagedSourcesByTopic: Object.fromEntries(
        Object.entries(state.build.stagedSourcesByTopic).map(([k, v]) => [k, Array.from(v || [])]),
      ),
      topicQueriesByTopic: state.build.topicQueriesByTopic,
    },
    review: {
      offset: state.review.offset,
      statusFilter: el("reviewStatusFilter")?.value || "pending",
      limit: el("reviewLimit")?.value || "50",
      selected: Array.from(state.review.selected),
    },
    documents: {
      offset: state.documents.offset,
      queueFilter: el("documentsQueueFilter")?.value || "awaiting",
      limit: el("documentsLimit")?.value || "50",
      selected: Array.from(state.documents.selected),
      acqRunIdInput: el("documentsAcqRunIdInput")?.value || "",
      manualSourceId: el("manualUploadSourceId")?.value || "",
    },
    library: {
      query: el("searchQuery")?.value || "",
      limit: el("searchLimit")?.value || "20",
      topicFilter: el("libraryTopicFilter")?.value || "",
      yearFilter: el("libraryYearFilter")?.value || "",
      docsFilter: el("libraryDocsFilter")?.value || "all",
      parsedFilter: el("libraryParsedFilter")?.value || "all",
      parseRunIdInput: el("searchParseRunIdInput")?.value || "",
    },
    ids: {
      discoverRunIdInput: el("discoverRunIdInput")?.value || "",
      reviewRunIdInput: el("reviewRunIdInput")?.value || "",
      startAcqRunId: el("startAcqRunId")?.value || "",
      startParseAcqRunId: el("startParseAcqRunId")?.value || "",
    },
  };
}

function applySessionState(snapshot) {
  if (!snapshot || typeof snapshot !== "object") throw new Error("invalid_session_payload");
  const section = snapshot.section || "build";
  state.latest.discovery = snapshot.latest?.discovery || "";
  state.latest.acquisition = snapshot.latest?.acquisition || "";
  state.latest.parse = snapshot.latest?.parse || "";
  setText("latestDiscoveryId", state.latest.discovery || "-");
  setText("latestAcqId", state.latest.acquisition || "-");
  setText("latestParseId", state.latest.parse || "-");

  if (snapshot.build) {
    state.build.activeTopicId = snapshot.build.activeTopicId || state.build.activeTopicId;
    state.build.activeTab = snapshot.build.activeTab || state.build.activeTab;
    if (Array.isArray(snapshot.build.topics) && snapshot.build.topics.length) state.build.topics = snapshot.build.topics;
    if (snapshot.build.stagedSourcesByTopic && typeof snapshot.build.stagedSourcesByTopic === "object") {
      state.build.stagedSourcesByTopic = Object.fromEntries(
        Object.entries(snapshot.build.stagedSourcesByTopic).map(([k, v]) => [k, Array.from(v || [])]),
      );
      state.build.sourceKeysByTopic = Object.fromEntries(
        Object.entries(state.build.stagedSourcesByTopic).map(([k, values]) => [k, new Set(values.map((raw) => sourceFingerprint(raw)))]),
      );
    }
    if (snapshot.build.topicQueriesByTopic && typeof snapshot.build.topicQueriesByTopic === "object") {
      state.build.topicQueriesByTopic = snapshot.build.topicQueriesByTopic;
    }
  }

  state.review.offset = Number(snapshot.review?.offset || 0);
  state.review.selected = new Set((snapshot.review?.selected || []).map((id) => String(id)));
  if (el("reviewStatusFilter")) el("reviewStatusFilter").value = snapshot.review?.statusFilter || "pending";
  if (el("reviewLimit")) el("reviewLimit").value = snapshot.review?.limit || "50";

  state.documents.offset = Number(snapshot.documents?.offset || 0);
  state.documents.selected = new Set((snapshot.documents?.selected || []).map((id) => String(id)));
  if (el("documentsQueueFilter")) el("documentsQueueFilter").value = snapshot.documents?.queueFilter || "awaiting";
  if (el("documentsLimit")) el("documentsLimit").value = snapshot.documents?.limit || "50";
  if (el("documentsAcqRunIdInput")) el("documentsAcqRunIdInput").value = snapshot.documents?.acqRunIdInput || "";
  if (el("manualUploadSourceId")) el("manualUploadSourceId").value = snapshot.documents?.manualSourceId || "";

  if (el("searchQuery")) el("searchQuery").value = snapshot.library?.query || "";
  if (el("searchLimit")) el("searchLimit").value = snapshot.library?.limit || "20";
  if (el("libraryTopicFilter")) el("libraryTopicFilter").value = snapshot.library?.topicFilter || "";
  if (el("libraryYearFilter")) el("libraryYearFilter").value = snapshot.library?.yearFilter || "";
  if (el("libraryDocsFilter")) el("libraryDocsFilter").value = snapshot.library?.docsFilter || "all";
  if (el("libraryParsedFilter")) el("libraryParsedFilter").value = snapshot.library?.parsedFilter || "all";
  if (el("searchParseRunIdInput")) el("searchParseRunIdInput").value = snapshot.library?.parseRunIdInput || "";

  if (el("discoverRunIdInput")) el("discoverRunIdInput").value = snapshot.ids?.discoverRunIdInput || state.latest.discovery || "";
  if (el("reviewRunIdInput")) el("reviewRunIdInput").value = snapshot.ids?.reviewRunIdInput || state.latest.discovery || "";
  if (el("startAcqRunId")) el("startAcqRunId").value = snapshot.ids?.startAcqRunId || state.latest.discovery || "";
  if (el("startParseAcqRunId")) el("startParseAcqRunId").value = snapshot.ids?.startParseAcqRunId || state.latest.acquisition || "";

  if (["build", "review", "documents", "library", "advanced", "discover"].includes(section)) {
    window.location.hash = `#${section}`;
  }
  renderBuildTopics();
  setBuildTab(state.build.activeTab);
}

function saveCurrentSession() {
  const id = `sess_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
  const name = (el("sessionNameInput")?.value || "").trim();
  const snapshot = {
    id,
    name: name || `Session ${new Date().toLocaleString()}`,
    updated_at: new Date().toISOString(),
    state: captureSessionState(),
  };
  state.sessions.items = [snapshot, ...state.sessions.items].slice(0, 20);
  saveSessionsToStorage();
  renderSessionHistory();
  setText("sessionState", `Session saved: ${snapshot.name}`);
}

async function loadSelectedSession() {
  const select = el("sessionHistorySelect");
  const id = select?.value || "";
  const item = state.sessions.items.find((row) => row.id === id);
  if (!item) {
    setText("sessionState", "Select a saved session first.");
    return;
  }
  try {
    applySessionState(item.state);
    await loadDashboard();
    if (activeSection() === "review") scheduleReviewAutoLoad("session_restore");
    if (activeSection() === "documents") await loadDocuments();
    if (activeSection() === "library") await runSearch();
    setText("sessionState", `Session loaded: ${item.name}`);
  } catch (err) {
    setText("sessionState", `Session load failed: ${err.message}`);
  }
}

function deleteSelectedSession() {
  const select = el("sessionHistorySelect");
  const id = select?.value || "";
  if (!id) {
    setText("sessionState", "Select a saved session first.");
    return;
  }
  const before = state.sessions.items.length;
  state.sessions.items = state.sessions.items.filter((row) => row.id !== id);
  saveSessionsToStorage();
  renderSessionHistory();
  if (state.sessions.items.length === before) setText("sessionState", "Session not found.");
  else setText("sessionState", "Session deleted.");
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
  if (kind === "discovery" && activeSection() === "review") {
    state.review.offset = 0;
    scheduleReviewAutoLoad("run_context_changed");
  }
}

function clearRunInputs() {
  const ids = ["discoverRunIdInput", "reviewRunIdInput", "documentsAcqRunIdInput", "searchParseRunIdInput"];
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

async function useLatestRunContext() {
  const payload = await apiGet("/v1/runs/latest");
  const d = (payload.discovery_run_id || "").trim();
  const a = (payload.acquisition_run_id || "").trim();
  const p = (payload.parse_run_id || "").trim();
  if (!d && !a && !p) {
    resetStaleRunContext("use_latest_none");
    return false;
  }
  if (d) {
    setLatestId("discovery", d);
    el("discoverRunIdInput").value = d;
    el("reviewRunIdInput").value = d;
  }
  if (a) {
    setLatestId("acquisition", a);
    el("documentsAcqRunIdInput").value = a;
  }
  if (p) {
    setLatestId("parse", p);
    el("searchParseRunIdInput").value = p;
  }
  setText("reviewState", "Loaded latest run context.");
  return true;
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
    setText("reviewDetailAbstract", "No source selected.");
    setText("reviewDetailSignals", "-");
    const link = el("reviewDetailLink");
    if (link) link.href = "#";
    return;
  }
  setText("reviewDetailTitle", row.title || "");
  setText("reviewDetailScore", String(row.heuristic_score ?? row.relevance_score ?? "-"));
  setText("reviewDetailStatus", reviewStatusLabel(row));
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
  const topic = activeTopic();
  if (!topic) {
    setText("buildDetails", "No topic selected.");
    return;
  }
  const coverage = state.build.coverageByTopic[topic.id] || {
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
        topic_id: topic.id,
        topic_name: topic.name,
        selected_tab: state.build.activeTab,
        topic_query: state.build.topicQueriesByTopic[topic.id] || "",
        latest_discovery_run: state.latest.discovery || null,
        coverage,
      },
      null,
      2,
    ),
  );
  setText("statusActiveTopic", topic.name);
}

function renderBuildSources() {
  const topic = activeTopic();
  const topicId = topic?.id || "";
  const rows = state.build.stagedSourcesByTopic[topicId] || [];
  renderTable(
    "buildSourcesRows",
    rows.map((value) => `<tr><td>${escapeHtml(topic?.name || "")}</td><td>${escapeHtml(value)}</td></tr>`),
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
    activeTopic: activeTopic()?.name || "Default Topic",
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
    const db = payload.db_ready ? "db:ready" : `db:missing-${(payload.db_missing_tables || []).length}`;
    const authBadge = payload.auth_enabled ? "Auth: Yes" : "Auth: No";
    setText("systemBadges", `${authBadge} | ${db} | ${ai} | ${brave} | ${s2}`);
    if (state.multiTab.isLeader) broadcastSnapshot();
    return payload;
  } catch (err) {
    setText("systemBadges", `status unavailable: ${err.message}`);
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
  setText("reviewNavBadge", String(pendingForUi));
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
    pendingReview: pendingForUi,
    awaitingDocs: awaitingForUi,
    docFailures: failedForUi,
    lastRunState: recent,
    activeTopic: activeTopic()?.name || "Default Topic",
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
  const runId = getDiscoveryRunId();
  if (!runId) {
    setText("reviewState", "No active runs found. Start from Discover -> Run One Iteration.");
    state.review.total = 0;
    state.review.items = [];
    renderTable("reviewRows", [], 4);
    renderReviewDetails(null);
    renderFastReviewCard();
    applyPaginationControls("review", 0, state.review.offset, Number(el("reviewLimit").value));
    return true;
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
      state.review.total = 0;
      state.review.items = [];
      renderTable("reviewRows", [], 4);
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
    items.map((s) => {
      const checked = state.review.selected.has(s.id) ? " checked" : "";
      const score = s.heuristic_score ?? s.relevance_score ?? "-";
      const statusLabel = reviewStatusLabel(s);
      return `<tr data-source-id="${escapeHtml(s.id)}"><td><input type="checkbox" class="review-select" data-source-id="${escapeHtml(s.id)}"${checked}></td><td><button type="button" class="review-action" data-action="preview" data-source-id="${escapeHtml(s.id)}">${escapeHtml(s.title || "")}</button></td><td>${escapeHtml(String(score))}</td><td>${escapeHtml(statusLabel)}</td></tr>`;
    }),
    4,
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
      renderTable("documentsRows", [], 4);
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
        renderTable("documentsRows", [], 4);
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
      pageRows.map((item) => {
        const checked = state.documents.selected.has(item.source_id) ? " checked" : "";
        const openUrl = item.source_url || "";
        return `<tr><td><input type="checkbox" class="documents-select" data-source-id="${escapeHtml(item.source_id)}"${checked}></td><td><button type="button" class="documents-action" data-action="select" data-source-id="${escapeHtml(item.source_id)}">${escapeHtml(item.title || "")}</button></td><td>${escapeHtml(item.problem)}</td><td>${openUrl ? `<a href="${escapeHtml(openUrl)}" target="_blank" rel="noopener noreferrer">Open source</a>` : "-"}</td></tr>`;
      }),
      4,
    );
    setText("documentsPage", `offset=${state.documents.offset}, limit=${limit}, total=${total}`);
    applyPaginationControls("documents", total, state.documents.offset, limit);
    setText("documentsState", `Approved sources ready: ${total}. Click Download Documents.`);
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
      renderTable("documentsRows", [], 4);
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
    pageRows.map((item) => {
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
      return `<tr><td><input type="checkbox" class="documents-select" data-source-id="${escapeHtml(item.source_id)}"${checked}></td><td><button type="button" class="documents-action" data-action="select" data-source-id="${escapeHtml(item.source_id)}">${escapeHtml(item.title || "")}</button></td><td>${escapeHtml(item.problem)}</td><td>${nextStep}${openLink}</td></tr>`;
    }),
    4,
  );
  setText("documentsPage", `offset=${state.documents.offset}, limit=${limit}, total=${total}`);
  applyPaginationControls("documents", total, state.documents.offset, limit);
  setText("documentsState", `Loaded ${total} items for ${acqRunId}`);
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

function libraryFilters() {
  return {
    topic: (el("libraryTopicFilter")?.value || "").trim().toLowerCase(),
    year: (el("libraryYearFilter")?.value || "").trim(),
    docs: (el("libraryDocsFilter")?.value || "all").trim(),
    parsed: (el("libraryParsedFilter")?.value || "all").trim(),
  };
}

function libraryDocPassesFilters(doc, filters) {
  const hay = `${doc.title || ""} ${doc.source_id || ""}`.toLowerCase();
  if (filters.topic && !hay.includes(filters.topic)) return false;
  if (filters.year && String(doc.publication_year || "") !== filters.year) return false;
  if (filters.docs === "available" && doc.status !== "parsed") return false;
  if (filters.docs === "errors" && doc.status === "parsed") return false;
  if (filters.parsed === "accept" && doc.decision !== "accept") return false;
  if (filters.parsed === "reject" && doc.decision !== "reject") return false;
  if (filters.parsed === "review" && doc.decision !== "review") return false;
  if (filters.parsed === "unset" && !!doc.decision) return false;
  return true;
}

function setSearchPreview(doc, snippet = "") {
  if (!doc) {
    setText("searchPreview", "No document selected.");
    return;
  }
  setText(
    "searchPreview",
    JSON.stringify(
      {
        title: doc.title || "",
        document_id: doc.document_id || "",
        source_id: doc.source_id || "",
        publication_year: doc.publication_year || null,
        status: doc.status || "",
        decision: doc.decision || "",
        confidence: doc.confidence ?? null,
        reason: doc.reason || "",
        snippet: snippet || "",
      },
      null,
      2,
    ),
  );
}

function renderLibraryRows(items, modeLabel) {
  renderTable(
    "searchRows",
    items.map((item, idx) => {
      const meta = item.document
        ? `${item.document.status || "-"} | year=${item.document.publication_year || "-"} | decision=${item.document.decision || "-"}`
        : "-";
      return `<tr><td>${escapeHtml(item.snippet || item.document?.title || item.document_id || "")}</td><td>${escapeHtml(meta)}</td><td><button type="button" class="search-action" data-action="doc" data-index="${idx}">Doc</button> <button type="button" class="search-action" data-action="text" data-index="${idx}">Text</button> <button type="button" class="search-action" data-action="source" data-index="${idx}">Source</button></td></tr>`;
    }),
    3,
  );
  setText("searchState", `${modeLabel}: ${items.length}`);
}

async function ensureSearchDocsCache(parseRunId, force = false) {
  if (!force && state.search.docsById.size && state.search.parseRunId === parseRunId) return;
  const docsPayload = await apiGet(`/v1/parse/runs/${encodeURIComponent(parseRunId)}/documents?limit=1000&offset=0`);
  const byId = new Map();
  for (const doc of docsPayload.items || []) {
    byId.set(doc.document_id, doc);
  }
  state.search.docsById = byId;
  state.search.parseRunId = parseRunId;
}

async function runSearchData(payload) {
  setText("searchError", "");
  const filters = libraryFilters();
  const result = await apiPost("/v1/search", payload);
  await ensureSearchDocsCache(payload.parse_run_id, false);
  const filtered = (result.items || [])
    .map((row) => ({ ...row, document: state.search.docsById.get(row.document_id) || null }))
    .filter((row) => (row.document ? libraryDocPassesFilters(row.document, filters) : true));
  state.search.items = filtered;
  state.search.mode = "search";
  state.search.payload = payload;
  renderLibraryRows(filtered, `Search results (${result.total} total matches)`);
  state.search.loaded = true;
  if (!filtered.length) setSearchPreview(null);
}

async function loadLibraryBrowser(parseRunId) {
  setText("searchError", "");
  const filters = libraryFilters();
  await ensureSearchDocsCache(parseRunId, true);
  const rows = Array.from(state.search.docsById.values())
    .filter((doc) => libraryDocPassesFilters(doc, filters))
    .map((doc) => ({
      document_id: doc.document_id,
      source_id: doc.source_id,
      score: 0,
      snippet: doc.title || doc.document_id,
      document: doc,
    }));
  rows.sort((a, b) => (a.document_id < b.document_id ? -1 : 1));
  state.search.items = rows;
  state.search.mode = "browse";
  state.search.payload = { parse_run_id: parseRunId, query: "", limit: Number(el("searchLimit").value) };
  renderLibraryRows(rows, "Corpus browser");
  state.search.loaded = true;
  if (!rows.length) setSearchPreview(null);
}

async function runSearch(event) {
  if (event) event.preventDefault();
  setText("searchError", "");
  try {
    await runBusy("library_search", ["searchRunBtn"], async () => {
      const parseRunId = getParseRunId();
      if (!parseRunId) throw new Error("parse run id is required");
      const query = el("searchQuery").value.trim();
      const payload = {
        parse_run_id: parseRunId,
        query,
        limit: Number(el("searchLimit").value),
      };
      if (!payload.query) {
        await loadLibraryBrowser(parseRunId);
        return;
      }
      await runSearchData(payload);
    });
  } catch (err) {
    setText("searchError", `Search failed: ${err.message}`);
  }
}

async function showSearchDoc(index) {
  const item = state.search.items[index];
  if (!item) return;
  const detail = await apiGet(`/v1/parse/documents/${encodeURIComponent(item.document_id)}`);
  el("searchDocDetail").textContent = JSON.stringify(detail, null, 2);
  setSearchPreview(detail, item.snippet || "");
}

async function showSearchText(index) {
  const item = state.search.items[index];
  if (!item) return;
  const body = await apiGet(`/v1/parse/documents/${encodeURIComponent(item.document_id)}/text`);
  el("searchDocText").textContent = body.text || "";
  const doc = item.document || (state.search.docsById.get(item.document_id) || null);
  if (doc) setSearchPreview(doc, item.snippet || "");
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
  const doc = item.document || (state.search.docsById.get(item.document_id) || null);
  if (doc) setSearchPreview(doc, item.snippet || "");
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
  el("reviewRunIdInput").value = result.run_id;
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
    setLatestId("discovery", result.run_id);
    el("discoverRunIdInput").value = result.run_id;
    el("reviewRunIdInput").value = result.run_id;
    state.review.offset = 0;
    setText("dashboardState", "Citation iteration started.");
    await loadDashboard();
    await loadDiscover();
    window.location.hash = "#review";
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
  const name = window.prompt("New topic name");
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
    setText("addSourceState", "Create/select topic first.");
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
    setText("addSourceState", "Create/select topic first.");
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
  setText("buildQueryState", `Saved topic query for ${activeTopic()?.name || "topic"}.`);
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
  if (event && event.preventDefault) event.preventDefault();
  state.review.offset = 0;
  try {
    await loadReview();
  } catch (err) {
    if (String(err.message || "").includes("run_not_found")) {
      const recovered = await recoverLatestDiscoveryRun();
      if (recovered) {
        await loadReview();
        return;
      }
    }
    setText("reviewError", `Load failed: ${err.message}`);
  }
}

function scheduleReviewAutoLoad(reason = "auto") {
  if (state.reviewAuto.timer) clearTimeout(state.reviewAuto.timer);
  state.reviewAuto.timer = setTimeout(async () => {
    state.reviewAuto.timer = null;
    if (activeSection() !== "review") return;
    try {
      await loadReview();
      setText("reviewState", `Review queue updated (${reason}).`);
    } catch (err) {
      if (String(err.message || "").includes("run_not_found")) {
        const recovered = await recoverLatestDiscoveryRun();
        if (recovered) {
          await loadReview();
          return;
        }
      }
      setText("reviewError", `Auto-load failed: ${err.message}`);
    }
  }, 250);
}

async function recoverLatestDiscoveryRun() {
  try {
    const queue = await apiGet("/v1/work-queue?limit=200&offset=0");
    const first = (queue.items || []).find((row) => row.phase === "discovery" && row.run_id);
    if (!first) return false;
    const runId = first.run_id;
    setLatestId("discovery", runId);
    el("discoverRunIdInput").value = runId;
    el("reviewRunIdInput").value = runId;
    setText("reviewState", `Recovered latest available run: ${runId}`);
    return true;
  } catch (_err) {
    return false;
  }
}

async function handleReviewAction(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.classList.contains("review-select")) {
    const sourceId = target.dataset.sourceId || "";
    if (!sourceId) return;
    if (target.checked) state.review.selected.add(sourceId);
    else state.review.selected.delete(sourceId);
    updateReviewSelectionControls();
    return;
  }
  if (!target.classList.contains("review-action")) return;
  const action = target.dataset.action || "";
  const sourceId = target.dataset.sourceId || "";

  if (action === "preview") {
    const idx = state.review.items.findIndex((item) => item.id === sourceId);
    const row = idx >= 0 ? state.review.items[idx] : null;
    if (!row) return;
    state.review.activeIndex = idx;
    renderReviewDetails(row);
    renderFastReviewCard();
    return;
  }

  if (action === "later") {
    if (!sourceId) return;
    state.review.selected.delete(sourceId);
    await applySingleReviewDecision(sourceId, "later");
    return;
  }

  if (action !== "accept" && action !== "reject") return;
  if (!sourceId) return;
  await applySingleReviewDecision(sourceId, action === "accept" ? "accept" : "reject");
}

async function applyReviewDecisionToSelected(decision) {
  const selected = Array.from(state.review.selected);
  if (!selected.length) {
    setText("reviewError", "Select at least one row.");
    return;
  }
  setText("reviewError", "");
  let ok = 0;
  for (const sourceId of selected) {
    try {
      await apiPost(`/v1/sources/${encodeURIComponent(sourceId)}/review`, { decision });
      ok += 1;
    } catch (_err) {
      // continue to apply best-effort for rest
    }
  }
  state.review.selected.clear();
  if (decision === "accept") {
    setText("reviewState", `Accepted ${ok}/${selected.length}. Open Documents and click Download Documents.`);
  } else {
    setText("reviewState", `${decision} applied to ${ok}/${selected.length} selected rows.`);
  }
  await loadReview();
  await loadDashboard();
}

async function applySingleReviewDecision(sourceId, decision) {
  if (!sourceId) return;
  try {
    await apiPost(`/v1/sources/${encodeURIComponent(sourceId)}/review`, { decision });
    if (decision === "accept") setText("reviewState", "Accepted. Click Download Documents next.");
    else if (decision === "reject") setText("reviewState", "Rejected.");
    else setText("reviewState", "Moved to Later.");
    await loadReview();
    await loadDashboard();
  } catch (err) {
    setText("reviewError", `Review failed: ${err.message}`);
  }
}

function fastReviewMove(delta) {
  if (!state.review.items.length) return;
  state.review.activeIndex = Math.max(0, Math.min(state.review.activeIndex + delta, state.review.items.length - 1));
  renderFastReviewCard();
}

async function fastReviewDecision(decision) {
  const row = state.review.items[state.review.activeIndex];
  if (!row) return;
  await applySingleReviewDecision(row.id, decision);
}

function handleReviewShortcuts(event) {
  if (activeSection() !== "review") return;
  const target = event.target;
  if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return;
  const key = (event.key || "").toLowerCase();
  if (key === "a") {
    event.preventDefault();
    fastReviewDecision("accept");
  } else if (key === "r") {
    event.preventDefault();
    fastReviewDecision("reject");
  } else if (key === "l") {
    event.preventDefault();
    fastReviewDecision("later");
  } else if (event.key === "ArrowDown") {
    event.preventDefault();
    fastReviewMove(1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    fastReviewMove(-1);
  }
}

async function startAcquisition(event) {
  event.preventDefault();
  setText("acqError", "");
  try {
    await runBusy("acquisition", ["startAcqBtn"], async () => {
      const runId = el("startAcqRunId").value.trim();
      if (!runId) throw new Error("discovery run id is required");
      const retryFailedOnly = el("startAcqRetry").value === "true";
      const result = await apiPost("/v1/acquisition/runs", { run_id: runId, retry_failed_only: retryFailedOnly });
      setLatestId("acquisition", result.acq_run_id);
      el("documentsAcqRunIdInput").value = result.acq_run_id;
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
      const acqRunId = el("startParseAcqRunId").value.trim();
      if (!acqRunId) throw new Error("acquisition run id is required");
      const retryFailedOnly = el("startParseRetry").value === "true";
      const result = await apiPost("/v1/parse/runs", { acq_run_id: acqRunId, retry_failed_only: retryFailedOnly });
      setLatestId("parse", result.parse_run_id);
      el("searchParseRunIdInput").value = result.parse_run_id;
      setText("parseError", "Parse started. IDs are available in Advanced.");
    });
  } catch (err) {
    setText("parseError", `Start failed: ${err.message}`);
  }
}

async function handleDocumentsAction(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (target.classList.contains("documents-select")) {
    const sourceId = target.dataset.sourceId || "";
    if (!sourceId) return;
    if (target.checked) state.documents.selected.add(sourceId);
    else state.documents.selected.delete(sourceId);
    updateDocumentsSelectionControls();
    return;
  }
  if (!target.classList.contains("documents-action")) return;
  const action = target.dataset.action || "";
  const sourceId = target.dataset.sourceId || "";
  const discoveryRunId = target.dataset.discoveryRunId || "";

  if (action === "select") {
    const item = state.documents.items.find((row) => row.source_id === sourceId);
    if (!item) return;
    setText(
      "documentsDetails",
      JSON.stringify(
        {
          title: item.title,
          source_id: item.source_id,
          doi: item.doi,
          source_url: item.source_url,
          selected_url: item.selected_url,
          attempts: item.attempt_count,
          error: item.last_error,
          reason: item.problem,
        },
        null,
        2,
      ),
    );
    return;
  }

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
      setText("documentsState", "Retry download started.");
      await loadDocuments();
      await loadDashboard();
    } catch (err) {
      setText("documentsError", `Retry failed: ${err.message}`);
    }
  }

  if (action === "manual-complete") {
    try {
      const acqRunId = getAcqRunId();
      if (!acqRunId) throw new Error("acquisition run id is required");
      await apiPost(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/manual-complete`, { source_id: sourceId });
      setText("documentsState", "Manual completion saved.");
      await loadDocuments();
      await loadDashboard();
    } catch (err) {
      setText("documentsError", `Manual complete failed: ${err.message}`);
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
    setText("documentsState", "Manual upload registered.");
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

async function documentsAcquirePending() {
  const runId = state.documents.discoveryRunId || getDiscoveryRunId();
  if (!runId) {
    setText("documentsError", "Discovery run context is required.");
    return;
  }
  emitTelemetryEvent("submit", el("documentsAcquirePendingBtn") || document.body, `action:process_approved_docs:start run_id=${runId}`);
  try {
    const next = await runBusy("acquisition", ["documentsAcquirePendingBtn"], async () =>
      apiPost("/v1/acquisition/runs", { run_id: runId, retry_failed_only: false }),
    );
    emitTelemetryEvent(
      "change",
      el("documentsAcquirePendingBtn") || document.body,
      `action:process_approved_docs:success run_id=${runId} acq_run_id=${next.acq_run_id} accepted_count=${state.dashboard.accepted_waiting_docs}`,
    );
    setLatestId("acquisition", next.acq_run_id);
    el("documentsAcqRunIdInput").value = next.acq_run_id;
    setText("documentsState", "Started document download.");
    await loadDocuments();
    await loadDashboard();
  } catch (err) {
    emitTelemetryEvent(
      "change",
      el("documentsAcquirePendingBtn") || document.body,
      `action:process_approved_docs:error run_id=${runId} error=${String(err.message || "unknown")}`,
    );
    setText("documentsError", `Download documents failed: ${err.message}`);
  }
}

async function documentsRetryFailed() {
  const run = state.documents.acqRunMeta;
  if (!run || !run.discovery_run_id) {
    setText("documentsError", "Load documents queue first.");
    return;
  }
  try {
    const next = await runBusy("acquisition", ["documentsRetryFailedBtn"], async () =>
      apiPost("/v1/acquisition/runs", { run_id: run.discovery_run_id, retry_failed_only: true }),
    );
    setLatestId("acquisition", next.acq_run_id);
    el("documentsAcqRunIdInput").value = next.acq_run_id;
    setText("documentsState", "Started retry-failed acquisition.");
    await loadDocuments();
  } catch (err) {
    setText("documentsError", `Retry failed failed: ${err.message}`);
  }
}

async function documentsCopySelected() {
  const selected = state.documents.items.filter((row) => state.documents.selected.has(row.source_id));
  if (!selected.length) {
    setText("documentsError", "Select at least one row.");
    return;
  }
  const values = selected
    .map((row) => [row.doi, row.source_url, row.selected_url].filter(Boolean).join(" | "))
    .filter(Boolean)
    .join("\n");
  if (!values) {
    setText("documentsError", "No DOI/URL values available in selected rows.");
    return;
  }
  try {
    await navigator.clipboard.writeText(values);
    setText("documentsState", "Copied");
  } catch (_err) {
    setText("documentsError", "Copy failed.");
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
  loadReview().catch(() => {});
}

function deselectAllReviewRows() {
  state.review.selected.clear();
  setText("reviewState", "Selection cleared.");
  loadReview().catch(() => {});
}

function selectAllDocumentsRows() {
  for (const row of state.documents.items) state.documents.selected.add(row.source_id);
  setText("documentsState", `Selected ${state.documents.items.length} rows.`);
  loadDocuments().catch(() => {});
}

function deselectAllDocumentsRows() {
  state.documents.selected.clear();
  setText("documentsState", "Selection cleared.");
  loadDocuments().catch(() => {});
}

function toggleBatchUploadPanel() {
  const panel = el("batchUploadForm");
  if (!panel) return;
  panel.hidden = !panel.hidden;
}

async function uploadBatchDocuments(event) {
  event.preventDefault();
  setText("documentsError", "");
  const acqRunId = getAcqRunId();
  if (!acqRunId) {
    setText("documentsError", "acquisition run id is required");
    return;
  }
  const input = el("batchUploadFiles");
  const files = input?.files ? Array.from(input.files) : [];
  if (!files.length) {
    setText("documentsError", "Select at least one file.");
    return;
  }
  try {
    const payload = await runBusy("batch_upload", ["batchUploadSubmitBtn"], async () => {
      const form = new FormData();
      for (const file of files) form.append("files", file);
      requiredKey();
      const res = await fetch(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/manual-upload-batch`, {
        method: "POST",
        headers: authHeaders(),
        body: form,
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
    });
    setText("batchUploadResults", JSON.stringify(payload, null, 2));
    setText("documentsState", `Batch upload complete: matched=${payload.matched}, unmatched=${payload.unmatched}, ambiguous=${payload.ambiguous}`);
    await loadDocuments();
    await loadDashboard();
  } catch (err) {
    setText("documentsError", `Batch upload failed: ${err.message}`);
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
    setPollState(`Stale data in #${section}: ${err.message}`, true);
  }
  schedulePoll();
}

async function runLiveRefresh(eventType = "queue_updated") {
  try {
    await loadSystemStatus();
    if (eventType === "run_started" || eventType === "run_progress" || eventType === "run_completed") {
      await refreshRunProgress();
      if (activeSection() === "review") await loadReview();
      if (activeSection() === "documents" && state.documents.loaded) await loadDocuments();
    } else if (eventType === "queue_updated") {
      if (activeSection() === "review") await loadReview();
      else if (activeSection() === "documents" && state.documents.loaded) await loadDocuments();
    }
    await loadDashboard();
    updateFreshness();
    broadcastSnapshot();
    setPollState(`Live update processed (${eventType}).`);
  } catch (err) {
    setPollState(`Live update failed: ${err.message}`, true);
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
      await loadReview();
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
    const page = paginationState(state.documents.total, state.documents.offset, limit);
    if (!page.has_next) return;
    state.documents.offset += limit;
    try {
      await loadDocuments();
    } catch (err) {
      setText("documentsError", `Load failed: ${err.message}`);
    }
  });
}

function initSessionPersistence() {
  loadSessionsFromStorage();
  const autoRestoreSaved = localStorage.getItem(SESSIONS_AUTO_RESTORE_KEY);
  state.sessions.autoRestore = autoRestoreSaved == null ? true : autoRestoreSaved === "true";
  const checkbox = el("autoRestoreSession");
  if (checkbox) checkbox.checked = state.sessions.autoRestore;
  renderSessionHistory();

  addListener("saveSessionBtn", "click", saveCurrentSession);
  addListener("loadSessionBtn", "click", () => {
    loadSelectedSession().catch((err) => setText("sessionState", `Session load failed: ${err.message}`));
  });
  addListener("deleteSessionBtn", "click", deleteSelectedSession);
  addListener("autoRestoreSession", "change", () => {
    const enabled = !!el("autoRestoreSession").checked;
    state.sessions.autoRestore = enabled;
    localStorage.setItem(SESSIONS_AUTO_RESTORE_KEY, enabled ? "true" : "false");
    setText("sessionState", enabled ? "Auto-restore enabled." : "Auto-restore disabled.");
  });
}

function init() {
  if (!window.location.hash) {
    window.location.hash = `#${activeSection()}`;
  }
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
  addListener("reviewRunIdInput", "change", () => {
    state.review.offset = 0;
    scheduleReviewAutoLoad("run_override_changed");
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
        await loadDocuments();
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
  addListener("useLatestRunBtn", "click", async () => {
    try {
      const ok = await useLatestRunContext();
      if (ok) await loadDashboard();
    } catch (err) {
      setText("reviewError", `Use Latest failed: ${err.message}`);
    }
  });
  addListener("statusNextActionBtn", "click", () => {
    const route = state.statusStrip.nextActionRoute || "build";
    window.location.hash = `#${route}`;
  });
  addListener("taskReviewBtn", "click", () => (window.location.hash = "#review"));
  addListener("taskDocumentsBtn", "click", () => (window.location.hash = "#documents"));
  addListener("taskErrorsBtn", "click", () => (window.location.hash = "#documents"));

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
          applySessionState(latest.state);
          setText("sessionState", `Auto-restored: ${latest.name}`);
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
