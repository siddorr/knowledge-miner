from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.main import app
from knowledge_miner.config import settings


def test_hmi_shell_route_and_navigation():
    client = TestClient(app)
    response = client.get("/hmi")
    assert response.status_code == 200
    body = response.text
    assert "Knowledge Miner Task Dashboard" in body
    assert "Discover" in body
    assert "+ New Topic" in body
    assert "Add Sources" in body
    assert "Queries" in body
    assert "Runs" in body
    assert "Details" in body
    assert "Bulk paste (one per line)" in body
    assert "buildSourcesRows" in body
    assert "Copy" in body
    assert "Review" in body
    assert "Pending" in body
    assert "Later" in body
    assert "Accept Selected" in body
    assert "Reject Selected" in body
    assert "Documents Queue" in body
    assert "Article Details" in body
    assert "Copy Error" in body
    assert "Copy Runs Error" in body
    assert "Copy Parse Error" in body
    assert "Documents" in body
    assert "Retry Failed" in body
    assert "Copy Selected DOI/URL" in body
    assert "documentsDetails" in body
    assert "Library" in body
    assert "Unified browser + search. Leave query empty to browse corpus." in body
    assert "Load Library" in body
    assert "Topic Contains" in body
    assert "Parsed Decision" in body
    assert "searchPreview" in body
    assert "Advanced" in body
    assert "reviewNavBadge" in body
    assert "documentsNavBadge" in body
    assert "reviewPaginationRow" in body
    assert "documentsPaginationRow" in body
    assert "UPW Knowledge Miner" in body
    assert "inProgressBanner" in body
    assert "inProgressState" in body
    assert "liveUpdatesState" in body
    assert "Pending Tasks" in body
    assert "Review Sources:" in body
    assert "Resolve Errors:" in body
    assert "statusNextActionBtn" in body
    assert "statusActiveDiscoveryRun" in body
    assert "useLatestRunBtn" in body
    assert "/hmi/static/hmi.js?v=" in body
    assert "/hmi/static/hmi.css?v=" in body
    assert "Run One Iteration" in body
    assert "Run Next Citation Iteration" in body
    assert "Search New Keywords" in body
    assert "Mode" in body
    assert "Fast review" in body
    assert "Refresh Review Queue" in body
    assert "reviewRunChooser" in body
    assert "reviewRunChooserSelect" in body
    assert "reviewRunChooserUseBtn" in body
    assert "Start Acquisition" in body
    assert "Start Parse" in body
    assert "Score" in body
    assert "Status" in body
    assert "Latest IDs:" in body
    assert "Copy ID" in body
    assert "Context:" in body
    assert "__KM_HMI_DEFAULT_TOKEN__" in body
    assert "__KM_HMI_LAUNCH_SECTION__" in body
    assert "Max iterations" not in body
    assert "View Issues" in body
    assert "Upload PDF Batch" in body
    assert "statusProgressBar" in body
    assert "freshnessState" in body
    assert "Export CSV" in body
    assert "Upload PDF" in body
    assert "AI Filter Settings" in body
    assert "Load AI Settings" in body
    assert "Save AI Settings" in body
    assert "Session State" in body
    assert "Save Session" in body
    assert "Load Session" in body
    assert "Auto-restore latest" in body
    assert "AI Filter" in body
    assert "Global Search" in body
    assert "Parse Run ID Override" not in body
    assert "Parsed Document Detail" in body
    assert "Parsed Document Full Text" in body
    assert "Related Source Context" in body


def test_hmi_static_css_served():
    client = TestClient(app)
    response = client.get("/hmi/static/hmi.css")
    assert response.status_code == 200
    body = response.text
    assert "text/css" in response.headers.get("content-type", "")
    assert ".status-badge" in body
    assert ".status-ready" in body
    assert ".status-alert" in body


