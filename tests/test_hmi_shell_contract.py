from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.main import app


def _body() -> str:
    client = TestClient(app)
    response = client.get("/hmi")
    assert response.status_code == 200
    return response.text


def _assert_in_order(body: str, parts: list[str]) -> None:
    start = 0
    for part in parts:
        index = body.find(part, start)
        assert index != -1, f"missing or out of order: {part}"
        start = index + len(part)


def test_hmi_has_separate_header_controls_nav_workspace_footer_sections():
    body = _body()
    _assert_in_order(
        body,
        [
            '<header class="topbar">',
            'id="controlsRow"',
            'id="navigationRow"',
            "<main",
            "<footer",
        ],
    )


def test_controls_row_contains_canonical_session_actions():
    body = _body()
    controls_start = body.index('id="controlsRow"')
    nav_start = body.index('id="navigationRow"')
    controls = body[controls_start:nav_start]
    _assert_in_order(
        controls,
        [
            'id="topNewSessionBtn"',
            'id="topSaveSessionBtn"',
            'id="topLoadSessionBtn"',
            'id="topDeleteSessionBtn"',
        ],
    )
    for label in ("New Session", "Save", "Load", "Delete"):
        assert label in controls


def test_navigation_row_matches_new_stage_order():
    body = _body()
    nav_start = body.index('id="navigationRow"')
    main_start = body.index("<main")
    nav = body[nav_start:main_start]
    _assert_in_order(
        nav,
        [
            "#build",
            "Discover",
            "#review",
            "Review",
            "#documents",
            "Documents",
            "#library",
            "Library Export",
            "#advanced",
            "Advanced",
        ],
    )


def test_footer_exposes_operational_status_contract():
    body = _body()
    footer_start = body.index("<footer")
    footer = body[footer_start:]
    for field in (
        'id="footerSystemReady"',
        'id="footerAiReady"',
        'id="footerDbReady"',
        'id="freshnessState"',
    ):
        assert field in footer
    assert "Operational Status" in footer
