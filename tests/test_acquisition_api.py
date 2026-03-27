from __future__ import annotations

import base64
from pathlib import Path
import sys
import types

from fastapi.testclient import TestClient

from knowledge_miner.acquisition import find_reusable_artifact
import knowledge_miner.main as main_module
import knowledge_miner.rate_limit as rate_limit_module
from knowledge_miner.config import settings
from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.main import app
from knowledge_miner.models import AcquisitionItem, AcquisitionRun, Artifact, Run, Source


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer dev-token"}


def _seed_discovery_run(*, completed: bool) -> tuple[str, str]:
    with SessionLocal() as db:
        run = Run(
            id="run_seed_1",
            session_id="session_seed_acq",
            status="completed" if completed else "running",
            seed_queries=["upw semiconductor"],
            max_iterations=1,
            current_iteration=1,
            accepted_total=1,
            expanded_candidates_total=0,
            citation_edges_total=0,
            ai_filter_active=False,
            ai_filter_warning="AI filter disabled (USE_AI_FILTER=false); heuristic filtering only.",
        )
        db.add(run)
        src = Source(
            id="doi:10.1000/test-acq",
            run_id=run.id,
            title="UPW control in semiconductor lines",
            year=2023,
            url="https://ieee.org/test",
            doi="10.1000/test-acq",
            abstract="UPW and semiconductor process control",
            type="academic",
            source="openalex",
            source_native_id="W1",
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
        db.add(src)
        db.commit()
        return run.id, src.id


def _seed_discovery_run_with_two_sources() -> tuple[str, str, str]:
    run_id, first_source_id = _seed_discovery_run(completed=True)
    with SessionLocal() as db:
        db.add(
            Source(
                id="doi:10.1000/test-acq-2",
                run_id=run_id,
                title="UPW second source",
                year=2023,
                url="https://example.org/second",
                doi="10.1000/test-acq-2",
                abstract="second",
                type="academic",
                source="openalex",
                source_native_id="W2",
                patent_office=None,
                patent_number=None,
                iteration=1,
                discovery_method="seed_search",
                relevance_score=6.1,
                accepted=True,
                review_status="auto_accept",
                ai_decision=None,
                ai_confidence=None,
                parent_source_id=None,
                provenance_history=[],
            )
        )
        db.commit()
    return run_id, first_source_id, "doi:10.1000/test-acq-2"


def _seed_cross_run_session_sources() -> tuple[str, str, str]:
    run_id, source_a = _seed_discovery_run(completed=True)
    with SessionLocal() as db:
        run = Run(
            id="run_seed_2",
            session_id=db.get(Run, run_id).session_id,
            status="completed",
            seed_queries=["citation"],
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
                id="doi:10.1000/test-acq-cross",
                run_id=run.id,
                title="Cross-run accepted source",
                year=2024,
                url="https://example.org/cross",
                doi="10.1000/test-acq-cross",
                abstract="cross",
                type="academic",
                source="openalex",
                source_native_id="W3",
                patent_office=None,
                patent_number=None,
                iteration=2,
                discovery_method="forward_citation",
                relevance_score=7.0,
                accepted=True,
                review_status="auto_accept",
                ai_decision=None,
                ai_confidence=None,
                parent_source_id=None,
                provenance_history=[],
            )
        )
        db.commit()
    return run_id, source_a, "doi:10.1000/test-acq-cross"


