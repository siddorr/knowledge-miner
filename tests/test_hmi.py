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
    assert "Build" in body
    assert "Review" in body
    assert "Documents" in body
    assert "Library" in body
    assert "Advanced" in body
    assert "reviewNavBadge" in body
    assert "documentsNavBadge" in body
    assert "UPW Knowledge Miner" in body
    assert "Pending review:" in body
    assert "Accepted waiting docs:" in body
    assert "Document failures:" in body
    assert "statusNextActionBtn" in body
    assert "/hmi/static/hmi.js" in body
    assert "Run Discovery" in body
    assert "Load Review Queue" in body
    assert "Start Acquisition" in body
    assert "Start Parse" in body
    assert "Abstract" in body
    assert "Actions" in body
    assert "Latest IDs:" in body
    assert "Copy ID" in body
    assert "Context:" in body
    assert "__KM_HMI_DEFAULT_TOKEN__" in body
    assert "__KM_HMI_LAUNCH_SECTION__" in body
    assert "Load Download Issues" in body
    assert "Export CSV" in body
    assert "Upload PDF" in body
    assert "AI Filter Settings" in body
    assert "Load AI Settings" in body
    assert "Save AI Settings" in body
    assert "AI Filter" in body
    assert "Global Search" in body
    assert "Parse Run ID Override" in body
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
    assert "Run created:" in body
    assert "loadAiSettings" in body
    assert "saveAiSettings" in body
    assert "runGlobalSearch" in body
    assert "loadDashboard" in body
    assert "reviewNavBadge" in body
    assert "documentsNavBadge" in body
    assert "/v1/work-queue" in body
    assert "/v1/system/status" in body
    assert "/v1/search/global" in body
    assert "/v1/hmi/events" in body
    assert "emitTelemetryEvent" in body
    assert "emitDebouncedInputTelemetry" in body
    assert "initTelemetry" in body
    assert "LAUNCH_SECTION" in body
    assert "updateStatusStrip" in body


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
