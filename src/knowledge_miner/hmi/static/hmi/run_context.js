export function createRunContextModule(deps) {
  const {
    state,
    el,
    setText,
    activeSection,
    scheduleReviewAutoLoad,
    apiGet,
    emitTelemetryEvent,
    resetStaleRunContext,
  } = deps;

  function setLatestId(kind, value) {
    const trimmed = (value || "").trim();
    state.latest[kind] = trimmed;
    if (kind === "discovery") {
      setText("latestDiscoveryId", trimmed || "-");
      setText("statusActiveDiscoveryRun", trimmed || "-");
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
        if (!String(err.message || "").includes("missing")) throw err;
      }
    }
    const discoveryRunId = getDiscoveryRunId();
    await ensureDiscoveryRunExists(discoveryRunId);
    const next = await deps.apiPost("/v1/acquisition/runs", { run_id: discoveryRunId, retry_failed_only: retryFailedOnly });
    acqRunId = next.acq_run_id;
    setLatestId("acquisition", acqRunId);
    return acqRunId;
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

  return {
    setLatestId,
    getDiscoveryRunId,
    getAcqRunId,
    getParseRunId,
    ensureDiscoveryRunExists,
    ensureAcquisitionRunExists,
    ensureAcquisitionRunContext,
    useLatestRunContext,
  };
}