def _seed_cross_session_reusable_artifact(tmp_path: Path) -> tuple[str, str]:
    pdf_path = tmp_path / "reusable.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 reusable")
    with SessionLocal() as db:
        run_a = Run(
            id="run_reuse_a",
            session_id="session_reuse_a",
            status="completed",
            seed_queries=["upw"],
            max_iterations=1,
            current_iteration=1,
            accepted_total=1,
            expanded_candidates_total=0,
            citation_edges_total=0,
            ai_filter_active=False,
            ai_filter_warning=None,
        )
        source_a = Source(
            id="doi:10.1000/reuse-shared::run_a",
            run_id=run_a.id,
            title="Reusable paper",
            year=2024,
            url="https://example.org/reuse-shared",
            doi="10.1000/reuse-shared",
            abstract="shared",
            type="academic",
            source="openalex",
            source_native_id="reuse_a",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=7.0,
            accepted=True,
            review_status="human_accept",
            final_decision="human_accept",
            decision_source="human_review",
            heuristic_recommendation="accept",
            heuristic_score=7.0,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        acq_run_a = AcquisitionRun(
            id="acq_reuse_a",
            discovery_run_id=run_a.id,
            retry_failed_only=False,
            status="completed",
            total_sources=1,
            downloaded_total=1,
            partial_total=0,
            failed_total=0,
            skipped_total=0,
        )
        artifact = Artifact(
            id="artifact_reuse_a",
            acq_run_id=acq_run_a.id,
            source_id=source_a.id,
            item_id=None,
            kind="pdf",
            path=str(pdf_path),
            checksum_sha256="reuse-a",
            size_bytes=pdf_path.stat().st_size,
            mime_type="application/pdf",
        )
        run_b = Run(
            id="run_reuse_b",
            session_id="session_reuse_b",
            status="completed",
            seed_queries=["reuse"],
            max_iterations=1,
            current_iteration=1,
            accepted_total=1,
            expanded_candidates_total=0,
            citation_edges_total=0,
            ai_filter_active=False,
            ai_filter_warning=None,
        )
        source_b = Source(
            id="doi:10.1000/reuse-shared::run_b",
            run_id=run_b.id,
            title="Reusable paper",
            year=2024,
            url="https://example.org/reuse-shared",
            doi="10.1000/reuse-shared",
            abstract="shared",
            type="academic",
            source="openalex",
            source_native_id="reuse_b",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=7.1,
            accepted=True,
            review_status="human_accept",
            final_decision="human_accept",
            decision_source="human_review",
            heuristic_recommendation="accept",
            heuristic_score=7.1,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        db.add_all([run_a, source_a, acq_run_a, artifact, run_b, source_b])
        db.commit()
    return "run_reuse_b", "doi:10.1000/reuse-shared::run_b"


def _seed_manual_recovery_case() -> tuple[str, str]:
    run_id, source_id = _seed_discovery_run(completed=True)
    with SessionLocal() as db:
        acq_run = AcquisitionRun(
            id="acq_manual_1",
            discovery_run_id=run_id,
            retry_failed_only=False,
            status="completed",
            total_sources=1,
            downloaded_total=0,
            partial_total=0,
            failed_total=1,
            skipped_total=0,
        )
        db.add(acq_run)
        item = AcquisitionItem(
            id="acq_item_manual_1",
            acq_run_id=acq_run.id,
            source_id=source_id,
            status="failed",
            attempt_count=2,
            selected_url="https://ieee.org/test",
            last_error="http_404",
        )
        db.add(item)
        db.commit()
    return "acq_manual_1", source_id


def test_create_acquisition_run_happy_path(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    run_id, _ = _seed_discovery_run(completed=True)
    client = TestClient(app)
    resp = client.post(
        "/v1/acquisition/runs",
        json={"run_id": run_id, "retry_failed_only": False},
        headers=_auth_headers(),
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["acq_run_id"].startswith("acq_")
    assert body["status"] in {"queued", "running", "completed"}


def test_create_acquisition_run_reuses_downloaded_artifact_across_sessions(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    original_artifacts_dir = settings.artifacts_dir
    object.__setattr__(settings, "artifacts_dir", str(tmp_path))
    try:
        run_id, source_id = _seed_cross_session_reusable_artifact(tmp_path)
        client = TestClient(app)
        resp = client.post(
            "/v1/acquisition/runs",
            json={"run_id": run_id, "retry_failed_only": False},
            headers=_auth_headers(),
        )
        assert resp.status_code == 202
        acq_run_id = resp.json()["acq_run_id"]
        items = client.get(f"/v1/acquisition/runs/{acq_run_id}/items", headers=_auth_headers())
        assert items.status_code == 200
        body = items.json()
        assert body["items"][0]["source_id"] == source_id
        assert body["items"][0]["status"] == "downloaded"
        with SessionLocal() as db:
            artifacts = db.scalars(select(Artifact).where(Artifact.acq_run_id == acq_run_id, Artifact.source_id == source_id)).all()
            assert len(artifacts) == 1
            assert Path(artifacts[0].path).name == "reusable.pdf"
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)


def test_session_latest_acquisition_items_surface_global_reuse(tmp_path):
    original_artifacts_dir = settings.artifacts_dir
    object.__setattr__(settings, "artifacts_dir", str(tmp_path))
    try:
        _seed_cross_session_reusable_artifact(tmp_path)
        client = TestClient(app)
        resp = client.get("/v1/sessions/session_reuse_b/acquisition-items/latest", headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["status"] == "downloaded"
        assert body["items"][0]["source_id"] == "doi:10.1000/reuse-shared::run_b"
        assert body["items"][0]["artifact_kind"] == "pdf"
        assert body["items"][0]["artifact_quality_status"] == "pdf"
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)


def test_find_reusable_artifact_skips_invalid_html(tmp_path):
    bad_html = tmp_path / "bad.html"
    bad_html.write_text("<html><head><title>Redirecting</title></head><body>Redirecting</body></html>", encoding="utf-8")
    with SessionLocal() as db:
        run_a = Run(
            id="run_invalid_html_a",
            session_id="session_invalid_html_a",
            status="completed",
            seed_queries=["html"],
            max_iterations=1,
            current_iteration=1,
            accepted_total=1,
            expanded_candidates_total=0,
            citation_edges_total=0,
            ai_filter_active=False,
            ai_filter_warning=None,
        )
        run_b = Run(
            id="run_invalid_html_b",
            session_id="session_invalid_html_b",
            status="completed",
            seed_queries=["html"],
            max_iterations=1,
            current_iteration=1,
            accepted_total=1,
            expanded_candidates_total=0,
            citation_edges_total=0,
            ai_filter_active=False,
            ai_filter_warning=None,
        )
        source_a = Source(
            id="doi:10.1000/reuse-html-a",
            run_id=run_a.id,
            title="Reusable html paper",
            year=2024,
            url="https://example.org/reuse-html",
            doi="10.1000/reuse-html",
            abstract="shared",
            type="academic",
            source="openalex",
            source_native_id="reuse_html_a",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=7.0,
            accepted=True,
            review_status="human_accept",
            final_decision="human_accept",
            decision_source="human_review",
            heuristic_recommendation="accept",
            heuristic_score=7.0,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        source_b = Source(
            id="doi:10.1000/reuse-html-b",
            run_id=run_b.id,
            title="Reusable html paper",
            year=2024,
            url="https://example.org/reuse-html",
            doi="10.1000/reuse-html",
            abstract="shared",
            type="academic",
            source="openalex",
            source_native_id="reuse_html_b",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=7.1,
            accepted=True,
            review_status="human_accept",
            final_decision="human_accept",
            decision_source="human_review",
            heuristic_recommendation="accept",
            heuristic_score=7.1,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        acq_run = AcquisitionRun(
            id="acq_invalid_html_a",
            discovery_run_id=run_a.id,
            retry_failed_only=False,
            status="completed",
            total_sources=1,
            downloaded_total=0,
            partial_total=1,
            failed_total=0,
            skipped_total=0,
        )
        artifact = Artifact(
            id="artifact_invalid_html_a",
            acq_run_id=acq_run.id,
            source_id=source_a.id,
            item_id=None,
            kind="html",
            path=str(bad_html),
            checksum_sha256="bad-html",
            size_bytes=bad_html.stat().st_size,
            mime_type="text/html",
            quality_status="html_invalid",
            quality_reason="html_redirect_page",
        )
        db.add_all([run_a, run_b, source_a, source_b, acq_run, artifact])
        db.commit()
        assert find_reusable_artifact(db, source_b) is None


def test_create_manual_upload_context_does_not_enqueue(monkeypatch):
    called = {"n": 0}

    def fake_enqueue(_acq_run_id: str):
        called["n"] += 1

    monkeypatch.setattr(main_module, "enqueue_acquisition_run", fake_enqueue)
    run_id, source_id = _seed_discovery_run(completed=True)
    client = TestClient(app)
    resp = client.post(
        "/v1/acquisition/runs/manual-context",
        json={"run_id": run_id, "selected_source_ids": [source_id]},
        headers=_auth_headers(),
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["acq_run_id"].startswith("acq_")
    assert called["n"] == 0
    items = client.get(f"/v1/acquisition/runs/{body['acq_run_id']}/items", headers=_auth_headers())
    assert items.status_code == 200
    assert len(items.json()["items"]) == 1
    assert items.json()["items"][0]["source_id"] == source_id


def test_create_acquisition_run_supports_selected_source_ids(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    run_id, source_a, source_b = _seed_discovery_run_with_two_sources()
    client = TestClient(app)
    resp = client.post(
        "/v1/acquisition/runs",
        json={"run_id": run_id, "retry_failed_only": False, "selected_source_ids": [source_b]},
        headers=_auth_headers(),
    )
    assert resp.status_code == 202
    acq_run_id = resp.json()["acq_run_id"]
    items = client.get(f"/v1/acquisition/runs/{acq_run_id}/items", headers=_auth_headers())
    assert items.status_code == 200
    ids = [row["source_id"] for row in items.json()["items"]]
    assert source_b in ids
    assert source_a not in ids


def test_create_acquisition_run_supports_selected_source_ids_across_session_runs(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    run_id, source_a, source_cross = _seed_cross_run_session_sources()
    client = TestClient(app)
    resp = client.post(
        "/v1/acquisition/runs",
        json={"run_id": run_id, "retry_failed_only": False, "selected_source_ids": [source_cross]},
        headers=_auth_headers(),
    )
    assert resp.status_code == 202
    acq_run_id = resp.json()["acq_run_id"]
    items = client.get(f"/v1/acquisition/runs/{acq_run_id}/items", headers=_auth_headers())
    assert items.status_code == 200
    ids = [row["source_id"] for row in items.json()["items"]]
    assert source_cross in ids
    assert source_a not in ids


def test_acquisition_items_support_limit_above_1000():
    run_id, source_id = _seed_discovery_run(completed=True)
    with SessionLocal() as db:
        acq_run = AcquisitionRun(
            id="acq_large_limit",
            discovery_run_id=run_id,
            retry_failed_only=False,
            status="completed",
            total_sources=1001,
            downloaded_total=1001,
            partial_total=0,
            failed_total=0,
            skipped_total=0,
        )
        db.add(acq_run)
        for index in range(1001):
            db.add(
                AcquisitionItem(
                    id=f"acq_item_large_{index}",
                    acq_run_id=acq_run.id,
                    source_id=source_id if index == 0 else f"src_large_acq_{index}",
                    status="downloaded",
                    attempt_count=1,
                    selected_url=f"https://example.org/file/{index}.pdf",
                    last_error=None,
                )
            )
        db.commit()

    client = TestClient(app)
    response = client.get("/v1/acquisition/runs/acq_large_limit/items?limit=5000", headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1001
    assert body["limit"] == 5000
    assert len(body["items"]) == 1001


def test_create_acquisition_run_persists_internal_repository_url(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    run_id, _ = _seed_discovery_run(completed=True)
    client = TestClient(app)
    resp = client.post(
        "/v1/acquisition/runs",
        json={
            "run_id": run_id,
            "retry_failed_only": False,
            "internal_repository_base_url": "https://repo.example.org/service/",
        },
        headers=_auth_headers(),
    )
    assert resp.status_code == 202
    acq_run_id = resp.json()["acq_run_id"]
    with SessionLocal() as db:
        run = db.get(AcquisitionRun, acq_run_id)
        assert run is not None
        assert run.internal_repository_base_url == "https://repo.example.org/service"


def test_create_acquisition_run_rejects_invalid_internal_repository_url(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    run_id, _ = _seed_discovery_run(completed=True)
    client = TestClient(app)
    resp = client.post(
        "/v1/acquisition/runs",
        json={
            "run_id": run_id,
            "retry_failed_only": False,
            "internal_repository_base_url": "ftp://repo.example.org/service",
        },
        headers=_auth_headers(),
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "invalid_internal_repository_base_url"


def test_create_acquisition_run_requires_completed_discovery_run(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    run_id, _ = _seed_discovery_run(completed=False)
    client = TestClient(app)
    resp = client.post(
        "/v1/acquisition/runs",
        json={"run_id": run_id, "retry_failed_only": False},
        headers=_auth_headers(),
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "run_not_complete"


def test_stop_acquisition_run_marks_queued_run_failed(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    run_id, _ = _seed_discovery_run(completed=True)
    client = TestClient(app)
    create_resp = client.post("/v1/acquisition/runs", json={"run_id": run_id}, headers=_auth_headers())
    assert create_resp.status_code == 202
    acq_run_id = create_resp.json()["acq_run_id"]

    stop_resp = client.post(f"/v1/acquisition/runs/{acq_run_id}/stop", headers=_auth_headers())
    assert stop_resp.status_code == 200
    assert stop_resp.json()["message"] == "Acquisition run stopped."

    with SessionLocal() as db:
        run = db.get(AcquisitionRun, acq_run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error_message == "stopped_by_user"


def test_acquisition_status_items_manifest_endpoints(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    run_id, source_id = _seed_discovery_run(completed=True)
    client = TestClient(app)
    create_resp = client.post("/v1/acquisition/runs", json={"run_id": run_id}, headers=_auth_headers())
    assert create_resp.status_code == 202
    acq_run_id = create_resp.json()["acq_run_id"]

    status_resp = client.get(f"/v1/acquisition/runs/{acq_run_id}", headers=_auth_headers())
    assert status_resp.status_code == 200
    status_body = status_resp.json()
    assert status_body["acq_run_id"] == acq_run_id
    assert status_body["discovery_run_id"] == run_id
    assert status_body["current_stage"] == "acquisition"
    assert status_body["stage_status"] in {"queued", "running", "completed", "failed"}
    assert status_body["completed"] >= 0
    assert status_body["total"] >= 1
    assert 0 <= status_body["percent"] <= 100
    assert status_body["message"]
    assert status_body["started_at"] is not None
    assert status_body["updated_at"] is not None

    items_resp = client.get(f"/v1/acquisition/runs/{acq_run_id}/items", headers=_auth_headers())
    assert items_resp.status_code == 200
    items_body = items_resp.json()
    assert items_body["total"] == 1
    assert items_body["items"][0]["source_id"] == source_id

    manifest_resp = client.get(f"/v1/acquisition/runs/{acq_run_id}/manifest", headers=_auth_headers())
    assert manifest_resp.status_code == 200
    manifest = manifest_resp.json()
    assert manifest["acq_run_id"] == acq_run_id
    assert manifest["discovery_run_id"] == run_id
    assert len(manifest["items"]) == 1


def test_get_artifact_endpoint(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    run_id, source_id = _seed_discovery_run(completed=True)
    client = TestClient(app)
    create_resp = client.post("/v1/acquisition/runs", json={"run_id": run_id}, headers=_auth_headers())
    assert create_resp.status_code == 202
    acq_run_id = create_resp.json()["acq_run_id"]

    with SessionLocal() as db:
        artifact = Artifact(
            id="artifact_1",
            acq_run_id=acq_run_id,
            source_id=source_id,
            item_id=None,
            kind="html",
            path=f"acquisition/{acq_run_id}/{source_id}/source.html",
            checksum_sha256="abc",
            size_bytes=123,
            mime_type="text/html",
        )
        db.add(artifact)
        db.commit()

    resp = client.get("/v1/acquisition/artifacts/artifact_1", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["artifact_id"] == "artifact_1"
    assert body["acq_run_id"] == acq_run_id
    assert body["source_id"] == source_id


def test_get_artifact_content_endpoint_serves_pdf_inline(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    run_id, source_id = _seed_discovery_run(completed=True)
    client = TestClient(app)
    create_resp = client.post("/v1/acquisition/runs", json={"run_id": run_id}, headers=_auth_headers())
    assert create_resp.status_code == 202
    acq_run_id = create_resp.json()["acq_run_id"]

    pdf_path = tmp_path / "preview.pdf"
    pdf_bytes = b"%PDF-1.4 preview\n"
    pdf_path.write_bytes(pdf_bytes)

    with SessionLocal() as db:
        artifact = Artifact(
            id="artifact_pdf_1",
            acq_run_id=acq_run_id,
            source_id=source_id,
            item_id=None,
            kind="pdf",
            path=str(pdf_path),
            checksum_sha256="pdf123",
            size_bytes=len(pdf_bytes),
            mime_type="application/pdf",
            quality_status="pdf",
        )
        db.add(artifact)
        db.commit()

    resp = client.get("/v1/acquisition/artifacts/artifact_pdf_1/content", headers=_auth_headers())
    assert resp.status_code == 200
    assert "application/pdf" in resp.headers.get("content-type", "")
    assert "inline" in resp.headers.get("content-disposition", "")
    assert resp.content == pdf_bytes


def test_get_artifact_content_endpoint_returns_missing_file(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    run_id, source_id = _seed_discovery_run(completed=True)
    client = TestClient(app)
    create_resp = client.post("/v1/acquisition/runs", json={"run_id": run_id}, headers=_auth_headers())
    assert create_resp.status_code == 202
    acq_run_id = create_resp.json()["acq_run_id"]

    with SessionLocal() as db:
        artifact = Artifact(
            id="artifact_missing_file_1",
            acq_run_id=acq_run_id,
            source_id=source_id,
            item_id=None,
            kind="pdf",
            path="missing/nonexistent.pdf",
            checksum_sha256="missing123",
            size_bytes=0,
            mime_type="application/pdf",
            quality_status="pdf",
        )
        db.add(artifact)
        db.commit()

    resp = client.get("/v1/acquisition/artifacts/artifact_missing_file_1/content", headers=_auth_headers())
    assert resp.status_code == 404
    assert resp.json()["detail"] == "artifact_file_missing"


def test_acquisition_endpoints_allow_without_auth_when_disabled(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    run_id, _ = _seed_discovery_run(completed=True)
    client = TestClient(app)

    original_auth = settings.auth_enabled
    try:
        object.__setattr__(settings, "auth_enabled", False)
        resp = client.post("/v1/acquisition/runs", json={"run_id": run_id})
        assert resp.status_code == 202
    finally:
        object.__setattr__(settings, "auth_enabled", original_auth)


def test_acquisition_endpoints_require_auth_when_enabled(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    run_id, _ = _seed_discovery_run(completed=True)
    client = TestClient(app)
    original_auth = settings.auth_enabled
    try:
        object.__setattr__(settings, "auth_enabled", True)
        resp = client.post("/v1/acquisition/runs", json={"run_id": run_id})
        assert resp.status_code == 401
        resp = client.get("/v1/acquisition/runs/unknown")
        assert resp.status_code == 401
    finally:
        object.__setattr__(settings, "auth_enabled", original_auth)


def test_acquisition_endpoints_rate_limited_when_enabled(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    monkeypatch.setattr(rate_limit_module.rate_limiter, "check", lambda key: False)
    run_id, _ = _seed_discovery_run(completed=True)
    client = TestClient(app)
    original_auth = settings.auth_enabled
    try:
        object.__setattr__(settings, "auth_enabled", True)
        resp = client.post("/v1/acquisition/runs", json={"run_id": run_id}, headers=_auth_headers())
        assert resp.status_code == 429
        assert resp.json()["detail"] == "rate_limited"
    finally:
        object.__setattr__(settings, "auth_enabled", original_auth)


def test_acquisition_not_found_endpoints(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    client = TestClient(app)

    resp = client.get("/v1/acquisition/runs/acq_missing", headers=_auth_headers())
    assert resp.status_code == 404
    assert resp.json()["detail"] == "run_not_found"

    resp = client.get("/v1/acquisition/runs/acq_missing/items", headers=_auth_headers())
    assert resp.status_code == 404
    assert resp.json()["detail"] == "run_not_found"

    resp = client.get("/v1/acquisition/runs/acq_missing/manifest", headers=_auth_headers())
    assert resp.status_code == 404
    assert resp.json()["detail"] == "run_not_found"

    resp = client.get("/v1/acquisition/artifacts/missing", headers=_auth_headers())
    assert resp.status_code == 404
    assert resp.json()["detail"] == "artifact_not_found"


def test_manual_downloads_endpoint(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    acq_run_id, source_id = _seed_manual_recovery_case()
    client = TestClient(app)

    resp = client.get(f"/v1/acquisition/runs/{acq_run_id}/manual-downloads", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["acq_run_id"] == acq_run_id
    assert body["total"] == 1
    item = body["items"][0]
    assert item["source_id"] == source_id
    assert item["status"] == "failed"
    # ordered: source_url, doi_url, selected_url(deduped away when equal to source_url)
    assert item["manual_url_candidates"] == ["https://ieee.org/test", "https://doi.org/10.1000/test-acq"]
    assert "legal_candidates" in item
    assert "reason_code" in item


def test_manual_downloads_csv_endpoint(monkeypatch):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    acq_run_id, _ = _seed_manual_recovery_case()
    client = TestClient(app)

    resp = client.get(f"/v1/acquisition/runs/{acq_run_id}/manual-downloads.csv", headers=_auth_headers())
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("content-type", "")
    body = resp.text
    assert "title,authors,year,journal,citations,ai_score,status,source_link" in body
    assert "UPW control in semiconductor lines" in body


def test_manual_upload_registration(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(main_module, "enqueue_acquisition_run", lambda acq_run_id: None)
    acq_run_id, source_id = _seed_manual_recovery_case()
    original_artifacts_dir = settings.artifacts_dir
    object.__setattr__(settings, "artifacts_dir", str(tmp_path))
    try:
        client = TestClient(app)
        encoded = base64.b64encode(b"%PDF-1.4 manual test").decode("utf-8")
        resp = client.post(
            f"/v1/acquisition/runs/{acq_run_id}/manual-upload",
            json={
                "source_id": source_id,
                "filename": "manual.pdf",
                "content_base64": encoded,
                "content_type": "application/pdf",
            },
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["acq_run_id"] == acq_run_id
        assert body["source_id"] == source_id
        assert body["kind"] == "pdf"
        assert (tmp_path / body["path"]).exists()

        status = client.get(f"/v1/acquisition/runs/{acq_run_id}", headers=_auth_headers())
        assert status.status_code == 200
        status_body = status.json()
        assert status_body["downloaded_total"] == 1
        assert status_body["failed_total"] == 0
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)


def test_manual_complete_endpoint_persists_item_state():
    acq_run_id, source_id = _seed_manual_recovery_case()
    client = TestClient(app)
    resp = client.post(
        f"/v1/acquisition/runs/{acq_run_id}/manual-complete",
        json={"source_id": source_id},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "skipped"
    assert body["reason_code"] == "manual_complete"

    items = client.get(f"/v1/acquisition/runs/{acq_run_id}/items", headers=_auth_headers())
    assert items.status_code == 200
    assert items.json()["items"][0]["status"] == "skipped"


def test_manual_upload_batch_auto_matches_by_doi(tmp_path: Path):
    acq_run_id, source_id = _seed_manual_recovery_case()
    original_artifacts_dir = settings.artifacts_dir
    object.__setattr__(settings, "artifacts_dir", str(tmp_path))
    try:
        client = TestClient(app)
        files = [
            ("files", ("matched.pdf", b"%PDF-1.4 DOI 10.1000/test-acq", "application/pdf")),
            ("files", ("unknown.pdf", b"%PDF-1.4 no match token", "application/pdf")),
        ]
        resp = client.post(
            f"/v1/acquisition/runs/{acq_run_id}/manual-upload-batch",
            files=files,
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["matched"] == 1
        assert body["unmatched"] == 1
        assert body["ambiguous"] == 0
        assert any(row["source_id"] == source_id and row["status"] == "matched" for row in body["items"])
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)


def test_manual_upload_batch_auto_matches_by_doi_even_when_item_already_downloaded(tmp_path: Path):
    acq_run_id, source_id = _seed_manual_recovery_case()
    original_artifacts_dir = settings.artifacts_dir
    object.__setattr__(settings, "artifacts_dir", str(tmp_path))
    try:
        with SessionLocal() as db:
            item = db.scalars(select(AcquisitionItem).where(AcquisitionItem.acq_run_id == acq_run_id)).first()
            assert item is not None
            item.status = "downloaded"
            item.last_error = None
            db.commit()

        client = TestClient(app)
        files = [
            ("files", ("matched_again.pdf", b"%PDF-1.4 DOI 10.1000/test-acq", "application/pdf")),
        ]
        resp = client.post(
            f"/v1/acquisition/runs/{acq_run_id}/manual-upload-batch",
            files=files,
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["matched"] == 1
        assert body["items"][0]["source_id"] == source_id
        assert body["items"][0]["reason"] == "doi_exact"
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)


def test_manual_upload_batch_auto_matches_by_pdf_text_title(monkeypatch, tmp_path: Path):
    acq_run_id, source_id = _seed_manual_recovery_case()
    original_artifacts_dir = settings.artifacts_dir
    object.__setattr__(settings, "artifacts_dir", str(tmp_path))
    try:
        class _FakePage:
            def extract_text(self):
                return "UPW control in semiconductor lines"

        class _FakeReader:
            def __init__(self, _stream):
                self.pages = [_FakePage()]

        monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=_FakeReader))

        client = TestClient(app)
        files = [
            ("files", ("scan_001.pdf", b"%PDF-1.4 not-using-filename", "application/pdf")),
        ]
        resp = client.post(
            f"/v1/acquisition/runs/{acq_run_id}/manual-upload-batch",
            files=files,
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["matched"] == 1
        assert body["unmatched"] == 0
        assert body["ambiguous"] == 0
        assert body["items"][0]["source_id"] == source_id
        assert body["items"][0]["reason"] == "pdf_title_similarity"
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)


def test_manual_upload_batch_auto_matches_by_ai_extracted_title(monkeypatch, tmp_path: Path):
    acq_run_id, source_id = _seed_manual_recovery_case()
    original_artifacts_dir = settings.artifacts_dir
    object.__setattr__(settings, "artifacts_dir", str(tmp_path))
    try:
        import knowledge_miner.routes.acquisition as acquisition_route
        from knowledge_miner.ai_filter import AIDocumentIdentityResult

        monkeypatch.setattr(acquisition_route, "_extract_upload_text", lambda *_args, **_kwargs: "scanned paper")
        monkeypatch.setattr(acquisition_route, "_extract_probable_pdf_title", lambda _text: None)
        monkeypatch.setattr(
            acquisition_route,
            "extract_document_identity",
            lambda **_kwargs: AIDocumentIdentityResult(
                title="UPW control in semiconductor lines",
                doi=None,
                confidence=0.93,
            ),
        )

        client = TestClient(app)
        files = [
            ("files", ("scan_002.pdf", b"%PDF-1.4 image-like", "application/pdf")),
        ]
        resp = client.post(
            f"/v1/acquisition/runs/{acq_run_id}/manual-upload-batch",
            files=files,
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["matched"] == 1
        assert body["items"][0]["source_id"] == source_id
        assert body["items"][0]["reason"] == "ai_title_similarity"
    finally:
        object.__setattr__(settings, "artifacts_dir", original_artifacts_dir)
