from __future__ import annotations

import logging

from fastapi.testclient import TestClient

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
