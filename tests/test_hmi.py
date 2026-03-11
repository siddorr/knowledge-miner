from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.main import app
from knowledge_miner.config import settings


def test_hmi_shell_route_and_navigation():
    client = TestClient(app)
    response = client.get("/hmi")
    assert response.status_code == 200
    body = response.text
    assert "Knowledge Miner Ops Dashboard" in body
    assert "Runs" in body
    assert "Discovery" in body
    assert "Acquisition" in body
    assert "Parse" in body
    assert "Search" in body
    assert "Manual Recovery" in body
    assert "Runs Dashboard" in body
    assert "/hmi/static/hmi.js" in body
    assert "Create New Session" in body
    assert "postCreateGuidance" in body
    assert "Start Acquisition" in body
    assert "Start Parse" in body
    assert "Abstract" in body
    assert "Actions" in body
    assert "Latest IDs:" in body
    assert "Copy ID" in body
    assert "__KM_HMI_DEFAULT_TOKEN__" in body
    assert "Load Queue" in body
    assert "Export CSV" in body
    assert "Register Manual Upload" in body
    assert "AI Filter Settings" in body
    assert "Load AI Settings" in body
    assert "Save AI Settings" in body
    assert "AI Filter" in body
    assert "Parse Run ID" in body
    assert "Selected Document Detail" in body
    assert "Selected Document Full Text" in body
    assert "Parsed Document Detail" in body
    assert "Parsed Document Full Text" in body
    assert "Related Source Context" in body


def test_hmi_static_css_served():
    client = TestClient(app)
    response = client.get("/hmi/static/hmi.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")


def test_hmi_static_js_served():
    client = TestClient(app)
    response = client.get("/hmi/static/hmi.js")
    assert response.status_code == 200
    body = response.text
    assert "Stale data in #" in body
    assert "pollState" in body
    assert "Load failed:" in body
    assert "discovery-review-action" in body
    assert "toggle-abstract" in body
    assert "Approve" in body
    assert "Reject" in body
    assert "Use Context" in body
    assert "acq-row-action" in body
    assert "parse-doc-action" in body
    assert "manual-row-action" in body
    assert "Manual Recovery" in body
    assert "Prefill Upload" in body
    assert "syncDiscoveryRunInputs" in body
    assert "updatePostCreateGuidance" in body
    assert "Run created:" in body
    assert "Switch filter to all or needs_review." in body
    assert "loadAiSettings" in body
    assert "saveAiSettings" in body


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
