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

export function createDocumentsModule(deps) {
  const {
    state,
    el,
    setText,
    apiPost,
    refreshDocuments,
    loadDashboard,
    ensureDiscoveryRunExists,
    getDiscoveryRunId,
    getAcqRunId,
    runBusy,
    emitTelemetryEvent,
    setLatestId,
    ensureAcquisitionRunContext,
    requiredKey,
    authHeaders,
  } = deps;

  async function handleDocumentsAction(event) {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.classList.contains("documents-select")) {
      const sourceId = target.dataset.sourceId || "";
      if (!sourceId) return;
      if (target.checked) state.documents.selected.add(sourceId);
      else state.documents.selected.delete(sourceId);
      deps.updateDocumentsSelectionControls();
      return;
    }
    if (!target.classList.contains("documents-action")) return;
    const action = target.dataset.action || "";
    const sourceId = target.dataset.sourceId || "";

    if (action === "select") {
      const item = state.documents.items.find((row) => row.source_id === sourceId);
      if (!item) return;
      const authors = Array.isArray(item.authors) ? item.authors.slice(0, 3).join(", ") : item.authors || "-";
      const metadata = `Year: ${item.year || "-"} | Journal: ${item.journal || "-"} | Citations: ${item.citations || "-"} | Authors: ${authors} | Link: ${item.source_url || item.selected_url || "-"}`;
      setText(
        "documentsDetails",
        JSON.stringify(
          {
            title: item.title,
            metadata,
            source_id: item.source_id,
            doi: item.doi,
            source_url: item.source_url,
            selected_url: item.selected_url,
            attempts: item.attempt_count,
            error: item.last_error,
            reason: item.problem,
          },
          null,
          2,
        ),
      );
      return;
    }

    if (action === "upload") {
      state.documents.selectedSourceId = sourceId;
      const sourceInput = el("manualUploadSourceId");
      if (sourceInput) sourceInput.value = sourceId;
      setText("documentsState", `Upload target selected: ${sourceId}`);
      el("manualUploadFile").focus();
      return;
    }

    if (action === "retry") {
      try {
        const runId = getDiscoveryRunId();
        await ensureDiscoveryRunExists(runId);
        await apiPost("/v1/acquisition/runs", { run_id: runId, retry_failed_only: true });
        setText("documentsState", "Retry download started.");
        await refreshDocuments(true);
        await loadDashboard();
      } catch (err) {
        emitTelemetryEvent("change", target, `action:retry_download:blocked reason=${String(err.message || "unknown")}`);
        setText("documentsError", `Retry failed: ${err.message}`);
      }
    }

    if (action === "manual-complete") {
      try {
        const acqRunId = getAcqRunId();
        if (!acqRunId) throw new Error("acquisition run id is required");
        await apiPost(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/manual-complete`, { source_id: sourceId });
        setText("documentsState", "Manual completion saved.");
        await refreshDocuments(true);
        await loadDashboard();
      } catch (err) {
        setText("documentsError", `Manual complete failed: ${err.message}`);
      }
    }
  }

  async function registerManualUpload(event) {
    event.preventDefault();
    setText("documentsError", "");
    try {
      const acqRunId = await ensureAcquisitionRunContext({ retryFailedOnly: false });
      const sourceId = ((el("manualUploadSourceId")?.value || "").trim()) || state.documents.selectedSourceId;
      const fileInput = el("manualUploadFile");
      const file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
      if (!sourceId) throw new Error("source id is required");
      if (!file) throw new Error("file is required");
      const contentBase64 = await fileToBase64(file);
      await apiPost(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/manual-upload`, {
        source_id: sourceId,
        filename: file.name,
        content_base64: contentBase64,
        content_type: file.type || null,
      });
      setText("documentsState", "Manual upload registered.");
      await refreshDocuments(true);
    } catch (err) {
      setText("documentsError", `Upload failed: ${err.message}`);
    }
  }

  async function documentsAcquirePending() {
    const runId = getDiscoveryRunId();
    if (!runId) {
      setText("documentsError", "Discovery run context is required.");
      return;
    }
    const acceptedPending = Number((el("statusAwaitingDocs")?.textContent || "0").trim());
    if (!Number.isFinite(acceptedPending) || acceptedPending <= 0) {
      setText("documentsState", "No approved sources are waiting for download.");
      return;
    }
    emitTelemetryEvent("submit", el("documentsAcquirePendingBtn") || document.body, `action:process_approved_docs:start run_id=${runId}`);
    try {
      await ensureDiscoveryRunExists(runId);
      const next = await runBusy("acquisition", ["documentsAcquirePendingBtn"], async () =>
        apiPost("/v1/acquisition/runs", { run_id: runId, retry_failed_only: false }),
      );
      emitTelemetryEvent(
        "change",
        el("documentsAcquirePendingBtn") || document.body,
        `action:process_approved_docs:success run_id=${runId} acq_run_id=${next.acq_run_id} accepted_count=${(el("statusAwaitingDocs")?.textContent || "0").trim()}`,
      );
      setLatestId("acquisition", next.acq_run_id);
      setText("documentsState", "Started document download.");
      await refreshDocuments(true);
      await loadDashboard();
    } catch (err) {
      emitTelemetryEvent(
        "change",
        el("documentsAcquirePendingBtn") || document.body,
        `action:process_approved_docs:error run_id=${runId} error=${String(err.message || "unknown")}`,
      );
      setText("documentsError", `Download documents failed: ${err.message}`);
    }
  }

  async function documentsRetryFailed() {
    const runId = getDiscoveryRunId();
    if (!runId) {
      setText("documentsError", "Discovery run context is required.");
      return;
    }
    try {
      await ensureDiscoveryRunExists(runId);
      const next = await runBusy("acquisition", ["documentsRetryFailedBtn"], async () =>
        apiPost("/v1/acquisition/runs", { run_id: runId, retry_failed_only: true }),
      );
      setLatestId("acquisition", next.acq_run_id);
      setText("documentsState", "Started retry-failed acquisition.");
      await refreshDocuments(true);
    } catch (err) {
      setText("documentsError", `Retry failed failed: ${err.message}`);
    }
  }

  async function documentsCopySelected() {
    const selected = state.documents.items.filter((row) => state.documents.selected.has(row.source_id));
    if (!selected.length) {
      setText("documentsError", "Select at least one row.");
      return;
    }
    const values = selected
      .map((row) => [row.doi, row.source_url, row.selected_url].filter(Boolean).join(" | "))
      .filter(Boolean)
      .join("\n");
    if (!values) {
      setText("documentsError", "No DOI/URL values available in selected rows.");
      return;
    }
    try {
      await navigator.clipboard.writeText(values);
      setText("documentsState", "Copied");
    } catch (_err) {
      setText("documentsError", "Copy failed.");
    }
  }

  function selectAllDocumentsRows() {
    for (const row of state.documents.items) state.documents.selected.add(row.source_id);
    setText("documentsState", `Selected ${state.documents.items.length} rows.`);
    refreshDocuments(true).catch(() => {});
  }

  function deselectAllDocumentsRows() {
    state.documents.selected.clear();
    setText("documentsState", "Selection cleared.");
    refreshDocuments(true).catch(() => {});
  }

  function toggleBatchUploadPanel() {
    const panel = el("batchUploadForm");
    if (!panel) return;
    panel.hidden = !panel.hidden;
  }

  async function uploadBatchDocuments(event) {
    event.preventDefault();
    setText("documentsError", "");
    const input = el("batchUploadFiles");
    const files = input?.files ? Array.from(input.files) : [];
    if (!files.length) {
      setText("documentsError", "Select at least one file.");
      return;
    }
    try {
      const payload = await runBusy("batch_upload", ["batchUploadSubmitBtn"], async () => {
        const acqRunId = await ensureAcquisitionRunContext({ retryFailedOnly: false });
        const form = new FormData();
        for (const file of files) form.append("files", file);
        requiredKey();
        const res = await fetch(`/v1/acquisition/runs/${encodeURIComponent(acqRunId)}/manual-upload-batch`, {
          method: "POST",
          headers: authHeaders(),
          body: form,
        });
        if (!res.ok) {
          let detail = `${res.status}`;
          try {
            const body = await res.json();
            detail = body.detail || detail;
          } catch (_err) {
            // ignore
          }
          throw new Error(detail);
        }
        return res.json();
      });
      setText("batchUploadResults", JSON.stringify(payload, null, 2));
      setText("documentsState", `Batch upload complete: matched=${payload.matched}, unmatched=${payload.unmatched}, ambiguous=${payload.ambiguous}`);
      await refreshDocuments(true);
      await loadDashboard();
    } catch (err) {
      setText("documentsError", `Batch upload failed: ${err.message}`);
    }
  }

  return {
    handleDocumentsAction,
    registerManualUpload,
    documentsAcquirePending,
    documentsRetryFailed,
    documentsCopySelected,
    selectAllDocumentsRows,
    deselectAllDocumentsRows,
    toggleBatchUploadPanel,
    uploadBatchDocuments,
  };
}
