from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.db import Base, engine
from knowledge_miner.main import app


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_hmi2_shell_route_and_navigation():
    client = TestClient(app)
    response = client.get("/hmi2")
    assert response.status_code == 200
    body = response.text
    assert "Knowledge Miner" in body
    assert "New Session" in body
    assert "Library Export" in body
    assert "Review Sources - Pending: 0" in body
    assert "Download missing" in body
    assert "Run Next Citation Iteration" in body
    assert "Selected queries: 0" in body
    assert "Run Query Status" in body
    assert "Pending</option>" in body
    assert "Accepted</option>" in body
    assert "Rejected</option>" in body
    assert "Later</option>" in body
    assert "Browse papers by review state" in body
    assert "No document selected." in body
    assert "Internal PDF Repository URL" in body
    assert "Save Repository URL" in body
    assert "Download Selected" in body
    assert "Export ZIP with PDFs" in body
    assert "Advanced" in body
    assert "/hmi2/static/gui.js?v=" in body
    assert "/hmi2/static/gui.css?v=" in body


def test_hmi2_static_assets_served():
    client = TestClient(app)
    css = client.get("/hmi2/static/gui.css")
    js = client.get("/hmi2/static/gui.js")
    assert css.status_code == 200
    assert js.status_code == 200
    assert "text/css" in css.headers.get("content-type", "")
    assert "exportLibraryZip" in js.text
