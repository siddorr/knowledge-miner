const state = {
  apiKey: localStorage.getItem("km_api_key") || "",
  runRows: [],
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

async function loadDiscovery(event) {
  event.preventDefault();
  setText("discoveryError", "");
  const runId = el("discoveryRunId").value.trim();
  const status = el("discoveryStatusFilter").value;
  if (!runId) return;

  try {
    const [run, sources] = await Promise.all([
      apiGet(`/v1/discovery/runs/${encodeURIComponent(runId)}`),
      apiGet(`/v1/discovery/runs/${encodeURIComponent(runId)}/sources?status=${encodeURIComponent(status)}&limit=50`),
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
  } catch (err) {
    setText("discoveryError", `Load failed: ${err.message}`);
  }
}

async function loadAcquisition(event) {
  event.preventDefault();
  setText("acqError", "");
  const runId = el("acqRunId").value.trim();
  if (!runId) return;

  try {
    const [run, items] = await Promise.all([
      apiGet(`/v1/acquisition/runs/${encodeURIComponent(runId)}`),
      apiGet(`/v1/acquisition/runs/${encodeURIComponent(runId)}/items?limit=100`),
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
  } catch (err) {
    setText("acqError", `Load failed: ${err.message}`);
  }
}

async function loadParse(event) {
  event.preventDefault();
  setText("parseError", "");
  const runId = el("parseRunId").value.trim();
  if (!runId) return;

  try {
    const [run, docs, chunks] = await Promise.all([
      apiGet(`/v1/parse/runs/${encodeURIComponent(runId)}`),
      apiGet(`/v1/parse/runs/${encodeURIComponent(runId)}/documents?limit=50`),
      apiGet(`/v1/parse/runs/${encodeURIComponent(runId)}/chunks?limit=50`),
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
  } catch (err) {
    setText("parseError", `Load failed: ${err.message}`);
  }
}

function setApiStateText() {
  setText("authState", state.apiKey ? "Key saved" : "Key not set");
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
  renderRunsTable();
}

document.addEventListener("DOMContentLoaded", init);
