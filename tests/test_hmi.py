from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.main import app


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


def test_hmi_static_css_served():
    client = TestClient(app)
    response = client.get("/hmi/static/hmi.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")


def test_hmi_static_js_served():
    client = TestClient(app)
    response = client.get("/hmi/static/hmi.js")
    assert response.status_code == 200
