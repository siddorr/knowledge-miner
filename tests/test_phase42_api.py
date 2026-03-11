from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.main import app
from knowledge_miner.models import AcquisitionItem, AcquisitionRun, ParseRun, ParsedDocument, Run, Source


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer dev-token"}


def _seed_phase42_records() -> None:
    with SessionLocal() as db:
        run = Run(
            id="run_q_1",
            status="completed",
            seed_queries=["upw"],
            max_iterations=1,
            current_iteration=1,
            accepted_total=1,
            expanded_candidates_total=0,
            citation_edges_total=0,
            ai_filter_active=False,
            ai_filter_warning="AI filter disabled (USE_AI_FILTER=false); heuristic filtering only.",
        )
        db.add(run)
        source = Source(
            id="src_q_1",
            run_id=run.id,
            title="Queue source needs review",
            year=2024,
            url="https://example.org/source-q",
            doi="10.1000/q1",
            abstract="UPW source abstract",
            type="academic",
            source="openalex",
            source_native_id="WQ1",
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
        db.add(source)

        acq_run = AcquisitionRun(
            id="acq_q_1",
            discovery_run_id=run.id,
            retry_failed_only=False,
            status="completed",
            total_sources=1,
            downloaded_total=0,
            partial_total=0,
            failed_total=1,
            skipped_total=0,
        )
        db.add(acq_run)
        db.add(
            AcquisitionItem(
                id="acq_item_q_1",
                acq_run_id=acq_run.id,
                source_id=source.id,
                status="failed",
                attempt_count=2,
                selected_url="https://example.org/source-q",
                selected_url_source="publisher",
                resolution_attempts=[],
                reason_code="paywalled",
                last_error="http_403",
            )
        )

        parse_run = ParseRun(
            id="parse_q_1",
            acq_run_id=acq_run.id,
            retry_failed_only=False,
            ai_filter_active=False,
            ai_filter_warning="AI filter disabled",
            status="completed",
            total_documents=1,
            parsed_total=0,
            failed_total=1,
            chunked_total=0,
        )
        db.add(parse_run)
        db.add(
            ParsedDocument(
                id="doc_q_1",
                parse_run_id=parse_run.id,
                source_id=source.id,
                artifact_id="artifact_missing",
                status="failed",
                title="Parsed doc failed",
                publication_year=2024,
                language=None,
                parser_used=None,
                body_text=None,
                char_count=0,
                section_count=0,
                content_hash=None,
                relevance_score=None,
                decision=None,
                confidence=None,
                reason=None,
                last_error="artifact_not_found",
            )
        )
        db.commit()


def test_work_queue_endpoint_returns_actionable_rows():
    _seed_phase42_records()
    client = TestClient(app)
    resp = client.get("/v1/work-queue", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 3
    phases = {item["phase"] for item in body["items"]}
    assert "discovery" in phases
    assert "acquisition" in phases
    assert "parse" in phases


def test_global_search_endpoint_returns_typed_results():
    _seed_phase42_records()
    client = TestClient(app)
    resp = client.get("/v1/search/global?q=queue&limit=20", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "queue"
    assert body["total"] >= 1
    assert body["items"][0]["result_type"] in {"run", "source", "acquisition_item", "parsed_document", "chunk"}


def test_system_status_endpoint_returns_operator_summary():
    _seed_phase42_records()
    client = TestClient(app)
    resp = client.get("/v1/system/status", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert "auth_enabled" in body
    assert "auth_mode" in body
    assert "ai_filter_active" in body
    assert "provider_readiness" in body
    assert "db_ready" in body
    assert "db_missing_tables" in body
    assert "database_target" in body
    assert "db_target_url" in body
    assert "db_target_resolved_path" in body
    assert "db_schema_ready" in body
    assert "process_pid" in body


def test_system_status_reports_schema_not_ready_when_tables_missing():
    Base.metadata.drop_all(bind=engine)
    client = TestClient(app)
    resp = client.get("/v1/system/status", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["db_ready"] is False
    assert "runs" in body["db_missing_tables"]


def test_debug_db_context_is_disabled_by_default():
    client = TestClient(app)
    resp = client.get("/v1/debug/db-context", headers=_auth_headers())
    assert resp.status_code == 404