def test_hmi_static_js_served():
    client = TestClient(app)
    response = client.get("/hmi/static/hmi.js")
    assert response.status_code == 200
    body = response.text
    assert "Stale data in #" in body
    assert "pollState" in body
    assert "Load failed:" in body
    assert "review-action" in body
    assert "documents-action" in body
    assert "Accept" in body
    assert "Reject" in body
    assert "updateSectionVisibility" in body
    assert "statusBadge" in body
    assert "IDs are available in Advanced." in body
    assert "loadAiSettings" in body
    assert "saveAiSettings" in body
    assert "runGlobalSearch" in body
    assert "loadLibraryBrowser" in body
    assert "libraryDocPassesFilters" in body
    assert "libraryFilterForm" in body
    assert "loadDashboard" in body
    assert "reviewNavBadge" in body
    assert "documentsNavBadge" in body
    assert "/v1/work-queue" in body
    assert "/v1/system/status" in body
    assert "/v1/search/global" in body
    assert "./hmi/telemetry.js" in body
    assert "emitTelemetryEvent" in body
    assert "telemetry.init()" in body
    assert "initTelemetry" in body
    assert "LAUNCH_SECTION" in body
    assert "updateStatusStrip" in body
    assert "renderBuildTopics" in body
    assert "setBuildTab" in body
    assert "handleAddSource" in body
    assert "handleBuildQuery" in body
    assert "handleBulkSource" in body
    assert "sourceFingerprint" in body
    assert "handleCopyValueClick" in body
    assert "copyFeedbackIdForTarget" in body
    assert "applyReviewDecisionToSelected" in body
    assert "runBusy" in body
    assert "setGlobalBusy" in body
    assert "resetStaleRunContext" in body
    assert "useLatestRunContext" in body
    assert "Context unchanged until you switch explicitly." in body
    assert "stale_context_reset" in body
    assert "documentsAcquirePending" in body
    assert "documentsRetryFailed" in body
    assert "documentsCopySelected" in body
    assert "paginationState" in body
    assert "applyPaginationControls" in body
    assert "Auth: No" in body
    assert "Auth: Yes" in body
    assert "openLiveUpdatesChannel" in body
    assert "Live updates connected. Idle mode: interval polling paused." in body
    assert "scheduleReviewAutoLoad" in body
    assert "Leader tab mode" in body
    assert "Follower tab mode" in body
    assert "Hidden tab: periodic refresh paused." in body
    assert "./hmi/state.js" in body
    assert "BroadcastChannel" in body
    assert "captureSessionState" in body
    assert "loadSelectedSession" in body
    assert "review_autoload:resolved_run" in body
    assert "review_autoload:no_run_context" in body
    assert "review_autoload:multiple_runs" in body


def test_hmi_static_telemetry_module_served():
    client = TestClient(app)
    response = client.get("/hmi/static/hmi/telemetry.js")
    assert response.status_code == 200
    body = response.text
    assert "/v1/hmi/events" in body
    assert "emitDebouncedInput" in body
    assert "createTelemetryClient" in body


def test_hmi_prefills_system_token_when_configured():
    original = settings.hmi_api_token
    original_auth = settings.auth_enabled
    object.__setattr__(settings, "hmi_api_token", "sys-token-123")
    object.__setattr__(settings, "auth_enabled", True)
    try:
        client = TestClient(app)
        response = client.get("/hmi")
        assert response.status_code == 200
        body = response.text
        assert 'window.__KM_HMI_DEFAULT_TOKEN__ = "sys-token-123";' in body
    finally:
        object.__setattr__(settings, "hmi_api_token", original)
        object.__setattr__(settings, "auth_enabled", original_auth)


def test_hmi_auth_disabled_flag_is_injected():
    original_auth = settings.auth_enabled
    object.__setattr__(settings, "auth_enabled", False)
    try:
        client = TestClient(app)
        response = client.get("/hmi")
        assert response.status_code == 200
        assert "window.__KM_HMI_AUTH_ENABLED__ = false;" in response.text
    finally:
        object.__setattr__(settings, "auth_enabled", original_auth)
