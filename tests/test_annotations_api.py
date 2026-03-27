from __future__ import annotations

from fastapi.testclient import TestClient

from knowledge_miner.db import Base, SessionLocal, engine
from knowledge_miner.main import app
from knowledge_miner.models import AcquisitionRun, Artifact, ParseRun, ParsedDocument, Run, SessionProfile, Source


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer dev-token"}


def _seed_session_source(parsed: bool = True) -> tuple[str, str]:
    with SessionLocal() as db:
        db.add(SessionProfile(session_id="session_ann", name="Annotation Session", session_context="Semiconductor wastewater context."))
        run = Run(
            id="run_ann",
            session_id="session_ann",
            status="completed",
            seed_queries=["semiconductor wastewater"],
            max_iterations=1,
            current_iteration=1,
            accepted_total=1,
            expanded_candidates_total=0,
            citation_edges_total=0,
            ai_filter_active=False,
            ai_filter_warning=None,
        )
        source = Source(
            id="src_ann",
            run_id=run.id,
            title="Wastewater treatment and recycle from a semiconductor industry",
            year=2024,
            url="https://example.org/paper",
            doi="10.1000/ann",
            abstract="Pilot-scale semiconductor wastewater treatment study.",
            journal="Water Research",
            authors=["A. Author"],
            citation_count=11,
            type="academic",
            source="openalex",
            source_native_id="oa_ann",
            patent_office=None,
            patent_number=None,
            iteration=1,
            discovery_method="seed_search",
            relevance_score=8.4,
            accepted=True,
            review_status="human_accept",
            final_decision="human_accept",
            decision_source="human_review",
            heuristic_recommendation="accept",
            heuristic_score=8.0,
            ai_decision=None,
            ai_confidence=None,
            parent_source_id=None,
            provenance_history=[],
        )
        db.add_all([run, source])
        if parsed:
            acq = AcquisitionRun(
                id="acq_ann",
                discovery_run_id=run.id,
                retry_failed_only=False,
                status="completed",
                total_sources=1,
                downloaded_total=1,
                partial_total=0,
                failed_total=0,
                skipped_total=0,
                error_message=None,
            )
            parse_run = ParseRun(
                id="parse_ann",
                acq_run_id=acq.id,
                retry_failed_only=False,
                ai_filter_active=False,
                ai_filter_warning=None,
                status="completed",
                total_documents=1,
                parsed_total=1,
                failed_total=0,
                chunked_total=1,
                error_message=None,
            )
            artifact = Artifact(
                id="artifact_ann",
                acq_run_id=acq.id,
                source_id=source.id,
                item_id=None,
                kind="pdf",
                path="paper.pdf",
                checksum_sha256="abc",
                size_bytes=100,
                mime_type="application/pdf",
            )
            parsed_doc = ParsedDocument(
                id="parsed_doc_ann",
                parse_run_id=parse_run.id,
                source_id=source.id,
                artifact_id=artifact.id,
                status="parsed",
                title=source.title,
                publication_year=source.year,
                language="en",
                abstract=source.abstract,
                body_text="Full parsed text for annotation summary generation.",
                parser_used="test",
                relevance_score=8.0,
                decision="auto_accept",
                confidence=0.9,
                reason="test",
                char_count=40,
                section_count=1,
                content_hash="hash_ann",
                last_error=None,
            )
            db.add_all([acq, parse_run, artifact, parsed_doc])
        db.commit()
    return "session_ann", "src_ann"


