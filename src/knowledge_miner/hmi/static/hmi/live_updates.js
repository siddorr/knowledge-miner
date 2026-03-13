export function createLiveUpdatesModule(deps) {
  const {
    state,
    AUTH_ENABLED,
    POLL_ACTIVE_MS,
    POLL_BACKGROUND_MS,
    POLL_DISCONNECTED_IDLE_MS,
    activeSection,
    hasActiveWork,
    setPollState,
    setLiveUpdatesState,
    loadSystemStatus,
    refreshCurrentSection,
    refreshRunProgress,
    updateFreshness,
    broadcastSnapshot,
    isReadRateLimitedError,
    resetStaleRunContext,
    refreshReview,
    refreshDocuments,
    loadDashboard,
  } = deps;

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

  async function refreshForSection(section) {
    if (section === "review" && state.review.loaded) return refreshReview();
    if (section === "documents" && state.documents.loaded) return refreshDocuments();
    return refreshCurrentSection();
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
      await refreshForSection(section);
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

  return {
    schedulePoll,
    runPollCycle,
    runLiveRefresh,
    queueLiveRefresh,
    openLiveUpdatesChannel,
  };
}
