from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.main import app


def _body() -> str:
    client = TestClient(app)
    response = client.get("/hmi")
    assert response.status_code == 200
    return response.text


def _section(body: str, section_id: str, next_section_id: str | None = None) -> str:
    start = body.index(f'<section id="{section_id}"')
    end = body.index(f'<section id="{next_section_id}"') if next_section_id else len(body)
    return body[start:end]


def test_advanced_contains_technical_controls_and_ids():
    body = _body()
    advanced = _section(body, "advanced")
    for text in (
        "Technical operations, IDs, logs, and diagnostics.",
        "API Key",
        "AI Filter Settings",
        "Latest IDs:",
        "Run lookup and stage controls",
        "Global Search",
    ):
        assert text in advanced


def test_primary_workflow_sections_do_not_require_manual_run_ids():
    body = _body()
    review = _section(body, "review", "documents")
    documents = _section(body, "documents", "library")
    library = _section(body, "library", "advanced")
    for section in (review, documents, library):
        assert "Discovery Run ID" not in section
        assert "Acquisition Run ID" not in section
        assert "Parse Run ID" not in section
