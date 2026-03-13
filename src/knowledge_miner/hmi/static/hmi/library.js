export function createLibraryModule(deps) {
  const {
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
  } = deps;

  function libraryFilters() {
    return {
      topic: (el("libraryTopicFilter")?.value || "").trim().toLowerCase(),
      year: (el("libraryYearFilter")?.value || "").trim(),
      docs: (el("libraryDocsFilter")?.value || "all").trim(),
      parsed: (el("libraryParsedFilter")?.value || "all").trim(),
    };
  }

  function ensureExportSelection() {
    if (!(state.search.exportSelection instanceof Set)) state.search.exportSelection = new Set();
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
    const authors = Array.isArray(doc.authors) ? doc.authors.slice(0, 3).join(", ") : doc.authors || "-";
    const metadata = `Year: ${doc.publication_year || "-"} | Journal: ${doc.journal || "-"} | Citations: ${doc.citations || doc.citation_count || "-"} | Authors: ${authors} | Link: ${doc.url || "-"}`;
    setText(
      "searchPreview",
      JSON.stringify(
        {
          title: doc.title || "",
          metadata,
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
    ensureExportSelection();
    renderTable(
      "searchRows",
      items.map((item, idx) => {
        const checked = state.search.exportSelection.has(item.document_id) ? " checked" : "";
        const year = item.document?.publication_year ?? "-";
        const score = item.document?.relevance_score ?? item.score ?? "-";
        const citations = item.document?.citations ?? item.document?.citation_count ?? "-";
        const title = item.document?.title || item.snippet || item.document_id || "";
        return `<tr><td><input type="checkbox" class="library-select" data-document-id="${escapeHtml(item.document_id)}"${checked}></td><td>${idx + 1}</td><td>${escapeHtml(String(score))}</td><td>${escapeHtml(String(year))}</td><td>${escapeHtml(String(citations))}</td><td><button type="button" class="search-action" data-action="doc" data-index="${idx}">${escapeHtml(title)}</button> <button type="button" class="search-action" data-action="text" data-index="${idx}">Text</button> <button type="button" class="search-action" data-action="source" data-index="${idx}">Source</button></td></tr>`;
      }),
      6,
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
    setContext({
      parse_run_id: parseRunId,
      acq_run_id: parseRun.acq_run_id,
      discovery_run_id: acqRun.discovery_run_id,
      source_id: item.source_id,
    });
    el("searchSourceContext").textContent = JSON.stringify(state.context, null, 2);
    const doc = item.document || (state.search.docsById.get(item.document_id) || null);
    if (doc) setSearchPreview(doc, item.snippet || "");
    window.location.hash = "#review";
  }

  async function handleSearchAction(event) {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.classList.contains("library-select")) {
      ensureExportSelection();
      const documentId = target.dataset.documentId || "";
      if (!documentId) return;
      if (target.checked) state.search.exportSelection.add(documentId);
      else state.search.exportSelection.delete(documentId);
      return;
    }
    if (!target.classList.contains("search-action")) return;
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

  function selectedExportRows() {
    ensureExportSelection();
    const selected = state.search.items.filter((row) => state.search.exportSelection.has(row.document_id));
    if (selected.length) return selected;
    const size = Number(el("libraryExportSize")?.value || "20");
    return state.search.items.slice(0, Math.max(1, size));
  }

  function csvCell(value) {
    const raw = String(value ?? "");
    const escaped = raw.replaceAll('"', '""');
    return `"${escaped}"`;
  }

  async function exportMetadataCsv() {
    const rows = selectedExportRows();
    const header = ["title", "authors", "year", "journal", "citations", "ai_score", "status", "source_link"];
    const lines = [header.join(",")];
    for (const row of rows) {
      const doc = row.document || {};
      lines.push(
        [
          csvCell(doc.title || row.snippet || row.document_id),
          csvCell(doc.authors || ""),
          csvCell(doc.publication_year || ""),
          csvCell(doc.journal || ""),
          csvCell(doc.citations || doc.citation_count || ""),
          csvCell(doc.relevance_score ?? row.score ?? ""),
          csvCell(doc.status || ""),
          csvCell(doc.url || ""),
        ].join(","),
      );
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `library_export_${Date.now()}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    setText("searchState", `Exported metadata CSV for ${rows.length} rows.`);
  }

  async function exportPdfZip() {
    const parseRunId = getParseRunId();
    if (!parseRunId) throw new Error("parse run id is required");
    const parseRun = await apiGet(`/v1/parse/runs/${encodeURIComponent(parseRunId)}`);
    if (!parseRun?.acq_run_id) throw new Error("acquisition run id not found");
    await apiDownload(`/v1/acquisition/runs/${encodeURIComponent(parseRun.acq_run_id)}/manifest`, `library_export_${parseRun.acq_run_id}.json`);
    setText("searchState", "Downloaded acquisition manifest. ZIP export endpoint is planned.");
  }

  function includeSelected() {
    ensureExportSelection();
    const size = Number(el("libraryExportSize")?.value || "20");
    for (const row of state.search.items.slice(0, Math.max(1, size))) state.search.exportSelection.add(row.document_id);
    renderLibraryRows(state.search.items, "Library export selection");
  }

  function excludeSelected() {
    ensureExportSelection();
    state.search.exportSelection.clear();
    renderLibraryRows(state.search.items, "Library export selection");
  }

  return {
    libraryFilters,
    libraryDocPassesFilters,
    setSearchPreview,
    renderLibraryRows,
    ensureSearchDocsCache,
    runSearchData,
    loadLibraryBrowser,
    runSearch,
    showSearchDoc,
    showSearchText,
    showSearchSource,
    handleSearchAction,
    exportMetadataCsv,
    exportPdfZip,
    includeSelected,
    excludeSelected,
  };
}
