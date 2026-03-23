from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

import knowledge_miner.main as main_module
from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.main import app
from knowledge_miner.models import Bookmark, DiscoveryCitationSeed, Run, SessionProfile, Source


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer dev-token"}


def _seed_bookmark_source() -> None:
    with SessionLocal() as db:
        db.add(
            SessionProfile(
                session_id="session_seeded_bookmark",
                name="Water Source Session",
                session_context="Seed session context.",
            )
        )
        run = Run(
            id="run_seeded_bookmark",
            session_id="session_seeded_bookmark",
            status="completed",
            seed_queries=["semiconductor water"],
            max_iterations=1,
            current_iteration=1,
            accepted_total=1,
            expanded_candidates_total=0,
            citation_edges_total=0,
            ai_filter_active=False,
            ai_filter_warning=None,
        )
        db.add(run)
        db.add(
            Source(
                id="doi:10.3390/su11143923",
                run_id=run.id,
                title="Aerobic Treatment of Waste Process Solutions from the Semiconductor Industry",
                year=2019,
                url="https://example.org/paper",
                doi="10.3390/su11143923",
                abstract="Pilot-scale wastewater treatment study for semiconductor process solutions.",
                journal="Sustainability",
                authors=["A. Author"],
                citation_count=42,
                type="academic",
                source="openalex",
                source_native_id="W123",
                patent_office=None,
                patent_number=None,
                iteration=1,
                discovery_method="seed_search",
                relevance_score=4.8,
                accepted=True,
                review_status="human_accept",
                final_decision="human_accept",
                decision_source="human_review",
                heuristic_recommendation="accept",
                heuristic_score=4.8,
                ai_decision=None,
                ai_confidence=None,
                parent_source_id=None,
                provenance_history=[],
            )
        )
        db.commit()


def test_bookmark_upsert_and_list_returns_global_metadata():
    _seed_bookmark_source()
    client = TestClient(app)

    created = client.post(
        "/v1/bookmarks",
        json={"source_id": "doi:10.3390/su11143923"},
        headers=_auth_headers(),
    )
    assert created.status_code == 200
    body = created.json()
    assert body["source_id"] == "doi:10.3390/su11143923"
    assert body["source_session_id"] == "session_seeded_bookmark"
    assert body["source_session_name"] == "Water Source Session"
    assert body["doi_url"] == "https://doi.org/10.3390/su11143923"

    listed = client.get("/v1/bookmarks?limit=20", headers=_auth_headers())
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["title"].startswith("Aerobic Treatment")

    created_again = client.post(
        "/v1/bookmarks",
        json={"source_id": "doi:10.3390/su11143923"},
        headers=_auth_headers(),
    )
    assert created_again.status_code == 200
    with SessionLocal() as db:
        total = db.scalar(select(func.count()).select_from(Bookmark))
        assert total == 1


def test_delete_bookmark_removes_row():
    _seed_bookmark_source()
    client = TestClient(app)
    created = client.post(
        "/v1/bookmarks",
        json={"source_id": "doi:10.3390/su11143923"},
        headers=_auth_headers(),
    )
    bookmark_id = created.json()["id"]

    deleted = client.delete(f"/v1/bookmarks/{bookmark_id}", headers=_auth_headers())
    assert deleted.status_code == 200

    listed = client.get("/v1/bookmarks?limit=20", headers=_auth_headers())
    assert listed.status_code == 200
    assert listed.json()["items"] == []


def test_create_session_from_bookmark_creates_seeded_run(monkeypatch):
    _seed_bookmark_source()
    monkeypatch.setattr(main_module, "enqueue_bookmark_seed_run", lambda run_id: None)
    client = TestClient(app)
    created = client.post(
        "/v1/bookmarks",
        json={"source_id": "doi:10.3390/su11143923"},
        headers=_auth_headers(),
    )
    bookmark_id = created.json()["id"]

    response = client.post(f"/v1/bookmarks/{bookmark_id}/create-session", headers=_auth_headers())
    assert response.status_code == 202
    body = response.json()
    assert body["session_id"].startswith("session_")
    assert body["discovery_run_id"].startswith("run_")
    assert body["bookmarked_parent_count"] == 1

    with SessionLocal() as db:
        profile = db.get(SessionProfile, body["session_id"])
        assert profile is not None
        assert profile.name == body["session_name"]
        assert "Title: Aerobic Treatment" in (profile.session_context or "")

        run = db.get(Run, body["discovery_run_id"])
        assert run is not None
        assert run.session_id == body["session_id"]
        assert run.seed_queries == []

        seeds = db.scalars(select(DiscoveryCitationSeed).where(DiscoveryCitationSeed.run_id == run.id)).all()
        assert len(seeds) == 1
        assert seeds[0].seed_source_id == "doi:10.3390/su11143923"

        new_session_sources = db.scalars(select(Source).where(Source.run_id == run.id)).all()
        assert new_session_sources == []


def test_delete_bookmark_after_session_creation_keeps_seed_record(monkeypatch):
    _seed_bookmark_source()
    monkeypatch.setattr(main_module, "enqueue_bookmark_seed_run", lambda run_id: None)
    client = TestClient(app)
    created = client.post(
        "/v1/bookmarks",
        json={"source_id": "doi:10.3390/su11143923"},
        headers=_auth_headers(),
    )
    bookmark_id = created.json()["id"]
    branch = client.post(f"/v1/bookmarks/{bookmark_id}/create-session", headers=_auth_headers())
    run_id = branch.json()["discovery_run_id"]

    deleted = client.delete(f"/v1/bookmarks/{bookmark_id}", headers=_auth_headers())
    assert deleted.status_code == 200

    with SessionLocal() as db:
        seeds = db.scalars(select(DiscoveryCitationSeed).where(DiscoveryCitationSeed.run_id == run_id)).all()
        assert len(seeds) == 1
        assert seeds[0].origin_bookmark_id is None
