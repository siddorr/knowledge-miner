export function createSessionModule(deps) {
  const {
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
    createNewSession,
    sessionsStorageKey,
    sessionsAutoRestoreKey,
  } = deps;

  function loadSessionsFromStorage() {
    try {
      const raw = localStorage.getItem(sessionsStorageKey);
      if (!raw) {
        state.sessions.items = [];
        return;
      }
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) throw new Error("invalid_session_format");
      state.sessions.items = parsed.filter((row) => row && typeof row.id === "string" && row.state);
    } catch (_err) {
      state.sessions.items = [];
      localStorage.removeItem(sessionsStorageKey);
      setText("sessionState", "Session store was corrupted and has been reset.");
    }
  }

  function saveSessionsToStorage() {
    localStorage.setItem(sessionsStorageKey, JSON.stringify(state.sessions.items.slice(0, 20)));
  }

  function sessionSummaryLabel(item) {
    const name = item.name || item.id;
    const when = item.updated_at ? new Date(item.updated_at).toLocaleString() : "unknown";
    return `${name} (${when})`;
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
        startAcqRunId: el("startAcqRunId")?.value || "",
        startParseAcqRunId: el("startParseAcqRunId")?.value || "",
      },
    };
  }

  function applySessionState(snapshot) {
    if (!snapshot || typeof snapshot !== "object") throw new Error("invalid_session_payload");
    const section = snapshot.section || "build";
    setLatestId("discovery", snapshot.latest?.discovery || "");
    setLatestId("acquisition", snapshot.latest?.acquisition || "");
    setLatestId("parse", snapshot.latest?.parse || "");

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
    if (el("manualUploadSourceId")) el("manualUploadSourceId").value = snapshot.documents?.manualSourceId || "";

    if (el("searchQuery")) el("searchQuery").value = snapshot.library?.query || "";
    if (el("searchLimit")) el("searchLimit").value = snapshot.library?.limit || "20";
    if (el("libraryTopicFilter")) el("libraryTopicFilter").value = snapshot.library?.topicFilter || "";
    if (el("libraryYearFilter")) el("libraryYearFilter").value = snapshot.library?.yearFilter || "";
    if (el("libraryDocsFilter")) el("libraryDocsFilter").value = snapshot.library?.docsFilter || "all";
    if (el("libraryParsedFilter")) el("libraryParsedFilter").value = snapshot.library?.parsedFilter || "all";
    if (snapshot.library?.parseRunIdInput && !state.latest.parse) setLatestId("parse", snapshot.library.parseRunIdInput);
    if (snapshot.documents?.acqRunIdInput && !state.latest.acquisition) setLatestId("acquisition", snapshot.documents.acqRunIdInput);
    if (snapshot.ids?.discoverRunIdInput && !state.latest.discovery) setLatestId("discovery", snapshot.ids.discoverRunIdInput);

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

  async function validateSessionSnapshot(snapshot) {
    const clone = JSON.parse(JSON.stringify(snapshot || {}));
    const notes = [];
    const discoveryIds = new Set();
    const acquisitionIds = new Set();
    const parseIds = new Set();
    const discoveryCandidates = [clone.latest?.discovery, clone.ids?.discoverRunIdInput, clone.ids?.startAcqRunId];
    const acquisitionCandidates = [clone.latest?.acquisition, clone.documents?.acqRunIdInput, clone.ids?.startParseAcqRunId];
    const parseCandidates = [clone.latest?.parse, clone.library?.parseRunIdInput];
    for (const id of discoveryCandidates) if (id) discoveryIds.add(String(id));
    for (const id of acquisitionCandidates) if (id) acquisitionIds.add(String(id));
    for (const id of parseCandidates) if (id) parseIds.add(String(id));

    const validDiscovery = new Set();
    const validAcquisition = new Set();
    const validParse = new Set();
    await Promise.all(
      Array.from(discoveryIds).map(async (id) => {
        try {
          await apiGet(`/v1/discovery/runs/${encodeURIComponent(id)}`);
          validDiscovery.add(id);
        } catch (_err) {
          notes.push(`discovery cleared: ${id}`);
        }
      }),
    );
    await Promise.all(
      Array.from(acquisitionIds).map(async (id) => {
        try {
          await apiGet(`/v1/acquisition/runs/${encodeURIComponent(id)}`);
          validAcquisition.add(id);
        } catch (_err) {
          notes.push(`acquisition cleared: ${id}`);
        }
      }),
    );
    await Promise.all(
      Array.from(parseIds).map(async (id) => {
        try {
          await apiGet(`/v1/parse/runs/${encodeURIComponent(id)}`);
          validParse.add(id);
        } catch (_err) {
          notes.push(`parse cleared: ${id}`);
        }
      }),
    );

    const keepDiscovery = (v) => (v && validDiscovery.has(String(v)) ? String(v) : "");
    const keepAcquisition = (v) => (v && validAcquisition.has(String(v)) ? String(v) : "");
    const keepParse = (v) => (v && validParse.has(String(v)) ? String(v) : "");
    clone.latest = clone.latest || {};
    clone.ids = clone.ids || {};
    clone.documents = clone.documents || {};
    clone.library = clone.library || {};
    clone.latest.discovery = keepDiscovery(clone.latest.discovery);
    clone.ids.discoverRunIdInput = keepDiscovery(clone.ids.discoverRunIdInput);
    clone.ids.startAcqRunId = keepDiscovery(clone.ids.startAcqRunId);
    clone.latest.acquisition = keepAcquisition(clone.latest.acquisition);
    clone.documents.acqRunIdInput = keepAcquisition(clone.documents.acqRunIdInput);
    clone.ids.startParseAcqRunId = keepAcquisition(clone.ids.startParseAcqRunId);
    clone.latest.parse = keepParse(clone.latest.parse);
    clone.library.parseRunIdInput = keepParse(clone.library.parseRunIdInput);
    return { snapshot: clone, notes };
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
      const validated = await validateSessionSnapshot(item.state);
      applySessionState(validated.snapshot);
      await loadDashboard();
      if (activeSection() === "review") scheduleReviewAutoLoad("session_restore");
      if (activeSection() === "documents") await refreshDocuments(true);
      if (activeSection() === "library") await runSearch();
      if (validated.notes.length) setText("sessionState", `Session loaded with cleared stale IDs: ${validated.notes.join("; ")}`);
      else setText("sessionState", `Session loaded: ${item.name}`);
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

  function initSessionPersistence() {
    loadSessionsFromStorage();
    const autoRestoreSaved = localStorage.getItem(sessionsAutoRestoreKey);
    state.sessions.autoRestore = autoRestoreSaved == null ? true : autoRestoreSaved === "true";
    const checkbox = el("autoRestoreSession");
    if (checkbox) checkbox.checked = state.sessions.autoRestore;
    renderSessionHistory();

    addListener("saveSessionBtn", "click", saveCurrentSession);
    addListener("topSaveSessionBtn", "click", saveCurrentSession);
    addListener("topLoadSessionBtn", "click", () => {
      loadSelectedSession().catch((err) => setText("sessionState", `Session load failed: ${err.message}`));
    });
    addListener("topDeleteSessionBtn", "click", deleteSelectedSession);
    addListener("topNewSessionBtn", "click", createNewSession);
    addListener("loadSessionBtn", "click", () => {
      loadSelectedSession().catch((err) => setText("sessionState", `Session load failed: ${err.message}`));
    });
    addListener("deleteSessionBtn", "click", deleteSelectedSession);
    addListener("autoRestoreSession", "change", () => {
      const enabled = !!el("autoRestoreSession").checked;
      state.sessions.autoRestore = enabled;
      localStorage.setItem(sessionsAutoRestoreKey, enabled ? "true" : "false");
      setText("sessionState", enabled ? "Auto-restore enabled." : "Auto-restore disabled.");
    });
  }

  return {
    loadSessionsFromStorage,
    saveSessionsToStorage,
    sessionSummaryLabel,
    renderSessionHistory,
    captureSessionState,
    applySessionState,
    saveCurrentSession,
    validateSessionSnapshot,
    loadSelectedSession,
    deleteSelectedSession,
    initSessionPersistence,
  };
}
