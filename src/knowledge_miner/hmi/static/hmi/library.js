export function createLibraryModule(deps) {
  const {
    state,
    el,
    setText,
    renderTable,
    escapeHtml,
    apiGet,
    apiPost,
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
  };
}

