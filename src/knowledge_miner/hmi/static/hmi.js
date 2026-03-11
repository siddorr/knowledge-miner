const POLL_ACTIVE_MS = 5000;
const POLL_BACKGROUND_MS = 15000;
const SYSTEM_TOKEN = typeof window !== "undefined" ? window.__KM_HMI_DEFAULT_TOKEN__ || null : null;
const AUTH_ENABLED = typeof window !== "undefined" ? window.__KM_HMI_AUTH_ENABLED__ !== false : true;

const state = {
  apiKey: "",
  tokenSource: "none",
  runRows: [],
  pollTimer: null,
  view: {
    queue: { loaded: false, offset: 0, limit: 100 },
    discovery: { loaded: false, offset: 0, expandedAbstractIds: new Set(), createdRunId: null },
    acq: { loaded: false, offset: 0 },
    parse: { loaded: false, docsOffset: 0, chunksOffset: 0 },
    manual: { loaded: false, offset: 0 },
    search: { loaded: false, payload: null, latestItems: [] },
    globalSearch: { loaded: false, latestItems: [] },
  },
  context: {},
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

function truncatedAbstract(text, expanded) {
  const raw = (text || "").trim();
  if (!raw) return { body: "", toggle: false, expanded: false };
  if (raw.length <= 220) return { body: raw, toggle: false, expanded: false };
  if (expanded) return { body: raw, toggle: true, expanded: true };
  return { body: `${raw.slice(0, 220)}...`, toggle: true, expanded: false };
}

function requiredKey() {
  if (!AUTH_ENABLED) return;
  if (!state.apiKey) {
    throw new Error("API key is required");
  }
}

function authHeaders() {
  if (!AUTH_ENABLED || !state.apiKey) {
    return {};
  }
  return { Authorization: `Bearer ${state.apiKey}` };
}

function isTerminalStatus(status) {
  return status === "completed" || status === "failed";
}

function activeSection() {
  const id = window.location.hash.replace("#", "") || "work-queue";
  if (["work-queue", "runs", "discovery", "acquisition", "parse", "search", "manual-recovery"].includes(id)) {
    return id;
  }
  return "work-queue";
}

function setPollState(message, stale = false) {
  const node = el("pollState");
  if (!node) return;
  node.textContent = message;
  node.classList.remove("poll-ok", "poll-stale");
  node.classList.add(stale ? "poll-stale" : "poll-ok");
}

function schedulePoll() {
  if (state.pollTimer) clearTimeout(state.pollTimer);
  const interval = document.visibilityState === "hidden" ? POLL_BACKGROUND_MS : POLL_ACTIVE_MS;
  state.pollTimer = setTimeout(runPollCycle, interval);
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
      // ignore json parse errors
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
      // ignore json parse errors
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
      // ignore json parse errors
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

function summaryForRun(phase, payload) {
  if (phase === "discovery") {
    return `iter=${payload.current_iteration}, accepted=${payload.accepted_total}, expanded=${payload.expanded_candidates_total}`;
  }
  if (phase === "acquisition") {
    return `downloaded=${payload.downloaded_total}, partial=${payload.partial_total}, failed=${payload.failed_total}`;
  }
  return `parsed=${payload.parsed_total}, failed=${payload.failed_total}, chunks=${payload.chunked_total}`;
}

function setLatestId(kind, value) {
  const id = value && value.trim() ? value.trim() : "-";
  if (kind === "discovery") setText("latestDiscoveryId", id);
  if (kind === "acquisition") setText("latestAcqId", id);
  if (kind === "parse") setText("latestParseId", id);
}

function setContext(patch) {
  state.context = { ...state.context, ...patch };
  el("globalContext").textContent = JSON.stringify(state.context, null, 2);
}

function syncDiscoveryRunInputs(runId) {
  const id = (runId || "").trim();
  if (!id) return;
  el("discoveryRunId").value = id;
  el("runPhaseSelect").value = "discovery";
  el("runIdInput").value = id;
}

function updatePostCreateGuidance(run, sourcesTotal) {
  const guidanceNode = el("postCreateGuidance");
  if (!guidanceNode) return;
  if (!state.view.discovery.createdRunId || run.run_id !== state.view.discovery.createdRunId) {
    guidanceNode.textContent = "";
    return;
  }
  const statusFilter = el("discoveryStatusFilter").value;
  let message = `Run created: ${run.run_id}. Status filter currently: ${statusFilter}.`;
  if (statusFilter === "accepted" && Number(sourcesTotal || 0) === 0) {
    message += " No accepted sources yet. Switch filter to all or needs_review.";
  }
  guidanceNode.textContent = message;
}

function getLatestId(kind) {
  if (kind === "discovery") return (el("latestDiscoveryId").textContent || "").trim();
  if (kind === "acquisition") return (el("latestAcqId").textContent || "").trim();
  return (el("latestParseId").textContent || "").trim();
}

async function copyLatestId(kind) {
  const id = getLatestId(kind);
  if (!id || id === "-") {
    setText("idCopyState", "No ID to copy.");
    return;
  }
  try {
    await navigator.clipboard.writeText(id);
    setText("idCopyState", `Copied ${kind} ID: ${id}`);
  } catch (_err) {
    setText("idCopyState", "Copy failed (clipboard unavailable).");
  }
}

function upsertRunRow(phase, runId, payload) {
  const idx = state.runRows.findIndex((r) => r.phase === phase && r.id === runId);
  const row = { phase, id: runId, status: payload.status, summary: summaryForRun(phase, payload) };
  if (idx >= 0) state.runRows[idx] = row;
  else state.runRows.unshift(row);
}

function renderRunsTable() {
  const phaseFilter = el("runFilterPhase").value;
  const statusFilter = el("runFilterStatus").value;
  const tbody = el("runsTable");
  const rows = state.runRows.filter((row) => {
    const phaseOk = phaseFilter === "all" || row.phase === phaseFilter;
    const statusOk = statusFilter === "all" || row.status === statusFilter;
    return phaseOk && statusOk;
  });

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="4">No runs loaded.</td></tr>';
    return;
  }

  tbody.innerHTML = rows
    .map(
      (row) =>
        `<tr><td>${escapeHtml(row.phase)}</td><td>${escapeHtml(row.id)}</td><td>${escapeHtml(row.status)}</td><td>${escapeHtml(row.summary)}</td></tr>`,
    )
    .join("");
}

async function refreshRunsData() {
  if (!state.runRows.length) return true;
  let hasNonTerminal = false;
  for (const row of state.runRows) {
    const endpoint =
      row.phase === "discovery"
        ? `/v1/discovery/runs/${encodeURIComponent(row.id)}`
        : row.phase === "acquisition"
          ? `/v1/acquisition/runs/${encodeURIComponent(row.id)}`
          : `/v1/parse/runs/${encodeURIComponent(row.id)}`;
    const payload = await apiGet(endpoint);
    row.status = payload.status;
    row.summary = summaryForRun(row.phase, payload);
    if (!isTerminalStatus(row.status)) hasNonTerminal = true;
  }
  renderRunsTable();
  return hasNonTerminal;
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

async function loadWorkQueueData() {
  setText("workQueueError", "");
  const payload = await apiGet(`/v1/work-queue?limit=${state.view.queue.limit}&offset=${state.view.queue.offset}`);
  renderTable(
    "workQueueRows",
    payload.items.map((item) => {
      const contextJson = escapeHtml(JSON.stringify(item.context || {}));
      let actions = "";
      if (item.item_type === "source_review") {
        actions = `<button type="button" class="queue-action" data-action="approve" data-source-id="${escapeHtml(item.source_id || "")}" data-context="${contextJson}">Approve</button>
          <button type="button" class="queue-action" data-action="reject" data-source-id="${escapeHtml(item.source_id || "")}" data-context="${contextJson}">Reject</button>`;
      } else if (item.item_type === "acquisition_issue") {
        actions = `<button type="button" class="queue-action" data-action="retry-acq" data-context="${contextJson}">Retry Acquisition</button>
          <button type="button" class="queue-action" data-action="open-manual" data-context="${contextJson}">Manual Recovery</button>`;
      } else if (item.item_type === "parse_issue") {
        actions = `<button type="button" class="queue-action" data-action="retry-parse" data-context="${contextJson}">Retry Parse</button>`;
      }
      return `<tr><td>${escapeHtml(item.phase)}</td><td>${escapeHtml(item.status)}</td><td>${escapeHtml(item.title || item.source_id || item.item_id || "")}</td><td>${escapeHtml(item.reason_text || item.reason_code || "")}</td><td>${actions}</td></tr>`;
    }),
    5,
  );
  setText("workQueueState", `items=${payload.total}`);
  state.view.queue.loaded = true;
  return true;
}

async function refreshWorkQueue() {
  try {
    await loadWorkQueueData();
    schedulePoll();
  } catch (err) {
    setText("workQueueError", `Load failed: ${err.message}`);
  }
}

async function handleQueueAction(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement) || !target.classList.contains("queue-action")) return;
  const action = target.dataset.action || "";
  let context = {};
  try {
    context = JSON.parse(target.dataset.context || "{}");
  } catch (_err) {
    context = {};
  }
  setContext(context);
  try {
    if (action === "approve" || action === "reject") {
      const sourceId = target.dataset.sourceId || "";
      if (!sourceId) throw new Error("source id is required");
      await apiPost(`/v1/sources/${encodeURIComponent(sourceId)}/review`, { decision: action === "approve" ? "accept" : "reject" });
    } else if (action === "retry-acq") {
      if (!context.discovery_run_id) throw new Error("discovery run id missing");
      await apiPost("/v1/acquisition/runs", { run_id: context.discovery_run_id, retry_failed_only: true });
    } else if (action === "retry-parse") {
      if (!context.acq_run_id) throw new Error("acq run id missing");
      await apiPost("/v1/parse/runs", { acq_run_id: context.acq_run_id, retry_failed_only: true });
    } else if (action === "open-manual") {
      if (context.acq_run_id) el("manualAcqRunId").value = context.acq_run_id;
      if (context.source_id) el("manualUploadSourceId").value = context.source_id;
      window.location.hash = "#manual-recovery";
      await loadManualRecoveryData();
    }
    await loadWorkQueueData();
  } catch (err) {
    setText("workQueueError", `Action failed: ${err.message}`);
  }
}

