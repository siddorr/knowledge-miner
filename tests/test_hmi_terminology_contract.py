from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.main import app


def _body() -> str:
    client = TestClient(app)
    response = client.get("/hmi")
    assert response.status_code == 200
    return response.text


def test_primary_hmi_uses_session_language():
    body = _body()
    assert "New Session" in body
    assert "Save Session" in body
    assert "Load Session" in body
    assert "Active Session:" in body
    assert "Session workspace" in body
    assert "Session Contains" in body
    assert "Default Session" in body
    assert "New Topic" not in body


def test_primary_navigation_uses_library_export_label():
    body = _body()
    nav_start = body.index('id="navigationRow"')
    main_start = body.index("<main")
    nav = body[nav_start:main_start]
    assert "Library Export" in nav
    assert ">Library<" not in nav


def test_first_time_workflow_copy_uses_library_export():
    body = _body()
    assert "First-time-user flow: Discover -> Review -> Documents -> Library Export." in body
