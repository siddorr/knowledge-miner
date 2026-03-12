from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from knowledge_miner.config import settings
from knowledge_miner.main import app


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer dev-token"}


def _valid_event() -> dict:
    return {
        "event_type": "click",
        "control_id": "createSessionBtn",
        "control_label": "Run Discovery",
        "page": "dashboard",
        "section": "dashboard",
        "session_id": "hmi_test_1",
        "run_id": "run_1",
        "acq_run_id": None,
        "parse_run_id": None,
        "value_preview": "upw, semiconductor",
        "timestamp_ms": 1710000000000,
    }


def test_hmi_events_ingest_accepts_and_logs(caplog):
    client = TestClient(app)
    payload = {"events": [_valid_event()]}
    with caplog.at_level(logging.INFO, logger="knowledge_miner"):
        resp = client.post("/v1/hmi/events", json=payload, headers=_auth_headers())
    assert resp.status_code == 202
    assert resp.json()["accepted"] == 1
    assert any("hmi_event" in rec.message for rec in caplog.records)


def test_hmi_events_ingest_rejects_invalid_event_type():
    client = TestClient(app)
    bad = _valid_event()
    bad["event_type"] = "tap"
    resp = client.post("/v1/hmi/events", json={"events": [bad]}, headers=_auth_headers())
    assert resp.status_code == 422


def test_hmi_events_ingest_rejects_oversized_batch():
    client = TestClient(app)
    events = [_valid_event() for _ in range(101)]
    resp = client.post("/v1/hmi/events", json={"events": events}, headers=_auth_headers())
    assert resp.status_code == 422


def test_hmi_events_ingest_redacts_sensitive_preview_in_logs(caplog):
    client = TestClient(app)
    sensitive = _valid_event()
    sensitive["value_preview"] = "Bearer sk-secret-value"
    with caplog.at_level(logging.INFO, logger="knowledge_miner"):
        resp = client.post("/v1/hmi/events", json={"events": [sensitive]}, headers=_auth_headers())
    assert resp.status_code == 202
    messages = [rec.message for rec in caplog.records if "hmi_event" in rec.message]
    assert messages
    assert any("[redacted]" in msg for msg in messages)


def test_hmi_event_stream_emits_connected_event():
    client = TestClient(app)
    response = client.get("/v1/events/stream?once=true")
    assert response.status_code == 200
    assert "event: connected" in response.text
    assert "event: queue_updated" in response.text


def test_hmi_event_stream_requires_key_when_auth_enabled():
    client = TestClient(app)
    original_auth = settings.auth_enabled
    original_api = settings.api_token
    try:
        object.__setattr__(settings, "auth_enabled", True)
        object.__setattr__(settings, "api_token", "dev-token")
        unauthorized = client.get("/v1/events/stream")
        assert unauthorized.status_code == 401
        authorized = client.get("/v1/events/stream?once=true&api_key=dev-token")
        assert authorized.status_code == 200
        assert "event: connected" in authorized.text
    finally:
        object.__setattr__(settings, "auth_enabled", original_auth)
        object.__setattr__(settings, "api_token", original_api)