async function runGlobalSearch(event) {
  event.preventDefault();
  setText("globalSearchState", "");
  const query = el("globalSearchQuery").value.trim();
  const limit = Number(el("globalSearchLimit").value);
  if (!query) return;
  try {
    const payload = await apiGet(`/v1/search/global?q=${encodeURIComponent(query)}&limit=${limit}`);
    state.view.globalSearch.latestItems = payload.items || [];
    setText("globalSearchState", `results=${payload.total}`);
    if (payload.items && payload.items.length) {
      setContext(payload.items[0].context || {});
    }
  } catch (err) {
    setText("globalSearchState", `Search failed: ${err.message}`);
  }
}

async function lookupRun(event) {
  event.preventDefault();
  setText("runsError", "");
  const phase = el("runPhaseSelect").value;
  const runId = el("runIdInput").value.trim();
  if (!runId) return;

  const endpoint =
    phase === "discovery"
      ? `/v1/discovery/runs/${encodeURIComponent(runId)}`
      : phase === "acquisition"
        ? `/v1/acquisition/runs/${encodeURIComponent(runId)}`
        : `/v1/parse/runs/${encodeURIComponent(runId)}`;

  try {
    const payload = await apiGet(endpoint);
    upsertRunRow(phase, runId, payload);
    setLatestId(phase, runId);
    renderRunsTable();
    schedulePoll();
  } catch (err) {
    setText("runsError", `Lookup failed: ${err.message}`);
  }
}