def test_annotations_list_returns_virtual_state_for_requested_source():
    session_id, source_id = _seed_session_source(parsed=True)
    client = TestClient(app)

    response = client.get(
        f"/v1/sessions/{session_id}/annotations",
        params=[("source_id", source_id)],
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["source_id"] == source_id
    assert item["freeform_tags"] == []
    assert item["approved_tags"] == []
    assert item["ai_suggested_tags"] == []
    assert item["can_generate_summary"] is True
    assert item["can_generate_tags"] is True
    assert item["summary_status"] == "none"
    assert item["tag_suggestion_status"] == "none"


def test_tag_catalog_and_annotation_update_round_trip():
    session_id, source_id = _seed_session_source(parsed=False)
    client = TestClient(app)

    catalog = client.put(
        f"/v1/sessions/{session_id}/tag-catalog",
        json={"tags": ["AOP", "RO"]},
        headers=_auth_headers(),
    )
    assert catalog.status_code == 200
    assert catalog.json()["tags"] == ["AOP", "RO"]

    updated = client.put(
        f"/v1/sessions/{session_id}/annotations/{source_id}",
        json={"freeform_tags": ["pilot scale", "AOP"], "approved_tags": ["AOP"]},
        headers=_auth_headers(),
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["freeform_tags"] == ["pilot scale", "AOP"]
    assert body["approved_tags"] == ["AOP"]
    assert body["can_generate_summary"] is False
    assert body["summary_block_reason"] == "parsed_text_required"


def test_generate_summary_persists_completed_annotation(monkeypatch):
    session_id, source_id = _seed_session_source(parsed=True)
    client = TestClient(app)
    artifact_json = {
        "summary": "Session-specific summary text.",
        "wastewater_source": {
            "fab_area": None,
            "process_step": None,
            "tool_or_equipment": None,
            "waste_stream_name": None,
            "real_or_synthetic_water": "unclear",
            "water_source_details": None,
        },
        "water_composition": {
            "components": [],
            "water_quality_parameters": [],
        },
        "treatment_target": {
            "target_contaminants_or_parameters": [],
        },
        "treatment_technology": {
            "technology_name": "reverse osmosis",
            "technology_category": "membrane",
        },
        "experiments": {
            "used_real_wastewater": None,
            "used_synthetic_wastewater": None,
            "experimental_scale": "pilot",
        },
        "performance": {
            "removal_results": [],
            "key_findings": [],
            "limitations": [],
        },
    }

    monkeypatch.setattr(
        "knowledge_miner.routes.annotations.generate_paper_summary",
        lambda **_: type(
            "SummaryResult",
            (),
            {"summary": "Session-specific summary text.", "artifact_json": artifact_json},
        )(),
    )

    response = client.post(
        f"/v1/sessions/{session_id}/summaries/generate",
        json={"source_ids": [source_id]},
        headers=_auth_headers(),
    )
    assert response.status_code == 202
    assert response.json()["queued_count"] == 1

    listed = client.get(
        f"/v1/sessions/{session_id}/annotations",
        params=[("source_id", source_id)],
        headers=_auth_headers(),
    )
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["summary_status"] == "completed"
    assert item["ai_summary"] == "Session-specific summary text."
    assert item["ai_summary_json"]["summary"] == "Session-specific summary text."
    assert item["ai_summary_json"]["treatment_technology"]["technology_name"] == "reverse osmosis"


def test_generate_tags_persists_completed_annotation(monkeypatch):
    session_id, source_id = _seed_session_source(parsed=True)
    client = TestClient(app)

    monkeypatch.setattr(
        "knowledge_miner.routes.annotations.generate_paper_tags",
        lambda **_: type("TagResult", (), {"tags": ["fluoride removal", "semiconductor wastewater", "reverse osmosis"]})(),
    )

    response = client.post(
        f"/v1/sessions/{session_id}/tags/generate",
        json={"source_ids": [source_id]},
        headers=_auth_headers(),
    )
    assert response.status_code == 202
    assert response.json()["queued_count"] == 1

    listed = client.get(
        f"/v1/sessions/{session_id}/annotations",
        params=[("source_id", source_id)],
        headers=_auth_headers(),
    )
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["tag_suggestion_status"] == "completed"
    assert item["ai_suggested_tags"] == ["fluoride removal", "semiconductor wastewater", "reverse osmosis"]


def test_promote_suggested_tag_to_freeform_and_approved(monkeypatch):
    session_id, source_id = _seed_session_source(parsed=True)
    client = TestClient(app)

    client.put(
        f"/v1/sessions/{session_id}/tag-catalog",
        json={"tags": ["RO", "fluoride removal"]},
        headers=_auth_headers(),
    )
    monkeypatch.setattr(
        "knowledge_miner.routes.annotations.generate_paper_tags",
        lambda **_: type("TagResult", (), {"tags": ["fluoride removal", "semiconductor wastewater"]})(),
    )
    queued = client.post(
        f"/v1/sessions/{session_id}/tags/generate",
        json={"source_ids": [source_id]},
        headers=_auth_headers(),
    )
    assert queued.status_code == 202

    freeform = client.post(
        f"/v1/sessions/{session_id}/annotations/{source_id}/suggested-tags/promote",
        json={"tag": "semiconductor wastewater", "target": "freeform"},
        headers=_auth_headers(),
    )
    assert freeform.status_code == 200
    assert freeform.json()["freeform_tags"] == ["semiconductor wastewater"]

    approved = client.post(
        f"/v1/sessions/{session_id}/annotations/{source_id}/suggested-tags/promote",
        json={"tag": "fluoride removal", "target": "approved"},
        headers=_auth_headers(),
    )
    assert approved.status_code == 200
    assert approved.json()["approved_tags"] == ["fluoride removal"]
