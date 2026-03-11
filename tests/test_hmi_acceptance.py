from __future__ import annotations

import base64

from fastapi.testclient import TestClient
from sqlalchemy import select

import knowledge_miner.acquisition as acquisition_module
import knowledge_miner.main as main_module
from knowledge_miner.acquisition import DownloadResult, execute_acquisition_run
from knowledge_miner.config import settings
from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.discovery import execute_run
from knowledge_miner.models import AcquisitionRun, ParseRun, Run, Source
from knowledge_miner.parse import execute_parse_run


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer dev-token"}


def _ensure_accepted_source(run_id: str) -> None:
    with SessionLocal() as db:
        accepted_count = db.query(Source).filter(Source.run_id == run_id, Source.accepted.is_(True)).count()
        if accepted_count > 0:
            return
        db.add(
            Source(
                id="doi:10.1000/e2e-fallback",
                run_id=run_id,
                title="UPW fallback source",
                year=2024,
                url="https://example.org/upw-fallback.pdf",
                doi="10.1000/e2e-fallback",
                abstract="UPW semiconductor process control",
                type="academic",
                source="openalex",
                source_native_id="fallback",
                patent_office=None,
                patent_number=None,
                iteration=1,
                discovery_method="seed_search",
                relevance_score=6.0,
                accepted=True,
                review_status="auto_accept",
                ai_decision=None,
                ai_confidence=None,
                parent_source_id=None,
                provenance_history=[],
            )
        )
        run = db.get(Run, run_id)
        if run is not None:
            run.accepted_total = 1
        db.commit()


def test_e2e_discovery_to_search_flow(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "enqueue_run", lambda run_id: None)
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    monkeypatch.setattr(main_module, "enqueue_parse_run", lambda parse_run_id: None)
    original_artifacts_dir = settings.artifacts_dir
    object.__setattr__(settings, "artifacts_dir", str(tmp_path))

    def fake_download(url: str, *, timeout_seconds: float, max_bytes: int):  # noqa: ANN001
        del timeout_seconds, max_bytes
        return DownloadResult(
            kind="pdf",
            mime_type="application/pdf",
            content=b"%PDF-1.4 UPW semiconductor process control",
            url=url,
            error=None,
            retryable=False,
        )

    monkeypatch.setattr(acquisition_module, "_download_url", fake_download)
    client = TestClient(main_module.app)
    try:
        created = client.post(
            "/v1/discovery/runs",
            json={"seed_queries": ["ultrapure water UPW semiconductor"], "max_iterations": 1},
            headers=_auth_headers(),
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]

        with SessionLocal() as db:
            run = db.get(Run, run_id)
            assert run is not None
            execute_run(db, run)
        _ensure_accepted_source(run_id)

        acq_created = client.post(
            "/v1/acquisition/runs",
            json={"run_id": run_id, "retry_failed_only": False},
            headers=_auth_headers(),
        )
        assert acq_created.status_code == 202
        acq_run_id = acq_created.json()["acq_run_id"]

        with SessionLocal() as db:
            acq_run = db.get(AcquisitionRun, acq_run_id)
            assert acq_run is not None
            execute_acquisition_run(db, acq_run)

        parse_created = client.post(
            "/v1/parse/runs",
            json={"acq_run_id": acq_run_id, "retry_failed_only": False},
            headers=_auth_headers(),
        )
        assert parse_created.status_code == 202
        parse_run_id = parse_created.json()["parse_run_id"]

        with SessionLocal() as db:
            parse_run = db.get(ParseRun, parse_run_id)
            assert parse_run is not None
            execute_parse_run(db, parse_run)

        search = client.post(
            "/v1/search",
            json={"parse_run_id": parse_run_id, "query": "UPW", "limit": 10},
            headers=_auth_headers(),
        )
        assert search.status_code == 200
        body = search.json()
        assert body["total"] >= 1
        assert body["items"][0]["document_id"]
        assert body["items"][0]["source_id"]
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)


def test_e2e_failed_acquisition_to_manual_recovery_flow(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "enqueue_run", lambda run_id: None)
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    original_artifacts_dir = settings.artifacts_dir
    object.__setattr__(settings, "artifacts_dir", str(tmp_path))

    def fail_download(url: str, *, timeout_seconds: float, max_bytes: int):  # noqa: ANN001
        del timeout_seconds, max_bytes
        return DownloadResult(
            kind=None,
            mime_type=None,
            content=None,
            url=url,
            error="http_404",
            retryable=False,
        )

    monkeypatch.setattr(acquisition_module, "_download_url", fail_download)
    client = TestClient(main_module.app)
    try:
        created = client.post(
            "/v1/discovery/runs",
            json={"seed_queries": ["UPW fabs"], "max_iterations": 1},
            headers=_auth_headers(),
        )
        assert created.status_code == 202
        run_id = created.json()["run_id"]

        with SessionLocal() as db:
            run = db.get(Run, run_id)
            assert run is not None
            execute_run(db, run)
        _ensure_accepted_source(run_id)

        acq_created = client.post("/v1/acquisition/runs", json={"run_id": run_id}, headers=_auth_headers())
        assert acq_created.status_code == 202
        acq_run_id = acq_created.json()["acq_run_id"]

        with SessionLocal() as db:
            acq_run = db.get(AcquisitionRun, acq_run_id)
            assert acq_run is not None
            execute_acquisition_run(db, acq_run)

        queue = client.get(f"/v1/acquisition/runs/{acq_run_id}/manual-downloads", headers=_auth_headers())
        assert queue.status_code == 200
        queue_body = queue.json()
        assert queue_body["total"] >= 1
        source_id = queue_body["items"][0]["source_id"]

        csv_resp = client.get(f"/v1/acquisition/runs/{acq_run_id}/manual-downloads.csv", headers=_auth_headers())
        assert csv_resp.status_code == 200
        assert "text/csv" in csv_resp.headers.get("content-type", "")
        assert source_id in csv_resp.text

        payload = {
            "source_id": source_id,
            "filename": "manual.pdf",
            "content_base64": base64.b64encode(b"%PDF-1.4 recovered").decode("utf-8"),
            "content_type": "application/pdf",
        }
        upload = client.post(
            f"/v1/acquisition/runs/{acq_run_id}/manual-upload",
            json=payload,
            headers=_auth_headers(),
        )
        assert upload.status_code == 200

        queue_after = client.get(f"/v1/acquisition/runs/{acq_run_id}/manual-downloads", headers=_auth_headers())
        assert queue_after.status_code == 200
        remaining_source_ids = {item["source_id"] for item in queue_after.json()["items"]}
        assert source_id not in remaining_source_ids

        with SessionLocal() as db:
            run = db.get(AcquisitionRun, acq_run_id)
            assert run is not None
            assert run.downloaded_total >= 1
            assert run.failed_total >= 0
            statuses = db.scalars(select(Source.id).where(Source.run_id == run_id)).all()
            assert statuses
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)