function renderTable(tbodyId, rows, fallbackCols) {
  const tbody = el(tbodyId);
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="${fallbackCols}">No records found.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.join("");
}

async function loadDiscoveryData() {
  setText("discoveryError", "");
  const runId = el("discoveryRunId").value.trim();
  const status = el("discoveryStatusFilter").value;
  const limit = Number(el("discoveryLimit").value);
  const offset = state.view.discovery.offset;
  if (!runId) return true;

  const [run, sources] = await Promise.all([
    apiGet(`/v1/discovery/runs/${encodeURIComponent(runId)}`),
    apiGet(`/v1/discovery/runs/${encodeURIComponent(runId)}/sources?status=${encodeURIComponent(status)}&limit=${limit}&offset=${offset}`),
  ]);

  upsertRunRow("discovery", runId, run);
  setLatestId("discovery", runId);
  renderRunsTable();

  el("discoveryMetrics").textContent = JSON.stringify(
    {
      run_id: run.run_id,
      status: run.status,
      current_iteration: run.current_iteration,
      seed_queries: run.seed_queries,
      accepted_total: run.accepted_total,
      expanded_candidates_total: run.expanded_candidates_total,
      citation_edges_total: run.citation_edges_total,
      ai_filter_active: run.ai_filter_active,
      ai_filter_warning: run.ai_filter_warning,
      listing_status_filter: status,
      listed_sources: sources.total,
    },
    null,
    2,
  );

  renderTable(
    "discoverySources",
    sources.items.map(
      (s) => {
        const expanded = state.view.discovery.expandedAbstractIds.has(s.id);
        const abstract = truncatedAbstract(s.abstract || "", expanded);
        const toggle = abstract.toggle
          ? `<button type="button" class="abstract-toggle" data-action="toggle-abstract" data-source-id="${escapeHtml(s.id)}">${abstract.expanded ? "Collapse" : "Expand"}</button>`
          : "";
        return `<tr data-source-id="${escapeHtml(s.id)}"><td>${escapeHtml(s.id)}</td><td>${escapeHtml(s.title)}</td><td><span class="abstract-text">${escapeHtml(abstract.body)}</span>${toggle}</td><td class="discovery-status-cell">${escapeHtml(s.review_status)}</td><td>${escapeHtml(String(s.relevance_score))}</td><td>${escapeHtml(s.type)}</td><td>${escapeHtml(s.source)}</td><td><div class="review-actions"><button type="button" class="discovery-review-action" data-action="approve" data-source-id="${escapeHtml(s.id)}">Approve</button><button type="button" class="discovery-review-action" data-action="reject" data-source-id="${escapeHtml(s.id)}">Reject</button><button type="button" class="discovery-context-action" data-action="use-context" data-source-id="${escapeHtml(s.id)}">Use Context</button></div></td></tr>`;
      },
    ),
    8,
  );
  setText("discoveryPage", `offset=${offset}, limit=${limit}, total=${sources.total}`);
  updatePostCreateGuidance(run, sources.total);
  state.view.discovery.loaded = true;
  return !isTerminalStatus(run.status);
}

async function loadDiscovery(event) {
  event.preventDefault();
  state.view.discovery.offset = 0;
  try {
    await loadDiscoveryData();
    schedulePoll();
  } catch (err) {
    setText("discoveryError", `Load failed: ${err.message}`);
  }
}

async function loadAcquisitionData() {
  setText("acqError", "");
  const runId = el("acqRunId").value.trim();
  const limit = Number(el("acqLimit").value);
  const offset = state.view.acq.offset;
  if (!runId) return true;

  const [run, items] = await Promise.all([
    apiGet(`/v1/acquisition/runs/${encodeURIComponent(runId)}`),
    apiGet(`/v1/acquisition/runs/${encodeURIComponent(runId)}/items?limit=${limit}&offset=${offset}`),
  ]);

  upsertRunRow("acquisition", runId, run);
  setLatestId("acquisition", runId);
  if (!el("manualAcqRunId").value.trim()) {
    el("manualAcqRunId").value = runId;
  }
  renderRunsTable();

  el("acqMetrics").textContent = JSON.stringify(
    {
      acq_run_id: run.acq_run_id,
      status: run.status,
      total_sources: run.total_sources,
      downloaded_total: run.downloaded_total,
      partial_total: run.partial_total,
      failed_total: run.failed_total,
      skipped_total: run.skipped_total,
      error_message: run.error_message,
    },
    null,
    2,
  );

  renderTable(
    "acqItems",
    items.items.map(
      (i) =>
        `<tr data-item-id="${escapeHtml(i.item_id)}" data-source-id="${escapeHtml(i.source_id)}"><td>${escapeHtml(i.item_id)}</td><td>${escapeHtml(i.source_id)}</td><td>${escapeHtml(i.status)}</td><td>${escapeHtml(String(i.attempt_count))}</td><td>${escapeHtml(i.selected_url || "")}</td><td>${escapeHtml(i.last_error || "")}</td><td><div class="review-actions"><button type="button" class="acq-row-action" data-action="open-manual" data-source-id="${escapeHtml(i.source_id)}">Manual Recovery</button><button type="button" class="acq-row-action" data-action="prefill-upload" data-source-id="${escapeHtml(i.source_id)}">Prefill Upload</button></div></td></tr>`,
    ),
    7,
  );
  setText("acqPage", `offset=${offset}, limit=${limit}, total=${items.total}`);
  state.view.acq.loaded = true;
  return !isTerminalStatus(run.status);
}

