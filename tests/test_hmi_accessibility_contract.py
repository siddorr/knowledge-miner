from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.main import app


def _body() -> str:
    client = TestClient(app)
    response = client.get("/hmi")
    assert response.status_code == 200
    return response.text


def test_status_information_is_textual_not_only_visual():
    body = _body()
    for text in (
        "Work in progress:",
        "Operational Status",
        "System readiness:",
        "AI readiness:",
        "DB readiness:",
        "Live updates:",
        "Last update:",
    ):
        assert text in body


def test_shell_dom_order_is_stable_for_responsive_layouts():
    body = _body()
    header_index = body.index('<header class="topbar">')
    controls_index = body.index('id="controlsRow"')
    nav_index = body.index('id="navigationRow"')
    footer_index = body.index("<footer")
    assert header_index < controls_index < nav_index < footer_index


def test_navigation_and_controls_have_explicit_labels():
    body = _body()
    assert 'aria-label="Session controls"' in body
    assert 'aria-label="Primary"' in body
