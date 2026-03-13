export function createReviewModule(deps) {
  const {
    state,
    setText,
    apiPost,
    getDiscoveryRunId,
    refreshReview,
    loadDashboard,
    renderReviewDetails,
    renderFastReviewCard,
    updateReviewSelectionControls,
    activeSection,
  } = deps;

  async function loadReviewClick(event) {
    if (event && event.preventDefault) event.preventDefault();
    state.review.offset = 0;
    try {
      await refreshReview(true);
    } catch (err) {
      setText("reviewError", `Load failed: ${err.message}`);
    }
  }

  async function applyReviewDecisionToSelected(decision) {
    const selected = Array.from(state.review.selected);
    if (!selected.length) {
      setText("reviewError", "Select at least one row.");
      return;
    }
    const runId = getDiscoveryRunId();
    if (!runId) {
      setText("reviewError", "Active discovery run is required.");
      return;
    }
    setText("reviewError", "");
    let ok = 0;
    let mismatches = 0;
    for (const sourceId of selected) {
      try {
        await apiPost(`/v1/sources/${encodeURIComponent(sourceId)}/review`, { decision, run_id: runId });
        ok += 1;
      } catch (err) {
        if (String(err.message || "").includes("run_context_mismatch")) mismatches += 1;
      }
    }
    state.review.selected.clear();
    if (decision === "accept") {
      setText("reviewState", `Accepted ${ok}/${selected.length}. Open Documents to upload PDFs or run auto-acquisition.`);
    } else {
      setText("reviewState", `${decision} applied to ${ok}/${selected.length} selected rows.`);
    }
    if (mismatches > 0) {
      setText("reviewError", `Run context mismatch on ${mismatches} rows. Refresh queue or switch context explicitly.`);
    }
    await refreshReview(true);
    await loadDashboard();
  }

  async function applySingleReviewDecision(sourceId, decision) {
    if (!sourceId) return;
    const runId = getDiscoveryRunId();
    if (!runId) {
      setText("reviewError", "Active discovery run is required.");
      return;
    }
    try {
      await apiPost(`/v1/sources/${encodeURIComponent(sourceId)}/review`, { decision, run_id: runId });
      if (decision === "accept") setText("reviewState", "Accepted. Open Documents to upload PDFs or run auto-acquisition.");
      else if (decision === "reject") setText("reviewState", "Rejected.");
      else setText("reviewState", "Moved to Later.");
      await refreshReview(true);
      await loadDashboard();
    } catch (err) {
      if (String(err.message || "").includes("run_context_mismatch")) {
        setText("reviewError", "Run context mismatch. Refresh review queue or switch context explicitly.");
      } else {
        setText("reviewError", `Review failed: ${err.message}`);
      }
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

  return {
    loadReviewClick,
    handleReviewAction,
    applyReviewDecisionToSelected,
    applySingleReviewDecision,
    fastReviewMove,
    fastReviewDecision,
    handleReviewShortcuts,
  };
}