async function loadAcquisition(event) {
  event.preventDefault();
  state.view.acq.offset = 0;
  try {
    await loadAcquisitionData();
    schedulePoll();
  } catch (err) {
    setText("acqError", `Load failed: ${err.message}`);
  }
}

async function loadParseData() {
  setText("parseError", "");
  const runId = el("parseRunId").value.trim();
  const docsLimit = Number(el("parseDocsLimit").value);
  const chunksLimit = Number(el("parseChunksLimit").value);
  const docsOffset = state.view.parse.docsOffset;
  const chunksOffset = state.view.parse.chunksOffset;
  if (!runId) return true;

  const [run, docs, chunks] = await Promise.all([
    apiGet(`/v1/parse/runs/${encodeURIComponent(runId)}`),
    apiGet(`/v1/parse/runs/${encodeURIComponent(runId)}/documents?limit=${docsLimit}&offset=${docsOffset}`),
    apiGet(`/v1/parse/runs/${encodeURIComponent(runId)}/chunks?limit=${chunksLimit}&offset=${chunksOffset}`),
  ]);

  upsertRunRow("parse", runId, run);
  setLatestId("parse", runId);
  renderRunsTable();

  el("parseMetrics").textContent = JSON.stringify(
    {
      parse_run_id: run.parse_run_id,
      status: run.status,
      total_documents: run.total_documents,
      parsed_total: run.parsed_total,
      failed_total: run.failed_total,
      chunked_total: run.chunked_total,
      ai_filter_active: run.ai_filter_active,
      ai_filter_warning: run.ai_filter_warning,
    },
    null,
    2,
  );

  renderTable(
    "parseDocuments",
    docs.items.map(
      (d) =>
        `<tr><td>${escapeHtml(d.document_id)}</td><td>${escapeHtml(d.status)}</td><td>${escapeHtml(d.decision || "")}</td><td>${escapeHtml(String(d.confidence || ""))}</td><td>${escapeHtml(d.parser_used || "")}</td><td>${escapeHtml(String(d.char_count))}</td><td><div class="review-actions"><button type="button" class="parse-doc-action" data-action="detail" data-document-id="${escapeHtml(d.document_id)}">Detail</button><button type="button" class="parse-doc-action" data-action="text" data-document-id="${escapeHtml(d.document_id)}">Text</button></div></td></tr>`,
    ),
    7,
  );

  renderTable(
    "parseChunks",
    chunks.items.map(
      (c) =>
        `<tr><td>${escapeHtml(c.chunk_id)}</td><td>${escapeHtml(c.document_id)}</td><td>${escapeHtml(String(c.chunk_index))}</td><td>${escapeHtml(c.decision || "")}</td><td>${escapeHtml(String(c.confidence || ""))}</td><td>${escapeHtml(`${c.start_char}-${c.end_char}`)}</td></tr>`,
    ),
    6,
  );

  setText("parseDocsPage", `offset=${docsOffset}, limit=${docsLimit}, total=${docs.total}`);
  setText("parseChunksPage", `offset=${chunksOffset}, limit=${chunksLimit}, total=${chunks.total}`);
  state.view.parse.loaded = true;
  return !isTerminalStatus(run.status);
}

async function loadParse(event) {
  event.preventDefault();
  state.view.parse.docsOffset = 0;
  state.view.parse.chunksOffset = 0;
  try {
    await loadParseData();
    schedulePoll();
  } catch (err) {
    setText("parseError", `Load failed: ${err.message}`);
  }
}

async function loadManualRecoveryData() {
  setText("manualError", "");
  const acqRunId = el("manualAcqRunId").value.trim();
  const limit = Number(el("manualLimit").value);
  const offset = state.view.manual.offset;
  if (!acqRunId) return true;

  const queue = await apiGet(
    `/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/manual-downloads?limit=${limit}&offset=${offset}`,
  );

  renderTable(
    "manualQueueRows",
    queue.items.map(
      (item) =>
        `<tr><td>${escapeHtml(item.item_id)}</td><td>${escapeHtml(item.source_id)}</td><td>${escapeHtml(item.status)}</td><td>${escapeHtml(String(item.attempt_count))}</td><td>${escapeHtml(item.title)}</td><td>${escapeHtml(item.doi || "")}</td><td>${escapeHtml(item.source_url || "")}</td><td>${escapeHtml(item.selected_url || "")}</td><td>${escapeHtml((item.manual_url_candidates || []).join(" | "))}</td><td>${escapeHtml(item.last_error || "")}</td><td><button type="button" class="manual-row-action" data-action="prefill-upload" data-source-id="${escapeHtml(item.source_id)}">Prefill Upload</button></td></tr>`,
    ),
    11,
  );
  setText("manualPage", `offset=${offset}, limit=${limit}, total=${queue.total}`);
  state.view.manual.loaded = true;

  const run = await apiGet(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}`);
  upsertRunRow("acquisition", acqRunId, run);
  setLatestId("acquisition", acqRunId);
  renderRunsTable();
  return !isTerminalStatus(run.status);
}

async function loadManualRecovery(event) {
  event.preventDefault();
  state.view.manual.offset = 0;
  setText("manualState", "");
  try {
    await loadManualRecoveryData();
    schedulePoll();
  } catch (err) {
    setText("manualError", `Load failed: ${err.message}`);
  }
}

async function exportManualCsv() {
  setText("manualError", "");
  try {
    const acqRunId = el("manualAcqRunId").value.trim();
    if (!acqRunId) throw new Error("acquisition run id is required");
    await apiDownload(
      `/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/manual-downloads.csv`,
      `manual_downloads_${acqRunId}.csv`,
    );
  } catch (err) {
    setText("manualError", `Export failed: ${err.message}`);
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
  setText("manualError", "");
  setText("manualState", "");
  try {
    const acqRunId = el("manualAcqRunId").value.trim();
    const sourceId = el("manualUploadSourceId").value.trim();
    const fileInput = el("manualUploadFile");
    const file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
    if (!acqRunId) throw new Error("acquisition run id is required");
    if (!sourceId) throw new Error("source id is required");
    if (!file) throw new Error("file is required");
    const contentBase64 = await fileToBase64(file);
    const result = await apiPost(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/manual-upload`, {
      source_id: sourceId,
      filename: file.name,
      content_base64: contentBase64,
      content_type: file.type || null,
    });
    setText("manualState", `Registered artifact: ${result.artifact_id}`);
    await loadManualRecoveryData();
    schedulePoll();
  } catch (err) {
    setText("manualError", `Upload failed: ${err.message}`);
  }
}

