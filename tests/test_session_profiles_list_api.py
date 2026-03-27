from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.main import app
from knowledge_miner.models import Run, SessionProfile


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer dev-token"}


def test_list_session_profiles_returns_saved_sessions():
    with SessionLocal() as db:
        db.add(SessionProfile(session_id="session_alpha", name="Alpha", session_context="Alpha context"))
        db.add(SessionProfile(session_id="session_beta", name="Beta", session_context="Beta context"))
        db.commit()

    client = TestClient(app)
    response = client.get("/v1/sessions", headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    ids = {item["session_id"] for item in body["items"]}
    assert ids == {"session_alpha", "session_beta"}


def test_list_session_profiles_includes_run_backed_sessions_without_profiles():
    with SessionLocal() as db:
        db.add(
            Run(
                id="run_only_session",
                session_id="session_from_run",
                status="completed",
                seed_queries=["upw"],
                session_context="Derived from run",
                max_iterations=1,
                current_iteration=1,
                accepted_total=0,
                expanded_candidates_total=0,
                citation_edges_total=0,
                ai_filter_active=False,
                ai_filter_warning=None,
            )
        )
        db.commit()

    client = TestClient(app)
    response = client.get("/v1/sessions", headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["session_id"] == "session_from_run"
    assert body["items"][0]["session_context"] == "Derived from run"
