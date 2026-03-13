export function createBuildModule(deps) {
  const { state, el, setText, escapeHtml, renderTable, updateStatusStrip } = deps;

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

  return {
    activeTopic,
    renderBuildDetails,
    renderBuildSources,
    renderBuildTopics,
    setBuildTab,
    sourceFingerprint,
    emptyTopicCoverage,
    mergeTopicCoverage,
    applyActiveTopicCoverageToShell,
    handleBuildTopicClick,
    createNewTopic,
    handleAddSource,
    handleBulkSource,
    handleBuildQuery,
  };
}