async function startDiscovery(event) {
  event.preventDefault();
  setText("discoveryError", "");
  setText("createSessionState", "");
  try {
    const rawSeeds = el("startDiscoverySeeds").value;
    const seedQueries = rawSeeds.split(",").map((s) => s.trim()).filter(Boolean);
    if (!seedQueries.length) throw new Error("provide at least one seed query");
    const maxIterations = Number(el("startDiscoveryMaxIterations").value);
    const aiMode = el("startDiscoveryAiMode").value;
    const aiFilterEnabled = aiMode === "default" ? null : aiMode === "on";
    const result = await apiPost("/v1/discovery/runs", {
      seed_queries: seedQueries,
      max_iterations: maxIterations,
      ai_filter_enabled: aiFilterEnabled,
    });
    syncDiscoveryRunInputs(result.run_id);
    setLatestId("discovery", result.run_id);
    state.view.discovery.createdRunId = result.run_id;
    setText("createSessionState", `Created discovery session: ${result.run_id}`);
    window.location.hash = "#discovery";
    el("discoveryRunId").focus();
    upsertRunRow("discovery", result.run_id, {
      status: result.status,
      current_iteration: 0,
      accepted_total: 0,
      expanded_candidates_total: 0,
    });
    renderRunsTable();
    await loadDiscoveryData();
    schedulePoll();
  } catch (err) {
    setText("discoveryError", `Start failed: ${err.message}`);
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
    el("acqRunId").value = result.acq_run_id;
    setLatestId("acquisition", result.acq_run_id);
    upsertRunRow("acquisition", result.acq_run_id, { status: result.status, downloaded_total: 0, partial_total: 0, failed_total: 0 });
    renderRunsTable();
    await loadAcquisitionData();
    schedulePoll();
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
    el("parseRunId").value = result.parse_run_id;
    setLatestId("parse", result.parse_run_id);
    upsertRunRow("parse", result.parse_run_id, { status: result.status, parsed_total: 0, failed_total: 0, chunked_total: 0 });
    renderRunsTable();
    await loadParseData();
    schedulePoll();
  } catch (err) {
    setText("parseError", `Start failed: ${err.message}`);
  }
}

async function handleDiscoveryTableAction(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;

  const action = target.dataset.action || "";
  const sourceId = target.dataset.sourceId || "";
  if (!sourceId) return;

  if (action === "toggle-abstract") {
    if (state.view.discovery.expandedAbstractIds.has(sourceId)) state.view.discovery.expandedAbstractIds.delete(sourceId);
    else state.view.discovery.expandedAbstractIds.add(sourceId);
    try {
      await loadDiscoveryData();
    } catch (err) {
      setText("discoveryError", `Load failed: ${err.message}`);
    }
    return;
  }
  if (action === "use-context") {
    setText("discoveryState", `Selected source context: ${sourceId}`);
    setContext({ source_id: sourceId, discovery_run_id: el("discoveryRunId").value.trim() });
    return;
  }

  if (action === "") {
    const row = target.closest("tr[data-source-id]");
    if (row) {
      const sid = row.getAttribute("data-source-id") || "";
      if (sid) {
        setText("discoveryState", `Selected source context: ${sid}`);
        setContext({ source_id: sid, discovery_run_id: el("discoveryRunId").value.trim() });
      }
    }
    return;
  }

  if (!target.classList.contains("discovery-review-action")) return;
  if (action !== "approve" && action !== "reject") return;

  const decision = action === "approve" ? "accept" : "reject";
  const row = target.closest("tr");
  const statusCell = row ? row.querySelector(".discovery-status-cell") : null;
  const prevStatus = statusCell ? statusCell.textContent : "";
  setText("discoveryError", "");
  setText("discoveryState", "");
  target.setAttribute("disabled", "true");
  if (statusCell) statusCell.textContent = decision === "accept" ? "human_accept" : "human_reject";

  try {
    await apiPost(`/v1/sources/${encodeURIComponent(sourceId)}/review`, { decision });
    setText("discoveryState", `Reviewed ${sourceId}: ${decision}`);
    await loadDiscoveryData();
    schedulePoll();
  } catch (err) {
    setText("discoveryError", `Review failed: ${err.message}`);
    if (statusCell) statusCell.textContent = prevStatus;
  } finally {
    target.removeAttribute("disabled");
  }
}

