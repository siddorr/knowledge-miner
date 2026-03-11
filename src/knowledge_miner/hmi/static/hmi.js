const POLL_ACTIVE_MS = 5000;
const POLL_BACKGROUND_MS = 15000;

const state = {
  apiKey: localStorage.getItem("km_api_key") || "",
  runRows: [],
  pollTimer: null,
  view: {
    discovery: { loaded: false, offset: 0 },
    acq: { loaded: false, offset: 0 },
    parse: { loaded: false, docsOffset: 0, chunksOffset: 0 },
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

function requiredKey() {
  if (!state.apiKey) {
    throw new Error("API key is required");
  }
}

function isTerminalStatus(status) {
  return status === "completed" || status === "failed";
}

function activeSection() {
  const id = window.location.hash.replace("#", "") || "runs";
  if (["runs", "discovery", "acquisition", "parse", "search", "manual-recovery"].includes(id)) {
    return id;
  }
  return "runs";
}

function setPollState(message, stale = false) {
  const node = el("pollState");
  if (!node) return;
  node.textContent = message;
  node.classList.remove("poll-ok", "poll-stale");
  node.classList.add(stale ? "poll-stale" : "poll-ok");
}

function schedulePoll() {
  if (state.pollTimer) {
    clearTimeout(state.pollTimer);
  }
  const interval = document.visibilityState === "hidden" ? POLL_BACKGROUND_MS : POLL_ACTIVE_MS;
  state.pollTimer = setTimeout(runPollCycle, interval);
}

async function apiGet(path) {
  requiredKey();
  const res = await fetch(path, {
    headers: { Authorization: `Bearer ${state.apiKey}` },
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

function summaryForRun(phase, payload) {
  if (phase === "discovery") {
    return `iter=${payload.current_iteration}, accepted=${payload.accepted_total}, expanded=${payload.expanded_candidates_total}`;
  }
  if (phase === "acquisition") {
    return `downloaded=${payload.downloaded_total}, partial=${payload.partial_total}, failed=${payload.failed_total}`;
  }
  return `parsed=${payload.parsed_total}, failed=${payload.failed_total}, chunks=${payload.chunked_total}`;
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
    const idx = state.runRows.findIndex((r) => r.phase === phase && r.id === runId);
    const row = {
      phase,
      id: runId,
      status: payload.status,
      summary: summaryForRun(phase, payload),
    };
    if (idx >= 0) state.runRows[idx] = row;
    else state.runRows.unshift(row);
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
    apiGet(
      `/v1/discovery/runs/${encodeURIComponent(runId)}/sources?status=${encodeURIComponent(status)}&limit=${limit}&offset=${offset}`,
    ),
  ]);

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

  const rows = sources.items.map(
    (s) =>
      `<tr><td>${escapeHtml(s.id)}</td><td>${escapeHtml(s.title)}</td><td>${escapeHtml(s.review_status)}</td><td>${escapeHtml(String(s.relevance_score))}</td><td>${escapeHtml(s.type)}</td><td>${escapeHtml(s.source)}</td></tr>`,
  );
  renderTable("discoverySources", rows, 6);
  setText("discoveryPage", `offset=${offset}, limit=${limit}, total=${sources.total}`);
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

  const rows = items.items.map(
    (i) =>
      `<tr><td>${escapeHtml(i.item_id)}</td><td>${escapeHtml(i.source_id)}</td><td>${escapeHtml(i.status)}</td><td>${escapeHtml(String(i.attempt_count))}</td><td>${escapeHtml(i.selected_url || "")}</td><td>${escapeHtml(i.last_error || "")}</td></tr>`,
  );
  renderTable("acqItems", rows, 6);
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
        `<tr><td>${escapeHtml(d.document_id)}</td><td>${escapeHtml(d.status)}</td><td>${escapeHtml(d.decision || "")}</td><td>${escapeHtml(String(d.confidence || ""))}</td><td>${escapeHtml(d.parser_used || "")}</td><td>${escapeHtml(String(d.char_count))}</td></tr>`,
    ),
    6,
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

async function runPollCycle() {
  const section = activeSection();
  const interval = document.visibilityState === "hidden" ? POLL_BACKGROUND_MS : POLL_ACTIVE_MS;

  try {
    let keepPolling = true;
    if (section === "runs") {
      keepPolling = await refreshRunsData();
    } else if (section === "discovery" && state.view.discovery.loaded) {
      keepPolling = await loadDiscoveryData();
    } else if (section === "acquisition" && state.view.acq.loaded) {
      keepPolling = await loadAcquisitionData();
    } else if (section === "parse" && state.view.parse.loaded) {
      keepPolling = await loadParseData();
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
  setText("authState", state.apiKey ? "Key saved" : "Key not set");
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
}

function init() {
  const keyInput = el("apiKeyInput");
  keyInput.value = state.apiKey;
  setApiStateText();

  el("saveApiKeyBtn").addEventListener("click", () => {
    state.apiKey = keyInput.value.trim();
    if (state.apiKey) localStorage.setItem("km_api_key", state.apiKey);
    else localStorage.removeItem("km_api_key");
    setApiStateText();
  });

  el("runLookupForm").addEventListener("submit", lookupRun);
  el("runFilterPhase").addEventListener("change", renderRunsTable);
  el("runFilterStatus").addEventListener("change", renderRunsTable);
  el("discoveryForm").addEventListener("submit", loadDiscovery);
  el("acqForm").addEventListener("submit", loadAcquisition);
  el("parseForm").addEventListener("submit", loadParse);

  attachPaginationHandlers();

  document.addEventListener("visibilitychange", schedulePoll);
  window.addEventListener("hashchange", schedulePoll);

  renderRunsTable();
  schedulePoll();
}

document.addEventListener("DOMContentLoaded", init);
