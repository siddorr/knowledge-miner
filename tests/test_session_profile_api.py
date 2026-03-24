from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.db import Base, engine
from knowledge_miner.main import app


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer dev-token"}


def test_session_profile_upsert_and_get():
    client = TestClient(app)
    upsert = client.put(
        "/v1/sessions/session_api_1",
        json={
            "name": "Plant design",
            "session_context": "UPW plant design context for semiconductor wafer fabs.",
        },
        headers=_auth_headers(),
    )
    assert upsert.status_code == 200
    body = upsert.json()
    assert body["session_id"] == "session_api_1"
    assert body["name"] == "Plant design"
    assert body["session_context"].startswith("UPW plant design context")
    assert body["updated_at"] is not None

    fetched = client.get("/v1/sessions/session_api_1", headers=_auth_headers())
    assert fetched.status_code == 200
    fetched_body = fetched.json()
    assert fetched_body["session_id"] == "session_api_1"
    assert fetched_body["session_context"] == body["session_context"]


def test_session_profile_allows_empty_context():
    client = TestClient(app)
    response = client.put(
        "/v1/sessions/session_api_empty",
        json={"name": "Empty context", "session_context": "   "},
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "session_api_empty"
    assert body["name"] == "Empty context"
    assert body["session_context"] == ""
    assert body["updated_at"] is not None
