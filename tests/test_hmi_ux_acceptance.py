from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.main import app
from knowledge_miner.models import Run, Source


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer dev-token"}


def _seed_needs_review_source() -> tuple[str, str]:
    with SessionLocal() as db:
        run = Run(
            id="run_ux_1",
            status="completed",
            seed_queries=["upw"],
            max_iterations=1,
            current_iteration=1,
            accepted_total=0,
            expanded_candidates_total=0,
            citation_edges_total=0,
            ai_filter_active=False,
            ai_filter_warning="AI filter disabled",
        )
        db.add(run)
        source = Source(
            id="src_ux_1",
            run_id=run.id,
            title="UX Review Source",
            year=2024,
            url="https://example.org/ux",
            doi=None,
            abstract="Needs review source",
            type="academic",
            source="openalex",
            source_native_id="ux1",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=2.5,
            accepted=False,
            review_status="needs_review",
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        db.add(source)
        db.commit()
        return run.id, source.id


def test_operator_queue_flow_without_manual_id_entry():
    run_id, source_id = _seed_needs_review_source()
    client = TestClient(app)

    queue = client.get("/v1/work-queue", headers=_auth_headers())
    assert queue.status_code == 200
    rows = queue.json()["items"]
    row = next(item for item in rows if item["source_id"] == source_id)
    assert row["phase"] == "discovery"
    assert row["status"] == "needs_review"
    assert row["context"]["discovery_run_id"] == run_id

    review = client.post(f"/v1/sources/{source_id}/review", json={"decision": "accept"}, headers=_auth_headers())
    assert review.status_code == 200
    assert review.json()["accepted"] is True

    acq = client.post("/v1/acquisition/runs", json={"run_id": run_id, "retry_failed_only": False}, headers=_auth_headers())
    assert acq.status_code == 202
    acq_run_id = acq.json()["acq_run_id"]
    items = client.get(f"/v1/acquisition/runs/{acq_run_id}/items", headers=_auth_headers())
    assert items.status_code == 200
    assert any(row["source_id"] == source_id for row in items.json()["items"])


def test_hmi_route_exposes_operator_first_shell_and_advanced_section():
    client = TestClient(app)
    response = client.get("/hmi")
    assert response.status_code == 200
    body = response.text
    assert "Build" in body
    assert "Review" in body
    assert "Documents" in body
    assert "Library" in body
    assert "Advanced" in body
    assert "Global Search" in body


def test_queue_rows_include_reason_text():
    _seed_needs_review_source()
    client = TestClient(app)
    queue = client.get("/v1/work-queue", headers=_auth_headers())
    assert queue.status_code == 200
    items = queue.json()["items"]
    assert any((item.get("reason_text") or "") for item in items)


def test_hmi_first_time_flow_labels_and_no_required_manual_ids():
    client = TestClient(app)
    response = client.get("/hmi")
    assert response.status_code == 200
    body = response.text
    assert "First-time-user flow: Build -> Review -> Documents -> Library." in body
    assert "Run One Iteration" in body
    assert "Run Next Citation Iteration" in body
    assert "Search New Keywords" in body
    assert "Load Review Queue" in body
    assert "View Issues" in body
    assert "Library" in body
    assert "Technical details" in body
    assert "Max iterations" not in body
