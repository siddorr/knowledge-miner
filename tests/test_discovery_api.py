from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

import knowledge_miner.main as main_module
from knowledge_miner.config import settings
from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.main import app
from knowledge_miner.models import Run, Source


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer dev-token"}


def _seed_run_with_sources() -> str:
    with SessionLocal() as db:
        run = Run(
            id="run_api_seed",
            status="completed",
            seed_queries=["upw", "semiconductor"],
            max_iterations=1,
            current_iteration=1,
            accepted_total=1,
            expanded_candidates_total=3,
            citation_edges_total=2,
            ai_filter_active=False,
            ai_filter_warning="AI filter disabled (USE_AI_FILTER=false); heuristic filtering only.",
        )
        db.add(run)

        db.add(
            Source(
                id="src_acc",
                run_id=run.id,
                title="Accepted source",
                year=2024,
                url="https://example.org/a",
                doi=None,
                abstract="a",
                type="academic",
                source="openalex",
                source_native_id="A",
                patent_office=None,
                patent_number=None,
                iteration=1,
                discovery_method="seed_search",
                relevance_score=5.0,
                accepted=True,
                review_status="auto_accept",
                ai_decision=None,
                ai_confidence=None,
                parent_source_id=None,
                provenance_history=[],
            )
        )
        db.add(
            Source(
                id="src_rej",
                run_id=run.id,
                title="Rejected source",
                year=2024,
                url="https://example.org/r",
                doi=None,
                abstract="r",
                type="academic",
                source="openalex",
                source_native_id="R",
                patent_office=None,
                patent_number=None,
                iteration=1,
                discovery_method="seed_search",
                relevance_score=1.5,
                accepted=False,
                review_status="auto_reject",
                ai_decision=None,
                ai_confidence=None,
                parent_source_id=None,
                provenance_history=[],
            )
        )
        db.add(
            Source(
                id="src_review",
                run_id=run.id,
                title="Review source",
                year=2024,
                url="https://example.org/n",
                doi=None,
                abstract="n",
                type="academic",
                source="openalex",
                source_native_id="N",
                patent_office=None,
                patent_number=None,
                iteration=1,
                discovery_method="seed_search",
                relevance_score=3.9,
                accepted=False,
                review_status="needs_review",
                ai_decision="needs_review",
                ai_confidence=0.92,
                parent_source_id=None,
                provenance_history=[],
            )
        )
        db.commit()
    return "run_api_seed"