async function handleAcquisitionTableAction(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (!target.classList.contains("acq-row-action")) return;
  const action = target.dataset.action || "";
  const sourceId = target.dataset.sourceId || "";
  const acqRunId = el("acqRunId").value.trim();
  if (!sourceId || !acqRunId) return;

  el("manualAcqRunId").value = acqRunId;
  el("manualUploadSourceId").value = sourceId;
  setContext({ acq_run_id: acqRunId, source_id: sourceId });
  if (action === "open-manual") {
    window.location.hash = "#manual-recovery";
    try {
      state.view.manual.offset = 0;
      await loadManualRecoveryData();
      schedulePoll();
    } catch (err) {
      setText("manualError", `Load failed: ${err.message}`);
    }
  } else if (action === "prefill-upload") {
    window.location.hash = "#manual-recovery";
    el("manualUploadFile").focus();
  }
}

async function showParseDocumentDetail(documentId) {
  setText("parseError", "");
  try {
    setContext({ parse_run_id: el("parseRunId").value.trim(), document_id: documentId });
    const detail = await apiGet(`/v1/parse/documents/${encodeURIComponent(documentId)}`);
    el("parseDocDetail").textContent = JSON.stringify(detail, null, 2);
  } catch (err) {
    setText("parseError", `Doc detail failed: ${err.message}`);
  }
}

async function showParseDocumentText(documentId) {
  setText("parseError", "");
  try {
    setContext({ parse_run_id: el("parseRunId").value.trim(), document_id: documentId });
    const body = await apiGet(`/v1/parse/documents/${encodeURIComponent(documentId)}/text`);
    el("parseDocText").textContent = body.text || "";
  } catch (err) {
    setText("parseError", `Doc text failed: ${err.message}`);
  }
}

async function handleParseDocumentAction(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (!target.classList.contains("parse-doc-action")) return;
  const action = target.dataset.action || "";
  const documentId = target.dataset.documentId || "";
  if (!documentId) return;
  if (action === "detail") await showParseDocumentDetail(documentId);
  else if (action === "text") await showParseDocumentText(documentId);
}

function handleManualQueueAction(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (!target.classList.contains("manual-row-action")) return;
  const sourceId = target.dataset.sourceId || "";
  if (!sourceId) return;
  el("manualUploadSourceId").value = sourceId;
  setContext({ acq_run_id: el("manualAcqRunId").value.trim(), source_id: sourceId });
  el("manualUploadFile").focus();
  setText("manualState", `Prefilled source for upload: ${sourceId}`);
}

async function exportSourcesRaw() {
  setText("discoveryError", "");
  try {
    const runId = el("discoveryRunId").value.trim();
    if (!runId) throw new Error("discovery run id is required");
    await apiDownload(`/v1/exports/sources_raw?run_id=${encodeURIComponent(runId)}`, `sources_raw_${runId}.json`);
  } catch (err) {
    setText("discoveryError", `Export failed: ${err.message}`);
  }
}

