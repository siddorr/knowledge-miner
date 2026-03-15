from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from knowledge_miner.config import settings
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
    assert 'id="discoverSessionName"' in body
    assert "Stop Running Task" in body
    assert "Library Export" in body
    assert 'id="reviewHeading"' in body
    assert "Download missing" in body
    assert "Run Next Citation Iteration" in body
    assert "Resume Citation Expansion" in body
    assert "Generate Suggestions" in body
    assert "Suggested Queries" in body
    assert "Selected Queries" in body
    assert "Selected queries: 0" in body
    assert 'id="discoverQueryInput"' in body
    assert 'placeholder="Add a search query"' in body
    assert "Executed Queries for Active Run" in body
    assert "Provider Limits" in body
    assert 'id="discoverOpenalexLimitInput"' in body
    assert 'id="discoverSemanticScholarLimitInput"' in body
    assert 'id="discoverBraveLimitInput"' in body
    assert 'data-review-filter="pending"' in body
    assert 'data-review-filter="accepted"' in body
    assert 'data-review-filter="rejected"' in body
    assert 'data-review-filter="later"' in body
    assert 'data-review-filter="all"' in body
    assert 'data-review-filter="latest_auto_approved"' in body
    assert 'data-review-filter="latest_auto_rejected"' in body
    assert 'data-review-sort="iteration"' in body
    assert 'data-review-sort="year"' in body
    assert 'data-review-sort="citation_count"' in body
    assert 'data-review-sort="relevance_score"' in body
    assert "Iter</button>" in body
    assert "Browse pending, reviewed, and latest auto-decided papers" in body
    assert "No document selected." in body
    assert "Internal PDF Repository URL" in body
    assert "Save Repository URL" in body
    assert "Download Selected" in body
    assert "Export ZIP with PDFs" in body
    assert "Advanced" in body
    assert "Operational Events" in body
    assert "Autoscroll: On" in body
    assert "<h2>Discover</h2>" not in body
    assert "<h2>Documents</h2>" not in body
    assert "<h2>Library Export</h2>" not in body
    assert "<h2>Advanced</h2>" not in body
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


def test_advanced_operational_events_route_returns_grouped_counts(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "knowledge_miner.log"
    log_path.write_text(
        "\n".join(
            [
                '2026-03-15 01:00:00 INFO [knowledge_miner] {"event":"provider_call","run_id":"run_1","iteration":1,"provider":"openalex","operation":"search","latency_ms":123.4,"ok":true}',
                '2026-03-15 01:00:01 INFO [knowledge_miner] {"event":"run_summary","run_id":"run_1","status":"completed","current_iteration":1,"counters":{"fetched":10}}',
                '2026-03-15 01:00:02 INFO [knowledge_miner] {"event":"acquisition_download","acq_run_id":"acq_1","source_id":"src_1","domain":"example.org","latency_ms":456.7,"status":"downloaded"}',
            ]
        ),
        encoding="utf-8",
    )
    original_log_dir = settings.log_dir
    original_log_file = settings.log_file
    try:
        object.__setattr__(settings, "log_dir", str(log_dir))
        object.__setattr__(settings, "log_file", "knowledge_miner.log")
        client = TestClient(app)
        response = client.get(
            "/v1/advanced/operational-events?limit=10",
            headers={"Authorization": "Bearer dev-token"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert Path(body["log_path"]) == log_path
        assert any(item["group"] == "provider:openalex/search" and item["count"] == 1 for item in body["grouped_counts"])
        assert any(item["group"] == "summary:run" and item["count"] == 1 for item in body["grouped_counts"])
        assert any(item["group"] == "download:example.org" and item["count"] == 1 for item in body["grouped_counts"])
    finally:
        object.__setattr__(settings, "log_dir", original_log_dir)
        object.__setattr__(settings, "log_file", original_log_file)