def test_discovery_run_status_includes_seed_queries():
    run_id = _seed_run_with_sources()
    client = TestClient(app)

    response = client.get(f"/v1/discovery/runs/{run_id}", headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert body["seed_queries"] == ["upw", "semiconductor"]
    assert "ai_filter_effective_enabled" in body
    assert body["ai_filter_config_source"] == "run"
    assert body["current_stage"] == "discovery"
    assert body["stage_status"] in {"completed", "waiting_user"}
    assert body["completed"] >= 0
    assert body["total"] >= 1
    assert 0 <= body["percent"] <= 100
    assert body["message"]
    assert body["started_at"] is not None
    assert body["updated_at"] is not None


def test_create_discovery_run_forces_single_iteration_contract(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_run", lambda run_id: None)
    client = TestClient(app)
    response = client.post(
        "/v1/discovery/runs",
        json={"seed_queries": ["upw"], "max_iterations": 6},
        headers=_auth_headers(),
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    with SessionLocal() as db:
        run = db.get(Run, run_id)
        assert run is not None
        assert run.max_iterations == 1


def test_citation_iteration_runs_only_when_explicitly_requested(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_run", lambda run_id: None)
    run_id = _seed_run_with_sources()
    client = TestClient(app)

    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(Run)) or 0
    assert before == 1

    create_next = client.post(
        f"/v1/discovery/runs/{run_id}/next-citation-iteration",
        json={},
        headers=_auth_headers(),
    )
    assert create_next.status_code == 202
    next_run_id = create_next.json()["run_id"]
    assert next_run_id != run_id

    with SessionLocal() as db:
        after = db.scalar(select(func.count()).select_from(Run)) or 0
        next_run = db.get(Run, next_run_id)
        assert next_run is not None
        assert next_run.max_iterations == 1
        assert next_run.seed_queries
    assert after == 2


def test_discovery_sources_status_filter():
    run_id = _seed_run_with_sources()
    client = TestClient(app)

    accepted = client.get(f"/v1/discovery/runs/{run_id}/sources?status=accepted", headers=_auth_headers())
    rejected = client.get(f"/v1/discovery/runs/{run_id}/sources?status=rejected", headers=_auth_headers())
    review = client.get(f"/v1/discovery/runs/{run_id}/sources?status=needs_review", headers=_auth_headers())
    all_rows = client.get(f"/v1/discovery/runs/{run_id}/sources?status=all", headers=_auth_headers())
    invalid = client.get(f"/v1/discovery/runs/{run_id}/sources?status=bad", headers=_auth_headers())

    assert accepted.status_code == 200
    assert accepted.json()["total"] == 1
    assert accepted.json()["items"][0]["id"] == "src_acc"

    assert rejected.status_code == 200
    assert rejected.json()["total"] == 1
    assert rejected.json()["items"][0]["id"] == "src_rej"

    assert review.status_code == 200
    assert review.json()["total"] == 1
    assert review.json()["items"][0]["id"] == "src_review"

    assert all_rows.status_code == 200
    assert all_rows.json()["total"] == 3

    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "invalid_request"


def test_source_review_supports_slash_source_id():
    with SessionLocal() as db:
        run = Run(
            id="run_slash_review",
            status="completed",
            seed_queries=["upw"],
            max_iterations=1,
            current_iteration=1,
            accepted_total=0,
            expanded_candidates_total=0,
            citation_edges_total=0,
            ai_filter_active=False,
            ai_filter_warning="AI filter disabled (USE_AI_FILTER=false); heuristic filtering only.",
        )
        db.add(run)
        db.add(
            Source(
                id="doi:10.1000/upw/review-test",
                run_id=run.id,
                title="Slash DOI source",
                year=2024,
                url="https://example.org/slash-doi",
                doi="10.1000/upw/review-test",
                abstract="test",
                type="academic",
                source="openalex",
                source_native_id="slash1",
                patent_office=None,
                patent_number=None,
                iteration=1,
                discovery_method="seed_search",
                relevance_score=3.2,
                accepted=False,
                review_status="needs_review",
                ai_decision=None,
                ai_confidence=None,
                parent_source_id=None,
                provenance_history=[],
            )
        )
        db.commit()

    client = TestClient(app)
    source_id = "doi:10.1000/upw/review-test"
    response = client.post(
        f"/v1/sources/{source_id}/review",
        json={"decision": "accept", "run_id": "run_slash_review"},
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    assert response.json()["source_id"] == source_id
    assert response.json()["accepted"] is True

    # Simulate reload/new process boundary with a fresh client session.
    second_client = TestClient(app)
    second = second_client.post(
        f"/v1/sources/{source_id}/review",
        json={"decision": "reject", "run_id": "run_slash_review"},
        headers=_auth_headers(),
    )
    assert second.status_code == 200
    assert second.json()["source_id"] == source_id
    assert second.json()["accepted"] is False


def test_source_review_not_found_returns_context_hint():
    client = TestClient(app)
    response = client.post(
        "/v1/sources/src_missing/review",
        json={"decision": "accept", "run_id": "run_missing"},
        headers=_auth_headers(),
    )
    assert response.status_code == 404
    assert "source_not_found" in response.json()["detail"]
    assert "reload_review_queue_or_check_discovery_run_context" in response.json()["detail"]


def test_source_review_later_is_persistent_and_filterable():
    run_id = _seed_run_with_sources()
    source_id = "src_review"
    client = TestClient(app)
    move = client.post(f"/v1/sources/{source_id}/review", json={"decision": "later", "run_id": run_id}, headers=_auth_headers())
    assert move.status_code == 200
    later = client.get(f"/v1/discovery/runs/{run_id}/sources?status=later", headers=_auth_headers())
    assert later.status_code == 200
    ids = [row["id"] for row in later.json()["items"]]
    assert source_id in ids


def test_run_and_source_endpoints_do_not_flap_in_repeated_reads():
    run_id = _seed_run_with_sources()
    source_id = "src_review"
    for _ in range(10):
        client = TestClient(app)
        run_resp = client.get(f"/v1/discovery/runs/{run_id}", headers=_auth_headers())
        assert run_resp.status_code == 200
        list_resp = client.get(
            f"/v1/discovery/runs/{run_id}/sources?status=needs_review&limit=10&offset=0",
            headers=_auth_headers(),
        )
        assert list_resp.status_code == 200
        review_resp = client.post(
            f"/v1/sources/{source_id}/review",
            json={"decision": "later", "run_id": run_id},
            headers=_auth_headers(),
        )
        assert review_resp.status_code == 200
        restore_resp = client.post(
            f"/v1/sources/{source_id}/review",
            json={"decision": "accept", "run_id": run_id},
            headers=_auth_headers(),
        )
        assert restore_resp.status_code == 200


def test_source_review_rejects_run_context_mismatch():
    run_id = _seed_run_with_sources()
    client = TestClient(app)
    response = client.post(
        "/v1/sources/src_review/review",
        json={"decision": "accept", "run_id": f"{run_id}_other"},
        headers=_auth_headers(),
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "run_context_mismatch"


def test_ai_settings_update_applies_to_new_runs(monkeypatch):
    original_use_ai = settings.use_ai_filter
    original_key = settings.ai_api_key
    original_model = settings.ai_model
    original_base = settings.ai_base_url
    try:
        monkeypatch.setattr(main_module, "enqueue_run", lambda run_id: None)
        client = TestClient(app)
        update = client.post(
            "/v1/settings/ai-filter",
            json={
                "use_ai_filter": True,
                "ai_api_key": "test-ai-key-1234",
                "ai_model": "gpt-4o-mini",
                "ai_base_url": "https://api.openai.com/v1",
            },
            headers=_auth_headers(),
        )
        assert update.status_code == 200
        body = update.json()
        assert body["use_ai_filter"] is True
        assert body["has_api_key"] is True

        created = client.post(
            "/v1/discovery/runs",
            json={"seed_queries": ["upw"], "max_iterations": 1},
            headers=_auth_headers(),
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]
        status_resp = client.get(f"/v1/discovery/runs/{run_id}", headers=_auth_headers())
        assert status_resp.status_code == 200
        assert status_resp.json()["ai_filter_active"] is True
        assert status_resp.json()["ai_filter_warning"] is None

        update_off = client.post(
            "/v1/settings/ai-filter",
            json={"use_ai_filter": False},
            headers=_auth_headers(),
        )
        assert update_off.status_code == 200
        assert update_off.json()["use_ai_filter"] is False
    finally:
        object.__setattr__(settings, "use_ai_filter", original_use_ai)
        object.__setattr__(settings, "ai_api_key", original_key)
        object.__setattr__(settings, "ai_model", original_model)
        object.__setattr__(settings, "ai_base_url", original_base)