async function exportManifest() {
  setText("acqError", "");
  try {
    const acqRunId = el("acqRunId").value.trim();
    if (!acqRunId) throw new Error("acquisition run id is required");
    await apiDownload(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/manifest`, `manifest_${acqRunId}.json`);
  } catch (err) {
    setText("acqError", `Export failed: ${err.message}`);
  }
}

async function runSearchData(payload) {
  setText("searchError", "");
  const result = await apiPost("/v1/search", payload);
  state.view.search.latestItems = result.items || [];
  renderTable(
    "searchRows",
    state.view.search.latestItems.map(
      (item, idx) =>
        `<tr><td>${escapeHtml(item.source_id)}</td><td>${escapeHtml(item.document_id)}</td><td>${escapeHtml(item.chunk_id)}</td><td>${escapeHtml(String(item.score))}</td><td>${escapeHtml(item.snippet || "")}</td><td><button type="button" class="search-action" data-action="doc" data-index="${idx}">Doc</button> <button type="button" class="search-action" data-action="text" data-index="${idx}">Text</button> <button type="button" class="search-action" data-action="source" data-index="${idx}">Source</button></td></tr>`,
    ),
    6,
  );
  setText("searchState", `Results: ${result.total}`);
  state.view.search.loaded = true;
  return result;
}

async function runSearch(event) {
  event.preventDefault();
  setText("searchError", "");
  try {
    const payload = {
      parse_run_id: el("searchParseRunId").value.trim(),
      query: el("searchQuery").value.trim(),
      limit: Number(el("searchLimit").value),
    };
    if (!payload.parse_run_id) throw new Error("parse run id is required");
    if (!payload.query) throw new Error("query is required");
    state.view.search.payload = payload;
    await runSearchData(payload);
    schedulePoll();
  } catch (err) {
    setText("searchError", `Search failed: ${err.message}`);
  }
}

async function showSearchDocumentDetail(index) {
  setText("searchError", "");
  const item = state.view.search.latestItems[index];
  if (!item) return;
  try {
    const detail = await apiGet(`/v1/parse/documents/${encodeURIComponent(item.document_id)}`);
    el("searchDocDetail").textContent = JSON.stringify(detail, null, 2);
  } catch (err) {
    setText("searchError", `Doc detail failed: ${err.message}`);
  }
}

async function showSearchDocumentText(index) {
  setText("searchError", "");
  const item = state.view.search.latestItems[index];
  if (!item) return;
  try {
    const body = await apiGet(`/v1/parse/documents/${encodeURIComponent(item.document_id)}/text`);
    el("searchDocText").textContent = body.text || "";
  } catch (err) {
    setText("searchError", `Doc text failed: ${err.message}`);
  }
}

async function showSearchSourceContext(index) {
  setText("searchError", "");
  const item = state.view.search.latestItems[index];
  if (!item) return;
  try {
    const parseRunId = el("searchParseRunId").value.trim();
    if (!parseRunId) throw new Error("parse run id is required");
    const parseRun = await apiGet(`/v1/parse/runs/${encodeURIComponent(parseRunId)}`);
    const acqRun = await apiGet(`/v1/acquisition/runs/${encodeURIComponent(parseRun.acq_run_id)}`);
    const discoveryRunId = acqRun.discovery_run_id;

    let found = null;
    const limit = 100;
    for (let offset = 0; offset < 500; offset += limit) {
      const page = await apiGet(
        `/v1/discovery/runs/${encodeURIComponent(discoveryRunId)}/sources?status=all&limit=${limit}&offset=${offset}`,
      );
      found = (page.items || []).find((s) => s.id === item.source_id) || null;
      if (found || (page.items || []).length < limit) break;
    }

    el("searchSourceContext").textContent = JSON.stringify(
      {
        source_id: item.source_id,
        discovery_run_id: discoveryRunId,
        acq_run_id: parseRun.acq_run_id,
        parse_run_id: parseRunId,
        source: found,
      },
      null,
      2,
    );

    syncDiscoveryRunInputs(discoveryRunId);
    el("discoveryStatusFilter").value = "all";
    if (!state.view.discovery.loaded) {
      state.view.discovery.offset = 0;
      await loadDiscoveryData();
    }
  } catch (err) {
    setText("searchError", `Source context failed: ${err.message}`);
  }
}

async function handleSearchAction(event) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  if (!target.classList.contains("search-action")) return;
  const action = target.dataset.action || "";
  const index = Number(target.dataset.index || "-1");
  if (index < 0) return;
  if (action === "doc") await showSearchDocumentDetail(index);
  else if (action === "text") await showSearchDocumentText(index);
  else if (action === "source") await showSearchSourceContext(index);
}

async function runPollCycle() {
  const section = activeSection();
  const interval = document.visibilityState === "hidden" ? POLL_BACKGROUND_MS : POLL_ACTIVE_MS;

  try {
    await loadSystemStatus();
    let keepPolling = true;
    if (section === "work-queue" && state.view.queue.loaded) keepPolling = await loadWorkQueueData();
    else if (section === "runs") keepPolling = await refreshRunsData();
    else if (section === "discovery" && state.view.discovery.loaded) keepPolling = await loadDiscoveryData();
    else if (section === "acquisition" && state.view.acq.loaded) keepPolling = await loadAcquisitionData();
    else if (section === "parse" && state.view.parse.loaded) keepPolling = await loadParseData();
    else if (section === "manual-recovery" && state.view.manual.loaded) keepPolling = await loadManualRecoveryData();
    else if (section === "search" && state.view.search.loaded && state.view.search.payload) {
      await runSearchData(state.view.search.payload);
      const run = await apiGet(`/v1/parse/runs/${encodeURIComponent(state.view.search.payload.parse_run_id)}`);
      keepPolling = !isTerminalStatus(run.status);
    }

    if (!keepPolling) {
      setPollState(`Stopped polling on #${section}: terminal status reached.`);
      return;
    }
    setPollState(`Auto-refreshing #${section} every ${Math.round(interval / 1000)}s.`);
  } catch (err) {
    setPollState(`Stale data in #${section}: ${err.message}`, true);
  }
  schedulePoll();
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

function aiSettingsSummary(payload) {
  if (!payload.use_ai_filter) return "disabled";
  if (payload.ai_filter_active) return "active";
  return payload.ai_filter_warning || "configured_with_warning";
}

async function loadAiSettings() {
  setText("aiSettingsState", "");
  try {
    const settingsPayload = await apiGet("/v1/settings/ai-filter");
    el("aiEnabledSelect").value = settingsPayload.use_ai_filter ? "true" : "false";
    el("aiModelInput").value = settingsPayload.ai_model || "";
    el("aiBaseUrlInput").value = settingsPayload.ai_base_url || "";
    el("aiApiKeyInput").value = "";
    setText(
      "aiSettingsState",
      `AI filter ${aiSettingsSummary(settingsPayload)}; key=${settingsPayload.has_api_key ? "present" : "missing"}`,
    );
  } catch (err) {
    setText("aiSettingsState", `Load failed: ${err.message}`);
  }
}

async function saveAiSettings() {
  setText("aiSettingsState", "");
  try {
    const key = el("aiApiKeyInput").value.trim();
    const payload = {
      use_ai_filter: el("aiEnabledSelect").value === "true",
      ai_model: el("aiModelInput").value.trim(),
      ai_base_url: el("aiBaseUrlInput").value.trim(),
    };
    if (key) payload.ai_api_key = key;
    const settingsPayload = await apiPost("/v1/settings/ai-filter", payload);
    el("aiApiKeyInput").value = "";
    setText(
      "aiSettingsState",
      `Saved. AI filter ${aiSettingsSummary(settingsPayload)}; key=${settingsPayload.has_api_key ? "present" : "missing"}`,
    );
  } catch (err) {
    setText("aiSettingsState", `Save failed: ${err.message}`);
  }
}

function attachPaginationHandlers() {
  el("discoveryPrev").addEventListener("click", async () => {
    const limit = Number(el("discoveryLimit").value);
    state.view.discovery.offset = Math.max(0, state.view.discovery.offset - limit);
    try {
      await loadDiscoveryData();
      schedulePoll();
    } catch (err) {
      setText("discoveryError", `Load failed: ${err.message}`);
    }
  });
  el("discoveryNext").addEventListener("click", async () => {
    const limit = Number(el("discoveryLimit").value);
    state.view.discovery.offset += limit;
    try {
      await loadDiscoveryData();
      schedulePoll();
    } catch (err) {
      setText("discoveryError", `Load failed: ${err.message}`);
    }
  });

  el("acqPrev").addEventListener("click", async () => {
    const limit = Number(el("acqLimit").value);
    state.view.acq.offset = Math.max(0, state.view.acq.offset - limit);
    try {
      await loadAcquisitionData();
      schedulePoll();
    } catch (err) {
      setText("acqError", `Load failed: ${err.message}`);
    }
  });
  el("acqNext").addEventListener("click", async () => {
    const limit = Number(el("acqLimit").value);
    state.view.acq.offset += limit;
    try {
      await loadAcquisitionData();
      schedulePoll();
    } catch (err) {
      setText("acqError", `Load failed: ${err.message}`);
    }
  });

  el("parseDocsPrev").addEventListener("click", async () => {
    const limit = Number(el("parseDocsLimit").value);
    state.view.parse.docsOffset = Math.max(0, state.view.parse.docsOffset - limit);
    try {
      await loadParseData();
      schedulePoll();
    } catch (err) {
      setText("parseError", `Load failed: ${err.message}`);
    }
  });
  el("parseDocsNext").addEventListener("click", async () => {
    const limit = Number(el("parseDocsLimit").value);
    state.view.parse.docsOffset += limit;
    try {
      await loadParseData();
      schedulePoll();
    } catch (err) {
      setText("parseError", `Load failed: ${err.message}`);
    }
  });

  el("parseChunksPrev").addEventListener("click", async () => {
    const limit = Number(el("parseChunksLimit").value);
    state.view.parse.chunksOffset = Math.max(0, state.view.parse.chunksOffset - limit);
    try {
      await loadParseData();
      schedulePoll();
    } catch (err) {
      setText("parseError", `Load failed: ${err.message}`);
    }
  });
  el("parseChunksNext").addEventListener("click", async () => {
    const limit = Number(el("parseChunksLimit").value);
    state.view.parse.chunksOffset += limit;
    try {
      await loadParseData();
      schedulePoll();
    } catch (err) {
      setText("parseError", `Load failed: ${err.message}`);
    }
  });

  el("manualPrev").addEventListener("click", async () => {
    const limit = Number(el("manualLimit").value);
    state.view.manual.offset = Math.max(0, state.view.manual.offset - limit);
    try {
      await loadManualRecoveryData();
      schedulePoll();
    } catch (err) {
      setText("manualError", `Load failed: ${err.message}`);
    }
  });
  el("manualNext").addEventListener("click", async () => {
    const limit = Number(el("manualLimit").value);
    state.view.manual.offset += limit;
    try {
      await loadManualRecoveryData();
      schedulePoll();
    } catch (err) {
      setText("manualError", `Load failed: ${err.message}`);
    }
  });
}

function init() {
  const keyInput = el("apiKeyInput");
  const saveKeyBtn = el("saveApiKeyBtn");
  if (!AUTH_ENABLED) {
    if (keyInput) keyInput.style.display = "none";
    if (saveKeyBtn) saveKeyBtn.style.display = "none";
    state.apiKey = "";
    state.tokenSource = "none";
  } else {
  const manualToken = localStorage.getItem("km_api_key");
  if (manualToken) {
    state.apiKey = manualToken;
    state.tokenSource = "manual";
  } else if (SYSTEM_TOKEN) {
    state.apiKey = SYSTEM_TOKEN;
    state.tokenSource = "system";
  } else {
    state.apiKey = "";
    state.tokenSource = "none";
  }
  }

  keyInput.value = state.apiKey;
  setApiStateText();

  if (AUTH_ENABLED) {
    el("saveApiKeyBtn").addEventListener("click", () => {
      state.apiKey = keyInput.value.trim();
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
  }
  el("loadAiSettingsBtn").addEventListener("click", loadAiSettings);
  el("saveAiSettingsBtn").addEventListener("click", saveAiSettings);

  el("runLookupForm").addEventListener("submit", lookupRun);
  el("runFilterPhase").addEventListener("change", renderRunsTable);
  el("runFilterStatus").addEventListener("change", renderRunsTable);

  el("startDiscoveryForm").addEventListener("submit", startDiscovery);
  el("downloadSourcesRawBtn").addEventListener("click", exportSourcesRaw);
  el("discoveryForm").addEventListener("submit", loadDiscovery);
  el("discoverySources").addEventListener("click", handleDiscoveryTableAction);

  el("startAcqForm").addEventListener("submit", startAcquisition);
  el("downloadManifestBtn").addEventListener("click", exportManifest);
  el("acqForm").addEventListener("submit", loadAcquisition);
  el("acqItems").addEventListener("click", handleAcquisitionTableAction);

  el("startParseForm").addEventListener("submit", startParse);
  el("parseForm").addEventListener("submit", loadParse);
  el("parseDocuments").addEventListener("click", handleParseDocumentAction);
  el("searchForm").addEventListener("submit", runSearch);
  el("searchRows").addEventListener("click", handleSearchAction);
  el("manualRecoveryForm").addEventListener("submit", loadManualRecovery);
  el("manualExportCsvBtn").addEventListener("click", exportManualCsv);
  el("manualUploadForm").addEventListener("submit", registerManualUpload);
  el("manualQueueRows").addEventListener("click", handleManualQueueAction);

  el("copyDiscoveryIdBtn").addEventListener("click", () => copyLatestId("discovery"));
  el("copyAcqIdBtn").addEventListener("click", () => copyLatestId("acquisition"));
  el("copyParseIdBtn").addEventListener("click", () => copyLatestId("parse"));

  attachPaginationHandlers();

  document.addEventListener("visibilitychange", schedulePoll);
  window.addEventListener("hashchange", schedulePoll);

  renderRunsTable();
  loadSystemStatus();
  loadWorkQueueData();
  loadAiSettings();
  schedulePoll();
}

document.addEventListener("DOMContentLoaded", init);
  el("refreshQueueBtn").addEventListener("click", refreshWorkQueue);
  el("workQueueRows").addEventListener("click", handleQueueAction);
  el("globalSearchForm").addEventListener("submit", runGlobalSearch);
