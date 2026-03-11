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
