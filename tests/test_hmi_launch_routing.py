from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.main import app
from knowledge_miner.models import AcquisitionItem, AcquisitionRun, Run, Source


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _create_run(run_id: str) -> Run:
    return Run(
        id=run_id,
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


def test_hmi_launch_defaults_to_build_when_no_topic_data():
    client = TestClient(app)
    response = client.get("/hmi")
    assert response.status_code == 200
    assert 'window.__KM_HMI_LAUNCH_SECTION__ = "build";' in response.text


def test_hmi_launch_prefers_review_when_review_queue_exists():
    with SessionLocal() as db:
        run = _create_run("run_launch_review")
        db.add(run)
        db.add(
            Source(
                id="src_launch_review",
                run_id=run.id,
                title="Needs review",
                year=2024,
                url="https://example.org/review",
                doi=None,
                abstract="needs review",
                type="academic",
                source="openalex",
                source_native_id="review1",
                patent_office=None,
                patent_number=None,
                iteration=1,
                discovery_method="seed_search",
                relevance_score=3.0,
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
    response = client.get("/hmi")
    assert response.status_code == 200
    assert 'window.__KM_HMI_LAUNCH_SECTION__ = "review";' in response.text


def test_hmi_launch_uses_documents_when_failures_exist_without_review_queue():
    with SessionLocal() as db:
        run = _create_run("run_launch_docs")
        db.add(run)
        db.add(
            Source(
                id="src_launch_docs",
                run_id=run.id,
                title="Doc issue",
                year=2024,
                url="https://example.org/docs",
                doi=None,
                abstract="doc fail",
                type="academic",
                source="openalex",
                source_native_id="docs1",
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
        acq = AcquisitionRun(
            id="acq_launch_docs",
            discovery_run_id=run.id,
            retry_failed_only=False,
            status="completed",
            total_sources=1,
            downloaded_total=0,
            partial_total=0,
            failed_total=1,
            skipped_total=0,
        )
        db.add(acq)
        db.add(
            AcquisitionItem(
                id="acq_item_launch_docs",
                acq_run_id=acq.id,
                source_id="src_launch_docs",
                status="failed",
                attempt_count=1,
                selected_url="https://example.org/docs",
                selected_url_source="publisher",
                resolution_attempts=[],
                reason_code="source_error",
                last_error="http_500",
            )
        )
        db.commit()

    client = TestClient(app)
    response = client.get("/hmi")
    assert response.status_code == 200
    assert 'window.__KM_HMI_LAUNCH_SECTION__ = "documents";' in response.text


def test_hmi_launch_falls_back_to_build_when_no_review_or_doc_failures():
    with SessionLocal() as db:
        db.add(_create_run("run_launch_build"))
        db.commit()

    client = TestClient(app)
    response = client.get("/hmi")
    assert response.status_code == 200
    assert 'window.__KM_HMI_LAUNCH_SECTION__ = "build";' in response.text
