const SAFE_VALUE_PREVIEW_IDS = new Set([
  "startDiscoverySeeds",
  "searchQuery",
  "globalSearchQuery",
  "discoverRunIdInput",
  "reviewRunIdInput",
  "documentsAcqRunIdInput",
  "searchParseRunIdInput",
  "runIdInput",
]);

function controlIdFromTarget(target) {
  if (!target) return "unknown";
  const raw = target.id || target.name || target.getAttribute("data-action") || target.tagName.toLowerCase();
  return String(raw).slice(0, 120);
}

function controlLabelFromTarget(target) {
  if (!target) return null;
  const aria = target.getAttribute("aria-label");
  if (aria) return aria.slice(0, 160);
  const text = (target.textContent || "").trim();
  if (text) return text.slice(0, 160);
  if (target.id) {
    try {
      const label = document.querySelector(`label[for="${CSS.escape(target.id)}"]`);
      const labelText = (label?.textContent || "").trim();
      if (labelText) return labelText.slice(0, 160);
    } catch (_err) {
      // ignore query/escape failures
    }
  }
  return null;
}

function sanitizeValuePreview(target) {
  if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement)) {
    return null;
  }
  const controlId = controlIdFromTarget(target);
  const inputType = target instanceof HTMLInputElement ? (target.type || "").toLowerCase() : "";
  if (["password", "file", "hidden"].includes(inputType)) return "[redacted]";
  if (!SAFE_VALUE_PREVIEW_IDS.has(controlId)) return "[redacted]";
  const value = String(target.value || "").trim();
  if (!value) return "";
  if (value.length <= 120) return value;
  return `${value.slice(0, 120)}...`;
}

export function createTelemetryClient({
  state,
  authEnabled,
  authHeaders,
  activeSection,
  telemetryInputDebounceMs,
}) {
  function telemetryHeaders() {
    if (authEnabled && !state.apiKey) return null;
    return { ...authHeaders(), "Content-Type": "application/json" };
  }

  function telemetrySessionId() {
    if (state.telemetry.sessionId) return state.telemetry.sessionId;
    const seed = `${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
    state.telemetry.sessionId = `hmi_${seed}`;
    return state.telemetry.sessionId;
  }

  function emitEvent(eventType, target, forcedValuePreview = undefined) {
    const headers = telemetryHeaders();
    if (!headers) return;
    const sectionNode = target?.closest ? target.closest("section") : null;
    const valuePreview = forcedValuePreview !== undefined ? forcedValuePreview : sanitizeValuePreview(target);
    const payload = {
      events: [
        {
          event_type: eventType,
          control_id: controlIdFromTarget(target),
          control_label: controlLabelFromTarget(target),
          page: activeSection(),
          section: sectionNode?.id || activeSection(),
          session_id: telemetrySessionId(),
          run_id: state.latest.discovery || null,
          acq_run_id: state.latest.acquisition || null,
          parse_run_id: state.latest.parse || null,
          value_preview: valuePreview,
          timestamp_ms: Date.now(),
        },
      ],
    };
    fetch("/v1/hmi/events", {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => {
      // fire-and-forget: telemetry failure must not block UI
    });
  }

  function emitDebouncedInput(target) {
    const controlId = controlIdFromTarget(target);
    const prev = state.telemetry.inputTimers.get(controlId);
    if (prev) clearTimeout(prev);
    const timer = setTimeout(() => {
      state.telemetry.inputTimers.delete(controlId);
      emitEvent("input", target);
    }, telemetryInputDebounceMs);
    state.telemetry.inputTimers.set(controlId, timer);
  }

  function init() {
    telemetrySessionId();
    document.addEventListener(
      "click",
      (event) => {
        const target = event.target instanceof HTMLElement ? event.target.closest("button, a, summary") : null;
        if (!target) return;
        emitEvent("click", target);
      },
      true,
    );
    document.addEventListener(
      "change",
      (event) => {
        const target = event.target;
        if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement)) return;
        emitEvent("change", target);
      },
      true,
    );
    document.addEventListener(
      "input",
      (event) => {
        const target = event.target;
        if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) return;
        emitDebouncedInput(target);
      },
      true,
    );
    document.addEventListener(
      "submit",
      (event) => {
        const target = event.target;
        if (!(target instanceof HTMLFormElement)) return;
        emitEvent("submit", target);
      },
      true,
    );
    window.addEventListener("hashchange", () => emitEvent("navigate", document.body, activeSection()));
  }

  return {
    emitEvent,
    init,
    ensureSession: telemetrySessionId,
  };
}

