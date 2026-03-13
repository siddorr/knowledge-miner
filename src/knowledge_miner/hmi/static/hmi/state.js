export const POLL_ACTIVE_MS = 5000;
export const POLL_BACKGROUND_MS = 15000;
export const POLL_DISCONNECTED_IDLE_MS = 30000;
export const TELEMETRY_INPUT_DEBOUNCE_MS = 400;
export const SESSIONS_STORAGE_KEY = "km_hmi_sessions_v1";
export const SESSIONS_AUTO_RESTORE_KEY = "km_hmi_sessions_auto_restore";
export const LEADER_STALE_MS = 6000;
export const LEADER_HEARTBEAT_MS = 2000;
export const LEADER_STORAGE_KEY = "km_hmi_leader";
export const BC_NAME = "km_hmi_updates";
export const SYSTEM_TOKEN = typeof window !== "undefined" ? window.__KM_HMI_DEFAULT_TOKEN__ || null : null;
export const AUTH_ENABLED = typeof window !== "undefined" ? window.__KM_HMI_AUTH_ENABLED__ !== false : true;
export const LAUNCH_SECTION = typeof window !== "undefined" ? window.__KM_HMI_LAUNCH_SECTION__ || "build" : "build";

export function createInitialState() {
  return {
    apiKey: "",
    tokenSource: "none",
    pollTimer: null,
    runRows: [],
    latest: { discovery: "", acquisition: "", parse: "" },
    build: {
      topics: [{ id: "topic_default", name: "Default Topic" }],
      activeTopicId: "topic_default",
      activeTab: "runs",
      stagedSourcesByTopic: { topic_default: [] },
      sourceKeysByTopic: { topic_default: new Set() },
      topicQueriesByTopic: { topic_default: "" },
      coverageByTopic: {
        topic_default: {
          candidates: 0,
          accepted: 0,
          pending_review: 0,
          awaiting_documents: 0,
          failed_documents: 0,
        },
      },
    },
    review: {
      offset: 0,
      total: 0,
      loaded: false,
      expanded: new Set(),
      selected: new Set(),
      items: [],
      mode: "table",
      activeIndex: 0,
      runChoices: [],
    },
    documents: {
      offset: 0,
      total: 0,
      loaded: false,
      selectedSourceId: "",
      selected: new Set(),
      items: [],
      acqRunMeta: null,
      discoveryRunId: "",
    },
    search: { loaded: false, payload: null, items: [], mode: "browse", docsById: new Map() },
    context: {},
    telemetry: {
      sessionId: "",
      inputTimers: new Map(),
    },
    statusStrip: {
      nextActionRoute: "build",
    },
    busy: {
      count: 0,
      phase: "",
      updatedAt: "",
    },
    stale: {
      lastResetKey: "",
    },
    reviewAuto: {
      timer: null,
    },
    live: {
      connected: false,
      eventSource: null,
      queuedRefresh: null,
    },
    net: {
      inflightGet: new Map(),
      etagCache: new Map(),
      requestCount: 0,
      dedupHits: 0,
      readThrottleUntil: 0,
      readBackoffMs: 0,
    },
    refresh: {
      inflight: new Map(),
      lastAt: new Map(),
    },
    multiTab: {
      tabId: `tab_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`,
      isLeader: true,
      heartbeatTimer: null,
      channel: null,
    },
    sessions: {
      items: [],
      autoRestore: true,
    },
  };
}
